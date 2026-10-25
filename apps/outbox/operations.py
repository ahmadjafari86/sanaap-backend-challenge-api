"""Bounded operator queries and explicit replay; never expose message payloads."""

from datetime import datetime
from uuid import UUID

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.db.models import Count

from .models import OutboxMessage
from .registry import registry


def get_outbox_status(*, now: datetime) -> dict:
    counts = dict.fromkeys(OutboxMessage.Status.values, 0)
    counts.update({row["status"]: row["count"] for row in
                   OutboxMessage.objects.values("status").annotate(count=Count("id"))})
    due = OutboxMessage.objects.filter(status="PENDING", available_at__lte=now).order_by("available_at").first()
    status = {
        "counts": counts,
        "oldest_due_age_seconds": max(0, (now - due.available_at).total_seconds()) if due else None,
        "expired_leases": OutboxMessage.objects.filter(status="PROCESSING", locked_until__lte=now).count(),
        "dead_event_ids": [str(value) for value in OutboxMessage.objects.filter(status="DEAD").order_by(
            "created_at", "id").values_list("pk", flat=True)[:20]],
    }

    extensions = registry.get_status(now=now)
    if status.keys() & extensions.keys():
        raise ImproperlyConfigured("Adapter cannot overwrite core outbox status fields.")
    return {**status, **extensions}


def replay_dead_messages(*, event_ids: list[UUID], now: datetime) -> int:
    identifiers = set(event_ids)
    if not identifiers or len(identifiers) > 100:
        raise ValidationError("Provide between 1 and 100 explicit event IDs.")
    with transaction.atomic():
        messages = list(OutboxMessage.objects.select_for_update().filter(pk__in=identifiers).order_by("id"))
        if len(messages) != len(identifiers) or any(message.status != "DEAD" for message in messages):
            raise ValidationError("Every replay target must exist and be DEAD.")
        return OutboxMessage.objects.filter(pk__in=identifiers).update(
            status="PENDING", attempts=0, available_at=now, published_at=None,
            lease_token=None, locked_until=None, last_error_code="", updated_at=now,
        )
