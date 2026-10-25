"""Persist obligations inside the originating mutation transaction."""

from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import OutboxMessage
from .validation import validate_message


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
