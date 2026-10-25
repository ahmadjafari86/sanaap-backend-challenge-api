"""Phase 1 fault probes, restricted to the validated disposable integration stack."""

from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import time


def _probe_bytes():
    from io import BytesIO
    from PIL import Image
    image = BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(image, format="PNG")
    return image.getvalue()


def seed_probe(*, legacy=False):
    """Container-side fixture: real staging/storage/audit, no user credentials."""
    from uuid import uuid4
    from django.contrib.auth import get_user_model
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.db import transaction
    from apps.documents.audit import record_document_event
    from apps.documents.background import stage_document, staging_storage
    from apps.documents.models import Document
    from apps.documents.serializers import DocumentUploadSerializer
    User = get_user_model()
    user = User.objects.filter(username="outbox-integration-probe").first()
    if user is None:
        user = User.objects.create_user(username="outbox-integration-probe", role="admin")
    serializer = DocumentUploadSerializer(data={"title": "Outbox probe", "file": SimpleUploadedFile(
        "probe.png", _probe_bytes(), content_type="image/png")})
    serializer.is_valid(raise_exception=True)
    upload = serializer.validated_data["file"]
    if legacy:
        identity = uuid4().hex
        staging_storage().save(identity + ".stage", upload)
        with transaction.atomic():
            document = Document.objects.create(
                title="Legacy outbox probe", original_filename="probe.png", creator=user,
                content_type="image/png", file_size=len(_probe_bytes()), status="PENDING",
                object_key=f"documents/{identity}.png", staging_key=identity + ".stage",
            )
            record_document_event(actor_id=user.pk, document_id=document.pk,
                                  action="create", outcome="success", status_code=202)
    else:
        document = stage_document(title="Outbox probe", uploaded_file=upload, creator=user)
    return {"id": document.pk, "key": document.object_key, "staging_key": document.staging_key}


def verify_probe_result(identifiers):
    from collections import Counter
    from pathlib import Path
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from apps.documents.models import Document, DocumentAuditEvent
    from apps.outbox.models import OutboxMessage, OutboxReceipt
    actor = get_user_model().objects.get(username="outbox-integration-probe")
    expected = Counter({("create", "success", 202): 5, ("store", "success", 200): 5, ("delete", "success", 204): 5})
    actual = Counter(DocumentAuditEvent.objects.filter(actor_id=actor.pk).values_list("action", "outcome", "status_code"))
    assert actual == expected, "Probe audits must have exactly one terminal outcome per document"
    assert not Document.objects.exists(), "Delayed work resurrected a deleted document"
    messages = OutboxMessage.objects.filter(document_id__in=identifiers)
    assert not messages.exclude(status="PUBLISHED").exists(), "Probe obligations did not drain"
    assert OutboxReceipt.objects.filter(event_id__in=messages.filter(
        kind="document.upload_requested").values("pk")).count() == 5
    assert not list(Path(settings.DOCUMENT_STAGING_ROOT).iterdir()), "Staging cleanup did not drain"


def run_outbox_checks(*, compose: list[str], phase: str = "core", run=None) -> None:
    if phase != "core":
        raise ValueError("Only the Phase 1 core profile is implemented.")
    if run is None:
        raise ValueError("Use the integration harness's private/redacting command runner.")
    if __package__:
        from .integration_check import is_isolated_config
    else:
        from integration_check import is_isolated_config
    config = json.loads(run(compose + ["config", "--format", "json"]))
    if not is_isolated_config(config, config.get("name", "")):
        raise RuntimeError("Refusing outbox fault injection outside a disposable integration stack.")

    def shell(code):
        return run(compose + ["exec", "-T", "backend", "python", "manage.py", "shell", "-c", code], timeout=60)

    def query(expression):
        prefix = ("import json; from pathlib import Path; from datetime import timedelta; "
                  "from django.conf import settings; from django.utils import timezone; "
                  "from django.core.files.storage import default_storage; "
                  "from apps.documents.models import Document; from apps.outbox.models import OutboxMessage, OutboxReceipt; ")
        return json.loads(shell(prefix + f"print(json.dumps({expression}))").splitlines()[-1])

    def wait_for(expression, *, timeout=60):
        deadline = time.monotonic() + timeout
        while not query(expression):
            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out waiting for outbox probe recovery.")
            time.sleep(.25)

    def seed(*, legacy=False):
        return json.loads(shell("import json; from scripts.outbox_checks import seed_probe; "
                                f"print(json.dumps(seed_probe(legacy={legacy})))").splitlines()[-1])

    def ready(row):
        wait_for(f"Document.objects.filter(pk={row['id']}, status='READY', storage_attempts=1).exists()")
        wait_for(f"not OutboxMessage.objects.filter(document_id={row['id']}).exclude(status='PUBLISHED').exists()")
        assert query(f"default_storage.open({row['key']!r}).read() == __import__('scripts.outbox_checks', fromlist=['_probe_bytes'])._probe_bytes()")

    def stop(*services):
        run(compose + ["stop", "--timeout", "3", *services])

    def start(*services):
        run(compose + ["up", "-d", "--no-deps", "--wait", *services])

    stop("outbox-relay")
    first = seed()
    assert query(f"Document.objects.get(pk={first['id']}).status == 'PENDING'")
    assert query(f"(Path(settings.DOCUMENT_STAGING_ROOT) / {first['staging_key']!r}).exists()")
    start("outbox-relay")
    ready(first)
    print("Outbox: acceptance with relay stopped and restart recovery passed.", flush=True)

    stop("outbox-relay", "worker")
    lost = seed()
    run(compose + ["run", "--rm", "--no-deps", "outbox-relay", "--once", "--batch-size", "1"])
    assert query(f"OutboxMessage.objects.get(document_id={lost['id']}).status == 'PUBLISHED'")
    assert int(run(compose + ["exec", "-T", "redis", "redis-cli", "-n", "1", "LLEN", "documents"]).strip()) >= 1
    run(compose + ["exec", "-T", "redis", "redis-cli", "-n", "1", "DEL", "documents"])
    shell("from datetime import timedelta; from django.conf import settings; from django.utils import timezone; "
          "from apps.outbox.models import OutboxMessage; "
          f"OutboxMessage.objects.filter(document_id={lost['id']}).update(published_at=timezone.now()-timedelta(seconds=settings.OUTBOX_UPLOAD_STALE_SECONDS+1))")
    start("worker", "outbox-relay")
    ready(lost)
    print("Outbox: actual Redis task loss repaired by periodic receipt recovery.", flush=True)

    stop("outbox-relay", "worker")
    duplicate = seed()
    crash_code = ("import os; from django.utils import timezone; "
                  "from apps.outbox.delivery import claim_batch, dispatch_message; "
                  "claim = claim_batch(now=timezone.now(), limit=1)[0]; "
                  f"assert claim.document_id == {duplicate['id']}; "
                  "dispatch_message(claim); os._exit(23)")
    crashed = subprocess.run(compose + ["exec", "-T", "backend", "python", "manage.py", "shell", "-c", crash_code],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    if crashed.returncode != 23:
        raise RuntimeError("Publication crash probe did not reach the expected post-send checkpoint.")
    assert query(f"OutboxMessage.objects.get(document_id={duplicate['id']}).status == 'PROCESSING'")
    shell("from datetime import timedelta; from django.utils import timezone; from apps.outbox.models import OutboxMessage; "
          f"OutboxMessage.objects.filter(document_id={duplicate['id']}).update(locked_until=timezone.now()-timedelta(seconds=1))")
    start("outbox-relay")
    wait_for(f"OutboxMessage.objects.get(document_id={duplicate['id']}).status == 'PUBLISHED'")
    assert int(run(compose + ["exec", "-T", "redis", "redis-cli", "-n", "1", "LLEN", "documents"]).strip()) >= 2
    start("worker")
    ready(duplicate)
    print("Outbox: hard process exit after send, lease recovery and real duplicate delivery passed.", flush=True)

    stop("outbox-relay")
    concurrent = seed()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, compose + ["run", "--rm", "--no-deps", "outbox-relay", "--once", "--batch-size", "1"]) for _ in range(2)]
        for future in futures:
            future.result(timeout=60)
    start("outbox-relay")
    ready(concurrent)
    print("Outbox: concurrent real relay processes completed one logical upload.", flush=True)

    stop("outbox-relay", "worker")
    legacy = seed(legacy=True)
    assert query(f"not OutboxMessage.objects.filter(document_id={legacy['id']}).exists()")
    shell(f"from apps.documents.tasks import store_document; store_document.delay({legacy['id']})")
    start("worker")
    wait_for(f"Document.objects.filter(pk={legacy['id']}, status='READY').exists()")
    start("outbox-relay")
    ready(legacy)
    print("Outbox: legacy PENDING row and queued document-ID-only task recovered.", flush=True)

    stop("minio")
    shell("from apps.documents.models import Document; from apps.documents.services import delete_document; "
          f"delete_document(document_id={first['id']}, actor_id=Document.objects.get(pk={first['id']}).creator_id)")
    wait_for(f"OutboxMessage.objects.filter(document_id={first['id']}, kind='storage.object_delete_requested', attempts__gte=1, status='PENDING').exists()")
    start("minio")
    wait_for(f"not default_storage.exists({first['key']!r})")
    remaining = [lost, duplicate, concurrent, legacy]
    shell("from apps.documents.models import Document; from apps.documents.services import delete_document; "
          f"identifiers = {[row['id'] for row in remaining]!r}; "
          "[delete_document(document_id=identity, actor_id=Document.objects.get(pk=identity).creator_id) for identity in identifiers]")
    identifiers = [row["id"] for row in [first, *remaining]]
    wait_for(f"not OutboxMessage.objects.filter(document_id__in={identifiers!r}).exclude(status='PUBLISHED').exists()")
    assert all(query(f"not default_storage.exists({row['key']!r})") for row in remaining)

    replay_id = query(f"str(OutboxMessage.objects.get(document_id={first['id']}, kind='storage.object_delete_requested').pk)")
    shell(f"from apps.outbox.models import OutboxMessage; OutboxMessage.objects.filter(pk={replay_id!r}).update(status='DEAD', attempts=20)")
    run(compose + ["exec", "-T", "backend", "python", "manage.py", "replay_outbox", replay_id])
    wait_for(f"OutboxMessage.objects.get(pk={replay_id!r}).status == 'PUBLISHED'")
    shell(f"from scripts.outbox_checks import verify_probe_result; verify_probe_result({identifiers!r})")
    status = json.loads(run(compose + ["exec", "-T", "backend", "python", "manage.py", "outbox_status"]))
    assert status["counts"]["DEAD"] == 0
    print("Outbox: MinIO cleanup recovery, missing-object replay, exact 15 audits/5 receipts and empty staging passed.", flush=True)
