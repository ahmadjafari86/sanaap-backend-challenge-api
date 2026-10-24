"""Accept validated uploads and manage private staging/state transitions."""

from functools import partial
import logging
import re
from uuid import uuid4

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from minio.error import MinioException
from urllib3.exceptions import HTTPError

from .audit import record_document_event
from .caching import invalidate_document_metadata
from .models import Document, DocumentAuditEvent
from .notifications import notify_document
from .outbox import enqueue_message

logger = logging.getLogger(__name__)


def staging_storage():
    return FileSystemStorage(location=settings.DOCUMENT_STAGING_ROOT, file_permissions_mode=0o600, directory_permissions_mode=0o700)


def delete_staged_file(key):
    if not re.fullmatch(r"[0-9a-f]{32}\.stage", key):
        logger.error("Invalid staging identifier cannot be cleaned.")
        return False
    try:
        staging_storage().delete(key)
        return True
    except Exception as error:
        logger.error("Staged file cleanup failed (%s).", type(error).__name__)
        try:
            with transaction.atomic():
                enqueue_message(kind="storage.staging_delete_requested", document_id=None,
                                payload={"staging_key": key}, dedupe_key=f"staging-compensation:{uuid4()}")
        except Exception as failure:
            logger.error("Staging cleanup persistence deferred (%s).", type(failure).__name__)
        return False


def stage_document(*, title, uploaded_file, creator):
    identity = uuid4().hex
    staging_key = identity + ".stage"
    try:
        staging_storage().save(staging_key, uploaded_file)
        with transaction.atomic():
            document = Document.objects.create(
                title=title, original_filename=uploaded_file.name,
                object_key=f"documents/{identity}.{uploaded_file.image.format.lower()}",
                content_type=uploaded_file.content_type, file_size=uploaded_file.size,
                creator=creator, status=Document.Status.PENDING, staging_key=staging_key,
            )
            record_document_event(actor_id=creator.pk, action=DocumentAuditEvent.Action.CREATE,
                                  document_id=document.pk, outcome=DocumentAuditEvent.Outcome.SUCCESS, status_code=202)
            enqueue_message(kind="document.upload_requested", document_id=document.pk,
                            payload={"attempt_number": 1}, dedupe_key=f"upload:{document.pk}:1")
        return document
    except Exception:
        delete_staged_file(staging_key)
        raise


def transition_document(document, state):
    previous_version, staging_key = document.metadata_version, document.staging_key
    document.status = state
    document.metadata_version = uuid4()
    document.staging_key = ""
    document.save(update_fields=["status", "metadata_version", "staging_key", "storage_attempts", "updated_at"])
    record_document_event(
        actor_id=document.creator_id, action=DocumentAuditEvent.Action.STORE, document_id=document.pk,
        outcome=DocumentAuditEvent.Outcome.SUCCESS if state == Document.Status.READY else DocumentAuditEvent.Outcome.FAILURE,
        status_code=200 if state == Document.Status.READY else 503,
    )
    transaction.on_commit(partial(invalidate_document_metadata, document.pk, previous_version))
    if re.fullmatch(r"[0-9a-f]{32}\.stage", staging_key):
        enqueue_message(kind="storage.staging_delete_requested", document_id=document.pk,
                        payload={"staging_key": staging_key},
                        dedupe_key=f"staging-delete:{document.pk}:{previous_version}")
    if state == Document.Status.READY:
        transaction.on_commit(partial(notify_document, document.pk, "document.ready"))


def publish_document(document_id):
    """Compatibility entry point: ensure an obligation, never publish inline."""
    from .outbox_uploads import resolve_upload_message
    try:
        return resolve_upload_message(document_id=document_id, event_id=None) is not None
    except Exception as error:
        logger.error("Document obligation could not be persisted (%s).", type(error).__name__)
        return False


def process_staged_document(document_id):
    """Compatibility wrapper; the outbox owns timing and deduplication."""
    from .outbox_uploads import _process_upload, resolve_upload_message
    message = resolve_upload_message(document_id=document_id, event_id=None)
    return _process_upload(document_id=document_id, event_id=message.pk) if message else False


def _process_staged_document_attempt(document):
    """One bounded attempt; caller owns the event/document transaction and locks."""
    from .services import _delete_document_object, default_storage
    attempted_key = None
    if document.storage_attempts >= 4:
        transition_document(document, Document.Status.FAILED)
        return False
    document.storage_attempts += 1
    try:
        if not re.fullmatch(r"[0-9a-f]{32}\.stage", document.staging_key):
            raise FileNotFoundError("Invalid staging identifier")
        with staging_storage().open(document.staging_key, "rb") as content:
            content.content_type = document.content_type
            attempted_key = document.object_key
            stored_key = default_storage.save(document.object_key, content)
            if stored_key != document.object_key:
                _delete_document_object(stored_key)
                raise OSError("Storage changed the stable object key")
    except Exception as error:
        if attempted_key is not None:
            _delete_document_object(attempted_key)
        logger.warning("Background document storage failed (%s).", type(error).__name__)
        transient = isinstance(error, (OSError, MinioException, HTTPError)) and not isinstance(error, FileNotFoundError)
        if not transient or document.storage_attempts >= 4:
            transition_document(document, Document.Status.FAILED)
            return False
        document.save(update_fields=["storage_attempts"])
        return True
    transition_document(document, Document.Status.READY)
    return False


def _compensate_attempt(document_id, object_key):
    from .services import _delete_document_object
    try:
        # The failed transaction released its lock. A redelivery may now own this
        # stable key, so establish current ownership again before deleting it.
        with transaction.atomic():
            current = Document.objects.select_for_update().filter(pk=document_id).first()
            if current is None or current.status != Document.Status.READY or current.object_key != object_key:
                _delete_document_object(object_key)
    except Exception as error:
        # A possible orphan is safer than deleting an object whose commit we
        # cannot verify during a database outage. Preserve the original error.
        logger.error("Background upload compensation deferred (%s).", type(error).__name__)
