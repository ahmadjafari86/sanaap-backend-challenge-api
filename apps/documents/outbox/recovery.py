"""Recover publication without a processing receipt; never reset storage state."""

from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef

from apps.outbox.models import OutboxMessage, OutboxReceipt

from ..models import Document
from .events import EventKind
from .uploads import resolve_upload_message


def recover_uploads(*, now: datetime, limit: int, dry_run: bool = False) -> int:
    if not 1 <= limit <= 1000:
        raise ValueError("Recovery batch size must be between 1 and 1000.")
    unreceipted = OutboxMessage.objects.filter(kind=EventKind.UPLOAD_REQUESTED).filter(
        ~Exists(OutboxReceipt.objects.filter(event_id=OuterRef("pk"))),
    )
    stale = unreceipted.filter(status="PUBLISHED", published_at__lte=now - timedelta(
        seconds=settings.OUTBOX_UPLOAD_STALE_SECONDS))
    legacy = Document.objects.filter(status="PENDING").filter(
        ~Exists(unreceipted.filter(document_id=OuterRef("pk"))),
    ).order_by("pk")
    if dry_run:
        count = len(list(stale.values_list("pk", flat=True)[:limit]))
        return count + len(list(legacy.values_list("pk", flat=True)[:limit - count]))
    recovered = 0
    with transaction.atomic():
        messages = list(stale.select_for_update(skip_locked=True).order_by("published_at", "id")[:limit])
        for message in messages:
            document = Document.objects.select_for_update(skip_locked=True).filter(pk=message.document_id).first()
            if document is None and Document.objects.filter(pk=message.document_id).exists():
                continue
            if document is None or document.status != "PENDING":
                OutboxReceipt.objects.get_or_create(event_id=message.pk)
            else:
                message.status = "PENDING"
                message.available_at = now
                message.published_at = message.lease_token = message.locked_until = None
                message.last_error_code = "recovered_unreceipted"
                message.save(update_fields=["status", "available_at", "published_at", "lease_token", "locked_until", "last_error_code", "updated_at"])
            recovered += 1
    # No document lock during enqueue; a racing terminal transition is a consumer
    # no-op, and unique attempt keys serialize seeders without reversing lock order.
    for document_id in list(legacy.values_list("pk", flat=True)[:limit - recovered]):
        if resolve_upload_message(document_id=document_id, event_id=None) is not None:
            recovered += 1
    return recovered
