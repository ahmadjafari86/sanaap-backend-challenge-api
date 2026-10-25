"""Document event names and their restricted payload schemas."""

from functools import partial
import re
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import models


class EventKind(models.TextChoices):
    UPLOAD_REQUESTED = "document.upload_requested", "Upload document"
    OBJECT_DELETE = "storage.object_delete_requested", "Delete object"
    STAGING_DELETE = "storage.staging_delete_requested", "Delete staged file"
    METADATA_INVALIDATE = "document.metadata_invalidate_requested", "Invalidate metadata"
    NOTIFICATION = "document.notification_requested", "Publish notification"


def validate_payload(*, kind, document_id, payload):
    fields = {
        EventKind.UPLOAD_REQUESTED: {"attempt_number"},
        EventKind.OBJECT_DELETE: {"object_key"},
        EventKind.STAGING_DELETE: {"staging_key"},
        EventKind.METADATA_INVALIDATE: {"metadata_version"},
        EventKind.NOTIFICATION: {"event"},
    }
    if kind not in fields or set(payload) != fields[kind]:
        raise ValidationError("Unknown kind or payload fields.", code="invalid_payload")
    if kind in (EventKind.UPLOAD_REQUESTED, EventKind.METADATA_INVALIDATE,
                EventKind.NOTIFICATION) and document_id is None:
        raise ValidationError("A document snapshot is required.", code="invalid_document")
    if kind == EventKind.UPLOAD_REQUESTED:
        attempt = payload["attempt_number"]
        if type(attempt) is not int or not 1 <= attempt <= 4:
            raise ValidationError("Invalid storage attempt.", code="invalid_payload")
    elif kind == EventKind.STAGING_DELETE:
        key = payload["staging_key"]
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}\.stage", key):
            raise ValidationError("Invalid staging identifier.", code="invalid_payload")
    elif kind == EventKind.OBJECT_DELETE:
        key = payload["object_key"]
        if not isinstance(key, str) or not key or len(key) > 1024 or "\x00" in key:
            raise ValidationError("Invalid object key.", code="invalid_payload")
    elif kind == EventKind.METADATA_INVALIDATE:
        try:
            UUID(payload["metadata_version"])
        except (ValueError, TypeError, AttributeError):
            raise ValidationError("Invalid metadata version.", code="invalid_payload") from None
    elif payload["event"] not in ("document.ready", "document.updated"):
        raise ValidationError("Invalid notification type.", code="invalid_payload")


VALIDATORS = {kind: partial(validate_payload, kind=kind) for kind in EventKind}
