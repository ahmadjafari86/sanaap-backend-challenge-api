import logging

from django.conf import settings
from django.core.cache import cache

from .models import Document
from .serializers import DocumentSerializer

logger = logging.getLogger(__name__)


def _metadata_key(document_id, metadata_version):
    return f"documents:metadata:v1:{document_id}:{metadata_version}"


def get_document_metadata(*, document):
    key = _metadata_key(document.pk, document.metadata_version)
    cache_available = True
    try:
        metadata = cache.get(key)
    except Exception as error:
        logger.warning("Document metadata cache read failed (%s).", type(error).__name__)
        metadata = None
        cache_available = False

    if metadata is not None:
        return metadata

    current = Document.objects.get(pk=document.pk)
    metadata = dict(DocumentSerializer(current).data)
    if cache_available:
        try:
            cache.set(
                _metadata_key(current.pk, current.metadata_version),
                metadata,
                timeout=settings.DOCUMENT_METADATA_CACHE_TTL_SECONDS,
            )
        except Exception as error:
            logger.warning("Document metadata cache write failed (%s).", type(error).__name__)
    return metadata


def invalidate_document_metadata(document_id, metadata_version):
    try:
        cache.delete(_metadata_key(document_id, metadata_version))
    except Exception as error:
        logger.warning("Document metadata cache invalidation failed (%s).", type(error).__name__)
