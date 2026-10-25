"""Upload processing metrics extending the generic outbox status."""

from django.db.models import Exists, OuterRef

from apps.outbox.models import OutboxMessage, OutboxReceipt

from .events import EventKind


def get_upload_status(*, now) -> dict:
    unreceipted = OutboxMessage.objects.filter(
        kind=EventKind.UPLOAD_REQUESTED, status=OutboxMessage.Status.PUBLISHED,
    ).filter(
        ~Exists(OutboxReceipt.objects.filter(event_id=OuterRef("pk"))),
    ).order_by("published_at").first()
    return {
        "oldest_unreceipted_age_seconds": max(0, (now - unreceipted.published_at).total_seconds())
        if unreceipted and unreceipted.published_at else None,
    }
