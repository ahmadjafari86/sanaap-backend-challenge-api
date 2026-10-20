import logging
from functools import partial
from uuid import uuid4

from django.core.files.storage import default_storage
from django.db import transaction

from .caching import invalidate_document_metadata
from .models import Document

logger = logging.getLogger(__name__)


def create_document(*, title, uploaded_file, creator):
    original_filename = uploaded_file.name
    content_type = uploaded_file.content_type
    file_size = uploaded_file.size

    extension = uploaded_file.image.format.lower()
    proposed_key = f"documents/{uuid4().hex}.{extension}"

    stored_key = default_storage.save(proposed_key, uploaded_file)

    try:
        with transaction.atomic():
            return Document.objects.create(
                title=title,
                original_filename=original_filename,
                object_key=stored_key,
                content_type=content_type,
                file_size=file_size,
                creator=creator,
            )
    except Exception:
        try:
            default_storage.delete(stored_key)
        except Exception:
            logger.exception(
                "Failed to clean up the uploaded document file.",
            )

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


def update_document(*, document_id, title=None, uploaded_file=None):
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
            previous_key = document.object_key
            previous_version = document.metadata_version

            for field, value in changes.items():
                setattr(document, field, value)
            document.metadata_version = uuid4()
            document.save(update_fields=[*changes, "updated_at", "metadata_version"])

            transaction.on_commit(
                partial(invalidate_document_metadata, document.pk, previous_version),
            )

            if stored_key is not None:
                transaction.on_commit(partial(_delete_document_object, previous_key))

        return document
    except Exception:
        if stored_key is not None:
            _delete_document_object(stored_key)
        raise


def delete_document(*, document_id):
    with transaction.atomic():
        document = Document.objects.select_for_update().get(pk=document_id)
        object_key = document.object_key
        metadata_version = document.metadata_version
        document.delete()
        transaction.on_commit(
            partial(invalidate_document_metadata, document_id, metadata_version),
        )
        transaction.on_commit(partial(_delete_document_object, object_key))


def get_document_download_url(*, document):
    return default_storage.url(document.object_key)
