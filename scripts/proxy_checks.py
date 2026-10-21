"""HTTP assertions for the deployed reverse proxy; no Docker or fixture ownership."""

import json
from urllib.parse import parse_qs, urlencode, urlsplit


def check_documentation_access(client, password):
    paths = ("/api/docs/", "/api/schema/", "/api/schema/?format=json")

    def assert_login_required():
        for path in paths:
            body, headers = client.http("GET", client.origin + path, expected=302, return_headers=True)
            destination = urlsplit(headers["location"])
            assert destination.path == "/admin/login/"
            assert parse_qs(destination.query) == {"next": [path]}
            assert "no-store" in headers.get("cache-control", "") and b"SwaggerUIBundle" not in body

    def csrf():
        return next(cookie.value for cookie in client.cookies if cookie.name == "csrftoken")

    assert_login_required()
    client.http("GET", client.origin + "/admin/login/?next=/api/docs/")
    client.http("POST", client.origin + "/admin/login/?next=/api/docs/", expected=302,
                body=urlencode({"username": "integration-staff", "password": password,
                                "csrfmiddlewaretoken": csrf(), "next": "/api/docs/"}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"})
    docs, headers = client.http("GET", client.origin + "/api/docs/", return_headers=True)
    assert headers.get("server") == "nginx" and "no-store" in headers.get("cache-control", "")
    assert b"SwaggerUIBundle" in docs
    schema = client.api("GET", "/api/schema/?format=json")
    assert "/api/v1/documents/" in schema["paths"]
    # A valid admin session still does not authenticate JWT business endpoints.
    client.api("GET", "/api/v1/documents/", expected=401)
    client.http("GET", client.origin + "/admin/documents/document/")
    client.http("POST", client.origin + "/admin/logout/", expected=200,
                body=urlencode({"csrfmiddlewaretoken": csrf()}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert_login_required()


def check_proxy_http(client):
    _, headers = client.http("GET", client.origin + "/healthz", return_headers=True)
    assert headers.get("server") == "nginx", "Public API entrypoint must be Nginx."
    css, headers = client.http("GET", client.origin + "/static/admin/css/base.css", return_headers=True)
    assert "text/css" in headers.get("content-type", "") and b"body" in css
    script, headers = client.http("GET", client.origin + "/static/admin/js/core.js", return_headers=True)
    assert "javascript" in headers.get("content-type", "") and script
    client.http("GET", client.origin + "/static/missing-asset.css", expected=404)
    denied, headers = client.http("GET", client.origin + "/api/v1/documents/", expected=401, return_headers=True)
    assert "detail" in json.loads(denied) and headers.get("www-authenticate", "").startswith("Bearer")
    client.http("GET", client.origin + "/api/docs/", headers={"Host": "attacker.invalid"}, expected=400)
    return css


def check_forwarded_links(client, token):
    page = client.api("GET", "/api/v1/documents/?page_size=1", token=token, headers={
        "X-Forwarded-Proto": "https", "X-Forwarded-Host": "attacker.invalid",
        "X-Forwarded-Port": "443", "X-Forwarded-SSL": "on", "X-Forwarded-Protocol": "ssl",
    })
    assert page["count"] >= 2 and page["next"]
    assert page["next"].startswith(client.origin + "/api/v1/documents/?")
    assert urlsplit(page["next"]).scheme == "http"


def check_upload_limits(client, *, image, editor, admin):
    exact = image + b"\0" * (10 * 1024 * 1024 - len(image))
    document = client.api("POST", "/api/v1/documents/", token=editor,
                          data={"title": "Boundary image"}, image=exact, expected=202)
    document = client.wait_document(document, editor)
    assert document["file_size"] == 10 * 1024 * 1024
    signed = client.api("GET", f"/api/v1/documents/{document['id']}/download/", token=editor)
    assert client.http("GET", signed["download_url"]) == exact
    invalid = client.api("POST", "/api/v1/documents/", token=editor,
                         data={"title": "Too large"}, image=exact + b"\0", expected=400)
    assert "file" in invalid
    # A known oversized Content-Length must be rejected before reading any body.
    client.http("POST", client.origin + "/api/v1/documents/", body=b"",
                headers={"Content-Length": str(12 * 1024 * 1024)}, expected=413)
    client.api("DELETE", f"/api/v1/documents/{document['id']}/", token=admin, expected=204)
    return document["id"]
