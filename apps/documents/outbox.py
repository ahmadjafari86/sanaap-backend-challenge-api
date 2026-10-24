"""Validate and persist obligations inside the originating mutation transaction."""

from datetime import datetime
import re
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import OutboxMessage


def validate_message(*, kind, document_id, payload, schema_version=1):
    if type(schema_version) is not int or schema_version != 1:
        raise ValidationError("Unsupported outbox schema version.", code="invalid_schema")
    if document_id is not None and (type(document_id) is not int or not 0 < document_id <= 2**63 - 1):
        raise ValidationError("Invalid document snapshot.", code="invalid_document")
    if not isinstance(payload, dict):
        raise ValidationError("Invalid outbox payload.", code="invalid_payload")

    fields = {
        OutboxMessage.Kind.UPLOAD_REQUESTED: {"attempt_number"},
        OutboxMessage.Kind.OBJECT_DELETE: {"object_key"},
        OutboxMessage.Kind.STAGING_DELETE: {"staging_key"},
        OutboxMessage.Kind.METADATA_INVALIDATE: {"metadata_version"},
        OutboxMessage.Kind.NOTIFICATION: {"event"},
    }
    if kind not in fields or set(payload) != fields[kind]:
        raise ValidationError("Unknown kind or payload fields.", code="invalid_payload")
    if kind in (OutboxMessage.Kind.UPLOAD_REQUESTED, OutboxMessage.Kind.METADATA_INVALIDATE,
                OutboxMessage.Kind.NOTIFICATION) and document_id is None:
        raise ValidationError("A document snapshot is required.", code="invalid_document")
    if kind == OutboxMessage.Kind.UPLOAD_REQUESTED:
        attempt = payload["attempt_number"]
        if type(attempt) is not int or not 1 <= attempt <= 4:
            raise ValidationError("Invalid storage attempt.", code="invalid_payload")
    elif kind == OutboxMessage.Kind.STAGING_DELETE:
        key = payload["staging_key"]
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}\.stage", key):
            raise ValidationError("Invalid staging identifier.", code="invalid_payload")
    elif kind == OutboxMessage.Kind.OBJECT_DELETE:
        key = payload["object_key"]
        if not isinstance(key, str) or not key or len(key) > 1024 or "\x00" in key:
            raise ValidationError("Invalid object key.", code="invalid_payload")
    elif kind == OutboxMessage.Kind.METADATA_INVALIDATE:
        try:
            UUID(payload["metadata_version"])
        except (ValueError, TypeError, AttributeError):
            raise ValidationError("Invalid metadata version.", code="invalid_payload") from None
    elif payload["event"] not in ("document.ready", "document.updated"):
        raise ValidationError("Invalid notification type.", code="invalid_payload")


def enqueue_message(*, kind: str, document_id: int | None, payload: dict,
                    dedupe_key: str, available_at: datetime | None = None) -> OutboxMessage:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Outbox enqueue requires the mutation's atomic block.")
    validate_message(kind=kind, document_id=document_id, payload=payload)
    if not isinstance(dedupe_key, str) or not dedupe_key.strip() or len(dedupe_key) > 255:
        raise ValidationError("Invalid deduplication key.", code="invalid_dedupe")
    if available_at is not None and (not isinstance(available_at, datetime) or timezone.is_naive(available_at)):
        raise ValidationError("An aware delivery time is required.", code="invalid_time")
    content = {"kind": kind, "document_id": document_id, "payload": payload, "schema_version": 1}
    # get_or_create uses a savepoint around insertion and recovers uniqueness races.
    message, _ = OutboxMessage.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={**content, "available_at": available_at if available_at is not None else timezone.now()},
    )
    if any(getattr(message, field) != value for field, value in content.items()):
        raise ValidationError("Deduplication key reused for different content.", code="dedupe_conflict")
    return message
