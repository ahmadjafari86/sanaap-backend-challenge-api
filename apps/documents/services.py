import logging
from uuid import uuid4

from django.core.files.storage import default_storage
from django.db import transaction

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