import logging
from functools import partial
from uuid import uuid4

from django.core.files.storage import default_storage
from django.db import transaction

from .audit import record_document_event
from .caching import invalidate_document_metadata
from .exceptions import DocumentNotReady
from .models import Document, DocumentAuditEvent
from .notifications import notify_document

logger = logging.getLogger(__name__)


def create_document(*, title, uploaded_file, creator):
    original_filename = uploaded_file.name
    content_type = uploaded_file.content_type
    file_size = uploaded_file.size

    extension = uploaded_file.image.format.lower()
    proposed_key = f"documents/{uuid4().hex}.{extension}"

    try:
        stored_key = default_storage.save(proposed_key, uploaded_file)
    except Exception:
        _delete_document_object(proposed_key)
        raise

    try:
        with transaction.atomic():
            document = Document.objects.create(
                title=title,
                original_filename=original_filename,
                object_key=stored_key,
                content_type=content_type,
                file_size=file_size,
                creator=creator,
            )
            record_document_event(
                actor_id=creator.pk,
                action=DocumentAuditEvent.Action.CREATE,
                document_id=document.pk,
                outcome=DocumentAuditEvent.Outcome.SUCCESS,
                status_code=201,
            )
            transaction.on_commit(partial(notify_document, document.pk, "document.ready"))
            return document
    except Exception:
        _delete_document_object(stored_key)
        raise


def _delete_document_object(object_key):
    try:
        default_storage.delete(object_key)
    except Exception as error:
        logger.error(
            "Document object cleanup failed for %s (%s).",
            object_key,
            type(error).__name__,
        )


def update_document(*, document_id, title=None, uploaded_file=None, actor_id=None):
    changes = {}
    stored_key = None

    if title is not None:
        changes["title"] = title

    if uploaded_file is not None:
        changes.update(
            original_filename=uploaded_file.name,
            content_type=uploaded_file.content_type,
            file_size=uploaded_file.size,
        )
        extension = uploaded_file.image.format.lower()
        proposed_key = f"documents/{uuid4().hex}.{extension}"
        try:
            stored_key = default_storage.save(proposed_key, uploaded_file)
        except Exception:
            _delete_document_object(proposed_key)
            raise
        changes["object_key"] = stored_key

    try:
        with transaction.atomic():
            document = Document.objects.select_for_update().get(pk=document_id)
            if document.status != Document.Status.READY:
                raise DocumentNotReady()
            previous_key = document.object_key
            previous_version = document.metadata_version

            for field, value in changes.items():
                setattr(document, field, value)
            document.metadata_version = uuid4()
            document.save(update_fields=[*changes, "updated_at", "metadata_version"])
            record_document_event(
                actor_id=actor_id,
                action=DocumentAuditEvent.Action.UPDATE,
                document_id=document.pk,
                outcome=DocumentAuditEvent.Outcome.SUCCESS,
                status_code=200,
            )

            transaction.on_commit(
                partial(invalidate_document_metadata, document.pk, previous_version),
            )

            if stored_key is not None:
                transaction.on_commit(partial(_delete_document_object, previous_key))

            transaction.on_commit(partial(notify_document, document.pk, "document.updated"))

        return document
    except Exception:
        if stored_key is not None:
            _delete_document_object(stored_key)
        raise


def delete_document(*, document_id, actor_id=None):
    with transaction.atomic():
        document = Document.objects.select_for_update().get(pk=document_id)
        object_key = document.object_key
        metadata_version = document.metadata_version
        staging_key = document.staging_key
        document.delete()
        record_document_event(
            actor_id=actor_id,
            action=DocumentAuditEvent.Action.DELETE,
            document_id=document_id,
            outcome=DocumentAuditEvent.Outcome.SUCCESS,
            status_code=204,
        )
        transaction.on_commit(
            partial(invalidate_document_metadata, document_id, metadata_version),
        )
        transaction.on_commit(partial(_delete_document_object, object_key))
        if staging_key:
            from .background import delete_staged_file
            transaction.on_commit(partial(delete_staged_file, staging_key))


def get_document_download_url(*, document, actor_id=None):
    if document.status != Document.Status.READY:
        raise DocumentNotReady()
    download_url = default_storage.url(document.object_key)
    record_document_event(
        actor_id=actor_id,
        action=DocumentAuditEvent.Action.DOWNLOAD_URL,
        document_id=document.pk,
        outcome=DocumentAuditEvent.Outcome.SUCCESS,
        status_code=200,
    )
    return download_url
