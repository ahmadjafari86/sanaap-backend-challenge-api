"""Run disposable, real-service integration checks without touching the working stack."""

import json
from hashlib import sha256
from pathlib import Path
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4
from xml.etree import ElementTree
import zlib

if __package__:
    from .proxy_checks import check_forwarded_links, check_proxy_http, check_upload_limits
    from .websocket_checks import SocketChecks
else:
    from proxy_checks import check_forwarded_links, check_proxy_http, check_upload_limits
    from websocket_checks import SocketChecks

ROOT = Path(__file__).resolve().parents[1]


def is_isolated_config(config, project):
    if not re.fullmatch(r"sanaap-t09-[0-9a-f]{10}", project) or config.get("name") != project:
        return False
    if set(config.get("services", {})) != {"backend", "worker", "nginx", "postgres", "redis", "minio"}:
        return False
    if config["services"]["backend"].get("ports") or config["services"]["worker"].get("ports"):
        return False
    for service in ("backend", "worker"):
        if not any(mount.get("source") == "staging_data" and mount.get("target") == "/app/.staging"
                   and not mount.get("read_only") for mount in config["services"][service].get("volumes", [])):
            return False
    proxy_mounts = config["services"]["nginx"].get("volumes", [])
    if not any(mount.get("source") == "static_data" and mount.get("target") == "/srv/static"
               and mount.get("read_only") for mount in proxy_mounts):
        return False
    for category in ("volumes", "networks"):
        if not config.get(category):
            return False
        for resource in config[category].values():
            if resource.get("external") or not resource.get("name", "").startswith(f"{project}_"):
                return False
    for service in config["services"].values():
        if service.get("container_name") or service.get("network_mode") or service.get("privileged"):
            return False
        for port in service.get("ports", []):
            if port.get("host_ip") != "127.0.0.1":
                return False
        for mount in service.get("volumes", []):
            if mount.get("type") != "volume" or mount.get("source") not in config["volumes"]:
                return False
    return True


def png_bytes(color):
    def chunk(name, data):
        return struct.pack("!I", len(data)) + name + data + struct.pack("!I", zlib.crc32(name + data))

    rows = (b"\0" + bytes(color) * 2) * 2
    return b"\x89PNG\r\n\x1a\n" + b"".join([
        chunk(b"IHDR", struct.pack("!IIBBBBB", 2, 2, 8, 2, 0, 0, 0)),
        chunk(b"IDAT", zlib.compress(rows)), chunk(b"IEND", b""),
    ])


class HTTPChecks:
    def __init__(self, port):
        self.origin = f"http://127.0.0.1:{port}"
        self.opener = build_opener(ProxyHandler({}))

    def http(self, method, url, *, body=None, headers=None, expected=200, return_headers=False):
        request = Request(url, data=body, headers=headers or {}, method=method)
        try:
            with self.opener.open(request, timeout=60) as response:
                code, payload = response.status, response.read()
                received_headers = dict((key.lower(), value) for key, value in response.headers.items())
        except HTTPError as error:
            code, payload = error.code, error.read()
            received_headers = dict((key.lower(), value) for key, value in error.headers.items())
        except URLError as error:
            raise RuntimeError(f"HTTP transport failed ({type(error.reason).__name__}).") from None
        accepted = (expected,) if isinstance(expected, int) else expected
        if code not in accepted:
            raise RuntimeError(f"{method} request expected HTTP {expected}, received {code}.")
        return (payload, received_headers) if return_headers else payload

    def api(self, method, path, *, token=None, data=None, image=None, expected=200, headers=None):
        headers = {**({"Authorization": f"Bearer {token}"} if token else {}), **(headers or {})}
        body = None
        if image is not None:
            boundary = f"sanaap-{uuid4().hex}"
            parts = []
            for name, value in (data or {}).items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
            parts.extend([
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.png"\r\nContent-Type: image/png\r\n\r\n'.encode(),
                image, f"\r\n--{boundary}--\r\n".encode(),
            ])
            body = b"".join(parts)
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        raw = self.http(method, self.origin + path, body=body, headers=headers, expected=expected)
        return json.loads(raw) if raw else None

    def wait_document(self, document, token, state="READY", timeout=60):
        deadline = time.monotonic() + timeout
        while True:
            current = self.api("GET", f"/api/v1/documents/{document['id']}/", token=token)
            if current["status"] == state:
                return current
            if current["status"] == "FAILED" or time.monotonic() >= deadline:
                raise RuntimeError(f"Document did not reach expected {state} state (observed {current['status']}).")
            time.sleep(0.2)


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def main():
    project = f"sanaap-t09-{uuid4().hex[:10]}"
    directory = Path(tempfile.mkdtemp(prefix="sanaap-integration-"))
    env_file = directory / "stack.env"
    api_port, minio_port = free_port(), free_port()
    while api_port == minio_port:
        minio_port = free_port()
    password = secrets.token_urlsafe(32)
    environment = {
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(48), "DJANGO_DEBUG": "False",
        "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost", "POSTGRES_DB": "sanaap_integration",
        "POSTGRES_USER": "sanaap_integration", "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "MINIO_ROOT_USER": "integration-" + secrets.token_hex(6), "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(32),
        "MINIO_BUCKET_NAME": "integration-documents", "INTEGRATION_ENV_FILE": str(env_file),
        "INTEGRATION_API_PORT": str(api_port), "INTEGRATION_MINIO_PORT": str(minio_port),
    }
    hidden = [password, environment["DJANGO_SECRET_KEY"], environment["POSTGRES_PASSWORD"],
              environment["MINIO_ROOT_USER"], environment["MINIO_ROOT_PASSWORD"]]

    def redact(text):
        for value in hidden:
            text = text.replace(value, "<redacted>")
        return text

    def run(arguments, *, input_text=None, timeout=300):
        result = subprocess.run(arguments, cwd=ROOT, input=input_text, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"Command failed (exit {result.returncode}):\n{redact(result.stdout[-4000:])}")
        return result.stdout

    def write_environment():
        env_file.write_text("".join(f"{name}={value}\n" for name, value in environment.items()))
        env_file.chmod(0o600)

    base = ["docker", "compose", "--project-directory", str(ROOT), "--env-file", str(env_file), "-f", str(ROOT / "compose.yaml")]
    compose = base[:2] + ["--project-name", project] + base[2:] + ["-f", str(ROOT / "compose.integration.yaml")]
    active = False
    before_volumes = set()
    clean = False
    sockets = None
    try:
        write_environment()
        source_project = json.loads(run(base + ["config", "--format", "json"]))["name"]
        environment.update(INTEGRATION_BACKEND_IMAGE=f"{source_project}-backend",
                           INTEGRATION_MINIO_IMAGE=f"{source_project}-minio",
                           INTEGRATION_NGINX_IMAGE=f"{source_project}-nginx")
        write_environment()
        config = json.loads(run(compose + ["config", "--format", "json"]))
        if not is_isolated_config(config, project):
            raise RuntimeError("Refusing a Compose configuration that is not isolated.")
        before_volumes = set(run(["docker", "volume", "ls", "--format", "{{.Name}}"]).splitlines())
        print(f"Integration project: {project}; credentials stay in a private temporary directory.", flush=True)
        print("Building current backend/MinIO/Nginx images...", flush=True)
        run(base + ["build", "backend", "minio", "nginx"], timeout=600)
        active = True
        print("Starting a clean, isolated stack...", flush=True)
        try:
            run(compose + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "180"])
        except RuntimeError as error:
            diagnostic = run(compose + ["logs", "--no-color", "--tail", "50", "worker"])
            raise RuntimeError(f"{error}\nWorker startup diagnostics:\n{redact(diagnostic)}") from None
        run(compose + ["exec", "-T", "backend", "python", "manage.py", "check"])
        run(compose + ["exec", "-T", "backend", "python", "manage.py", "makemigrations", "--check", "--dry-run"])
        run(compose + ["exec", "-T", "backend", "python", "manage.py", "migrate", "--check"])
        run(compose + ["exec", "-T", "nginx", "nginx", "-t"])
        assert run(compose + ["exec", "-T", "nginx", "id", "-u"]).strip() != "0"
        print("Fresh startup, migrations and private bucket initialization passed.", flush=True)
        regression = run(compose + ["run", "--rm", "--no-deps", "backend", "python", "manage.py", "test", "--noinput"])
        count = re.search(r"Ran (\d+) tests?", regression)
        print(f"Regression suite: {count.group(1) if count else 'all'} tests passed.", flush=True)
        run(compose + ["exec", "-T", "backend", "python", "manage.py", "spectacular", "--validate", "--fail-on-warn", "--file", "/tmp/openapi.yaml"])

        def shell(code, value=None):
            return run(compose + ["exec", "-T", "backend", "python", "manage.py", "shell", "-c", code],
                       input_text=json.dumps(value) if value is not None else None)

        shell(
            "import json, sys; from django.contrib.auth import get_user_model; from apps.documents.models import Document, DocumentAuditEvent; "
            "User = get_user_model(); data = json.load(sys.stdin); "
            "assert User.objects.count() == 0 and Document.objects.count() == 0 and DocumentAuditEvent.objects.count() == 0; "
            "user = User.objects.create_user(username='integration-admin', password=data['password'], role='admin'); "
            "assert not user.is_staff and not user.is_superuser",
            {"password": password},
        )
        client = HTTPChecks(api_port)
        css = check_proxy_http(client)
        expected_css_hash = shell(
            "from pathlib import Path; from hashlib import sha256; import django.contrib.admin; "
            "print(sha256((Path(django.contrib.admin.__file__).parent / 'static/admin/css/base.css').read_bytes()).hexdigest())"
        ).splitlines()[-1]
        assert sha256(css).hexdigest() == expected_css_hash
        print("Non-root Nginx, static bytes/MIME types, Swagger, host validation and JWT error headers passed.", flush=True)
        client.api("GET", "/api/schema/?format=json")
        client.api("GET", "/api/v1/documents/", expected=401)
        client.api("GET", "/api/v1/documents/", token="invalid-token", expected=401)
        admin = client.api("POST", "/api/v1/auth/token/", data={"username": "integration-admin", "password": password})
        hidden.extend(admin.values())
        tokens = {"admin": admin}
        for role in ("editor", "viewer"):
            client.api("POST", "/api/v1/users/", token=admin["access"], expected=201,
                       data={"username": f"integration-{role}", "password": password, "role": role})
            tokens[role] = client.api("POST", "/api/v1/auth/token/", data={"username": f"integration-{role}", "password": password})
            hidden.extend(tokens[role].values())

        original, replacement = png_bytes((255, 0, 0)), png_bytes((0, 0, 255))
        editor, viewer = tokens["editor"]["access"], tokens["viewer"]["access"]
        sockets = SocketChecks(compose, client.origin)
        for role, pair in tokens.items():
            sockets.open(role, pair["access"])
        assert sockets.command("open", name="unauthenticated", origin=client.origin) == {"connected": True}
        for origin in (None, "http://attacker.invalid"):
            assert sockets.command("open", name="denied", origin=origin) == {"http_status": 403}
        assert sockets.command("open", name="query", origin=client.origin, path="/ws/documents/?access=do-not-log-this-token") == {"http_status": 400}
        for name, token in (("invalid", "invalid-private-token"), ("refresh", tokens["viewer"]["refresh"])):
            assert sockets.command("open", name=name, origin=client.origin) == {"connected": True}
            assert sockets.command("authenticate", name=name, access=token) == {"closed": 4401}
        assert sockets.command("ping", name="viewer") == {"message": {"type": "pong"}}
        # Longer than Channels Redis's five-second blocking read, with no events.
        time.sleep(6)
        for role in tokens:
            assert sockets.command("ping", name=role) == {"message": {"type": "pong"}}
        print("Live Nginx WebSocket upgrade, all JWT roles, Origin/query/refresh rejection and ping passed.", flush=True)
        print("Stopping only disposable worker to check queued acceptance and restart recovery...", flush=True)
        run(compose + ["stop", "--timeout", "3", "worker"])
        document = client.api("POST", "/api/v1/documents/", token=editor, data={"title": "Integration image"}, image=original, expected=202)
        detail = f"/api/v1/documents/{document['id']}/"
        assert document["status"] == "PENDING"
        for pair in tokens.values():
            assert client.api("GET", detail, token=pair["access"])["status"] == "PENDING"
            client.api("GET", detail + "download/", token=pair["access"], expected=409)
        client.api("PATCH", detail, token=editor, data={"title": "Must remain unchanged"}, expected=409)
        run(compose + ["up", "-d", "--no-deps", "--wait", "worker"])
        document = client.wait_document(document, editor)
        for role in tokens:
            sockets.event(role, "document.ready", document["id"])
        assert sockets.command("receive", name="unauthenticated", timeout=6) == {"closed": 4401}
        assert sockets.command("receive", name="viewer", timeout=.1) == {"quiet": True}
        print("PENDING 202/readability/409 gates and queued job recovery after worker restart passed.", flush=True)
        assert document["content_type"] == "image/png" and document["file_size"] == len(original)
        first_url = None
        expiry_deadline = None
        for role, pair in tokens.items():
            metadata = client.api("GET", detail, token=pair["access"])
            assert metadata == document
            signed = client.api("GET", detail + "download/", token=pair["access"])
            parsed = urlsplit(signed["download_url"])
            assert parsed.netloc == f"127.0.0.1:{minio_port}" and parsed.scheme == "http"
            assert signed["expires_in"] == 60 and parse_qs(parsed.query)["X-Amz-Expires"] == ["60"]
            assert client.http("GET", signed["download_url"]) == original
            unsigned = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
            client.http("GET", unsigned, expected=403)
            if first_url is None:
                first_url, expiry_deadline = signed["download_url"], time.monotonic() + 62
        client.api("POST", "/api/v1/documents/", token=viewer, data={"title": "Denied"}, image=original, expected=403)
        client.api("DELETE", detail, token=editor, expected=403)
        client.http("HEAD", client.origin + detail + "download/", headers={"Authorization": f"Bearer {viewer}"}, expected=405)
        page = client.api("GET", f"/api/v1/documents/?search=INTEGRATION&creator={document['creator']}&page_size=1", token=viewer)
        assert page["count"] == 1 and page["results"][0]["id"] == document["id"]
        print("Real JWT roles, shared upload/download, exact bytes, private bucket and query contract passed.", flush=True)

        shell("from django.core.cache import cache; from apps.documents.models import Document; "
              "from apps.documents.caching import _metadata_key; "
              f"doc = Document.objects.get(pk={document['id']}); "
              "assert cache.get(_metadata_key(doc.pk, doc.metadata_version))['title'] == 'Integration image'")
        print("Stopping only the disposable Redis to verify real outage fallback...", flush=True)
        run(compose + ["stop", "--timeout", "3", "redis"])
        for role in tokens:
            assert sockets.command("receive", name=role) == {"closed": 1013}
        assert client.api("GET", detail, token=viewer)["title"] == "Integration image"
        client.api("PATCH", detail, token=editor, data={"title": "Redis outage update"})
        assert client.api("GET", detail, token=viewer)["title"] == "Redis outage update"
        broker_failed = client.api("POST", "/api/v1/documents/", token=editor, data={"title": "Broker outage"}, image=original, expected=202)
        client.wait_document(broker_failed, editor, state="FAILED")
        broker_detail = f"/api/v1/documents/{broker_failed['id']}/"
        client.api("GET", broker_detail + "download/", token=viewer, expected=409)
        client.api("DELETE", broker_detail, token=admin["access"], expected=204)
        run(compose + ["up", "-d", "--no-deps", "--wait", "redis"])
        assert client.api("GET", detail, token=viewer)["title"] == "Redis outage update"
        for role, pair in tokens.items():
            sockets.command("close", name=role)
            sockets.open(role, pair["access"])
        print("Redis outage and recovery passed without stale metadata or failed writes.", flush=True)

        second = client.api("POST", "/api/v1/documents/", token=editor, data={"title": "Replacement image"}, image=original, expected=202)
        second = client.wait_document(second, editor)
        for role in tokens:
            sockets.event(role, "document.ready", second["id"])
        check_forwarded_links(client, viewer)
        boundary_id = check_upload_limits(client, image=original, editor=editor, admin=admin["access"])
        for role in tokens:
            sockets.event(role, "document.ready", boundary_id)
        print("Public Host/port and forwarded-header spoof checks, 10 MiB transfer, API400 and proxy413 passed.", flush=True)
        second_detail = f"/api/v1/documents/{second['id']}/"
        old_url = client.api("GET", second_detail + "download/", token=viewer)["download_url"]
        updated = client.api("PATCH", second_detail, token=editor, image=replacement)
        for role in tokens:
            sockets.event(role, "document.updated", second["id"])
        new_url = client.api("GET", second_detail + "download/", token=viewer)["download_url"]
        assert updated["creator"] == second["creator"] and client.http("GET", new_url) == replacement
        second_key = unquote(urlsplit(new_url).path).split("/", 2)[2]
        old_key = unquote(urlsplit(old_url).path).split("/", 2)[2]
        shell("from django.core.files.storage import default_storage; "
              f"assert not default_storage.exists({old_key!r}) and default_storage.exists({second_key!r})")
        orphan = client.api("POST", "/api/v1/documents/", token=editor, data={"title": "Cleanup outage"}, image=original, expected=202)
        orphan = client.wait_document(orphan, editor)
        for role in tokens:
            sockets.event(role, "document.ready", orphan["id"])
        orphan_detail = f"/api/v1/documents/{orphan['id']}/"
        orphan_url = client.api("GET", orphan_detail + "download/", token=viewer)["download_url"]
        orphan_key = unquote(urlsplit(orphan_url).path).split("/", 2)[2]

        print("Stopping only disposable MinIO to verify storage failure handling...", flush=True)
        run(compose + ["stop", "--timeout", "3", "minio"])
        failed = client.api("POST", "/api/v1/documents/", token=editor, data={"title": "Failed upload"}, image=original, expected=202)
        client.wait_document(failed, editor, state="FAILED")
        client.api("PATCH", second_detail, token=editor, image=original, expected=503)
        assert client.api("GET", second_detail, token=viewer) == updated
        client.api("DELETE", orphan_detail, token=admin["access"], expected=204)
        client.api("GET", orphan_detail, token=viewer, expected=404)
        for role in tokens:
            assert sockets.command("receive", name=role, timeout=.1) == {"quiet": True}
        run(compose + ["up", "-d", "--no-deps", "--wait", "minio"])
        assert client.http("GET", client.api("GET", second_detail + "download/", token=viewer)["download_url"]) == replacement
        failed_page = client.api("GET", "/api/v1/documents/?search=Failed%20upload", token=viewer)
        assert failed_page["count"] == 1 and failed_page["results"][0]["status"] == "FAILED"
        client.api("DELETE", f"/api/v1/documents/{failed['id']}/", token=admin["access"], expected=204)
        shell("from django.core.files.storage import default_storage; "
              f"assert default_storage.exists({orphan_key!r})")
        print("Storage outages, rollback, recovery, replacement cleanup and documented orphan behavior passed.", flush=True)

        client.api("POST", "/api/v1/auth/logout/", data={"refresh": tokens["viewer"]["refresh"]})
        client.api("POST", "/api/v1/auth/token/refresh/", data={"refresh": tokens["viewer"]["refresh"]}, expected=401)
        client.api("GET", detail, token=viewer)
        remaining = max(0, expiry_deadline - time.monotonic())
        print(f"Checking actual signed-URL expiration (waiting at most {remaining:.0f} seconds)...", flush=True)
        while time.monotonic() < expiry_deadline:
            time.sleep(1)
        expired = client.http("GET", first_url, expected=403)
        assert "expired" in ElementTree.fromstring(expired).findtext("Message", "").lower()
        first_key = unquote(urlsplit(first_url).path).split("/", 2)[2]
        shell("from django.core.files.storage import default_storage; "
              f"assert default_storage.exists({first_key!r})")
        client.api("DELETE", detail, token=admin["access"], expected=204)
        client.api("DELETE", second_detail, token=admin["access"], expected=204)
        shell("from django.core.files.storage import default_storage; "
              f"assert not default_storage.exists({first_key!r}) and not default_storage.exists({second_key!r}); "
              f"default_storage.delete({orphan_key!r})")
        print("Actual URL expiry, logout semantics and successful document/object deletion passed.", flush=True)
        for role in tokens:
            assert sockets.command("receive", name=role, timeout=.1) == {"quiet": True}
        sockets.close()
        sockets = None
        print("Real worker READY/replacement notifications, no FAILED/delete messages, Redis closure/reconnect and cleanup passed.", flush=True)
        print("Stopping only disposable backend to check gateway errors/static availability and recreation recovery...", flush=True)
        run(compose + ["stop", "--timeout", "3", "backend"])
        client.http("GET", client.origin + "/api/docs/", expected=(502, 504))
        assert client.http("GET", client.origin + "/static/admin/css/base.css") == css
        run(compose + ["up", "-d", "--force-recreate", "--no-deps", "--wait", "backend"])
        deadline = time.monotonic() + 30
        while True:
            try:
                client.http("GET", client.origin + "/api/docs/")
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        print("Backend gateway error and recreation recovery passed without restarting Nginx.", flush=True)
        expected_events = [
            ["integration-editor", "create", row["id"], "success", 202]
            for row in (document, second, orphan)
        ] + [
            ["integration-viewer", "create", None, "failure", 403],
            ["integration-editor", "update", document["id"], "success", 200],
            ["integration-editor", "update", second["id"], "success", 200],
            ["integration-editor", "update", second["id"], "failure", 503],
            ["integration-editor", "delete", document["id"], "failure", 403],
        ] + [
            ["integration-admin", "delete", row["id"], "success", 204]
            for row in (document, second, orphan)
        ] + [
            [f"integration-{role}", "download_url", document["id"], "success", 200]
            for role in ("admin", "editor", "viewer")
        ] + [
            ["integration-viewer", "download_url", second["id"], "success", 200]
            for _ in range(3)
        ] + [
            ["integration-viewer", "download_url", orphan["id"], "success", 200],
            ["integration-editor", "create", boundary_id, "success", 202],
            ["integration-editor", "download_url", boundary_id, "success", 200],
            ["integration-editor", "create", None, "failure", 400],
            ["integration-admin", "delete", boundary_id, "success", 204],
            ["integration-editor", "update", document["id"], "failure", 409],
        ] + [
            [f"integration-{role}", "download_url", document["id"], "failure", 409]
            for role in ("admin", "editor", "viewer")
        ] + [
            ["integration-editor", "store", document_id, "success", 200]
            for document_id in (document["id"], second["id"], orphan["id"], boundary_id)
        ] + [
            ["integration-editor", "create", row["id"], "success", 202]
            for row in (broker_failed, failed)
        ] + [
            ["integration-editor", "store", row["id"], "failure", 503]
            for row in (broker_failed, failed)
        ] + [
            ["integration-admin", "delete", row["id"], "success", 204]
            for row in (broker_failed, failed)
        ] + [
            ["integration-viewer", "download_url", broker_failed["id"], "failure", 409],
        ]
        shell(
            "import json, sys; from collections import Counter; from django.contrib.auth import get_user_model; "
            "from apps.documents.models import Document, DocumentAuditEvent; "
            "identities = dict(get_user_model().objects.values_list('username', 'pk')); "
            "expected = Counter((identities[row[0]], *row[1:]) for row in json.load(sys.stdin)); "
            "actual = Counter(DocumentAuditEvent.objects.values_list('actor_id', 'action', 'document_id', 'outcome', 'status_code')); "
            "assert actual == expected and not Document.objects.exists(); "
            "assert DocumentAuditEvent.objects.count() == 37; "
            "from pathlib import Path; from django.conf import settings; "
            "assert not list(Path(settings.DOCUMENT_STAGING_ROOT).iterdir())",
            expected_events,
        )
        print("All 37 durable audit events match actors/outcomes; terminal/deleted documents left no staged files.", flush=True)
    except Exception:
        if active:
            try:
                diagnostic = run(compose + ["logs", "--no-color", "--tail", "50", "worker"])
                print("Worker failure diagnostics:\n" + redact(diagnostic[-5000:]), file=sys.stderr, flush=True)
            except Exception:
                print("Worker failure diagnostics unavailable.", file=sys.stderr, flush=True)
        raise
    finally:
        if sockets is not None:
            sockets.close()
        if active:
            config = json.loads(run(compose + ["config", "--format", "json"]))
            if not is_isolated_config(config, project):
                raise RuntimeError(f"Cleanup refused: configuration changed. Recovery files: {directory}")
            print("Removing only the disposable integration stack and its volumes...", flush=True)
            run(compose + ["down", "--volumes", "--remove-orphans"], timeout=180)
            after = set(run(["docker", "volume", "ls", "--format", "{{.Name}}"]).splitlines())
            if not before_volumes.issubset(after) or any(name.startswith(f"{project}_") for name in after):
                raise RuntimeError(f"Volume preservation/cleanup verification failed. Recovery files: {directory}")
            clean = True
        if not active or clean:
            shutil.rmtree(directory)
        else:
            print(f"Private recovery files retained at {directory}", file=sys.stderr)
    print("PASS: clean-stack integration complete; all pre-existing volumes preserved.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
