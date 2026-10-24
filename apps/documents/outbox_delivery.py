"""Claim briefly, perform external I/O without locks, and fence finalization."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import random
from uuid import UUID, uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import OutboxMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryClaim:
    event_id: UUID
    kind: str
    document_id: int | None
    payload: dict
    schema_version: int
    lease_token: UUID
    attempts: int


def claim_batch(*, now: datetime, limit: int) -> list[DeliveryClaim]:
    if not 1 <= limit <= 100:
        raise ValueError("Outbox batch size must be between 1 and 100.")
    claims = []
    with transaction.atomic():
        messages = OutboxMessage.objects.select_for_update(skip_locked=True).filter(
            Q(status=OutboxMessage.Status.PENDING, available_at__lte=now)
            | Q(status=OutboxMessage.Status.PROCESSING, locked_until__lte=now),
        ).order_by("available_at", "created_at", "id")[:limit]
        for message in messages:
            if message.attempts >= settings.OUTBOX_MAX_PUBLICATION_ATTEMPTS:
                message.status = OutboxMessage.Status.DEAD
                message.lease_token = message.locked_until = None
                message.last_error_code = "lease_exhausted"
            else:
                message.status = OutboxMessage.Status.PROCESSING
                message.attempts += 1
                message.lease_token = uuid4()
                message.locked_until = now + timedelta(seconds=settings.OUTBOX_LEASE_SECONDS)
                claims.append(DeliveryClaim(
                    event_id=message.pk, kind=message.kind, document_id=message.document_id,
                    payload=message.payload, schema_version=message.schema_version,
                    lease_token=message.lease_token, attempts=message.attempts,
                ))
            message.save(update_fields=["status", "attempts", "lease_token", "locked_until", "last_error_code", "updated_at"])
    return claims


def _finish(claim, *, now, error=None):
    values = {"lease_token": None, "locked_until": None, "updated_at": now}
    if error is None:
        values.update(status=OutboxMessage.Status.PUBLISHED, published_at=now, last_error_code="")
    else:
        permanent = isinstance(error, ValidationError)
        code = "invalid_payload" if permanent else "io_error" if isinstance(error, OSError) else "delivery_error"
        exhausted = claim.attempts >= settings.OUTBOX_MAX_PUBLICATION_ATTEMPTS
        delay = min(2 ** min(claim.attempts, 16), 300)
        values.update(
            status=OutboxMessage.Status.DEAD if permanent or exhausted else OutboxMessage.Status.PENDING,
            available_at=now + timedelta(seconds=delay + random.uniform(0, delay * .2)),
            published_at=None, last_error_code=code,
        )
        logger.warning("Outbox delivery failed (event=%s, code=%s).", claim.event_id, code)
    return bool(OutboxMessage.objects.filter(
        pk=claim.event_id, status=OutboxMessage.Status.PROCESSING, lease_token=claim.lease_token,
    ).update(**values))


def dispatch_message(claim: DeliveryClaim) -> None:
    from .outbox_handlers import dispatch_message as dispatch
    dispatch(claim)


def relay_once(*, now: datetime | None = None, batch_size: int | None = None) -> int:
    claims = claim_batch(now=now or timezone.now(), limit=batch_size if batch_size is not None else settings.OUTBOX_BATCH_SIZE)
    for claim in claims:
        try:
            dispatch_message(claim)
        except Exception as error:
            _finish(claim, now=now or timezone.now(), error=error)
        else:
            _finish(claim, now=now or timezone.now())
    return len(claims)
