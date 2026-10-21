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

logger = logging.getLogger(__name__)


def staging_storage():
    return FileSystemStorage(location=settings.DOCUMENT_STAGING_ROOT, file_permissions_mode=0o600, directory_permissions_mode=0o700)


def delete_staged_file(key):
    try:
        if not re.fullmatch(r"[0-9a-f]{32}\.stage", key):
            raise ValueError("Invalid staging identifier")
        staging_storage().delete(key)
    except Exception as error:
        logger.error("Staged file cleanup failed (%s).", type(error).__name__)


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
            transaction.on_commit(partial(publish_document, document.pk))
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
    transaction.on_commit(partial(delete_staged_file, staging_key))


def publish_document(document_id):
    from .tasks import store_document
    try:
        store_document.delay(document_id)
        return True
    except Exception as error:
        logger.error("Document task publication failed (%s).", type(error).__name__)
        try:
            with transaction.atomic():
                document = Document.objects.select_for_update().filter(pk=document_id).first()
                if document is not None and document.status == Document.Status.PENDING:
                    transition_document(document, Document.Status.FAILED)
        except Exception as failure:
            logger.error("Document dispatch failure could not be persisted (%s).", type(failure).__name__)
        return False


def process_staged_document(document_id):
    """Serialize one attempt; return whether a transient failure needs retry."""
    from .services import _delete_document_object, default_storage
    attempted_key = None
    try:
        with transaction.atomic():
            document = Document.objects.select_for_update().filter(pk=document_id).first()
            if document is None or document.status != Document.Status.PENDING:
                return False
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
                    attempted_key = None
                logger.warning("Background document storage failed (%s).", type(error).__name__)
                transient = isinstance(error, (OSError, MinioException, HTTPError)) and not isinstance(error, FileNotFoundError)
                if not transient or document.storage_attempts >= 4:
                    transition_document(document, Document.Status.FAILED)
                    return False
                document.save(update_fields=["storage_attempts"])
                return True
            transition_document(document, Document.Status.READY)
        return False
    except Exception:
        if attempted_key is not None:
            _compensate_attempt(document_id, attempted_key)
        raise


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
