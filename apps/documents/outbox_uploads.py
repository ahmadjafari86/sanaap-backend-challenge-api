"""Durable logical storage attempts and transactionally committed receipts."""

from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from .models import Document, OutboxMessage, OutboxReceipt
from .outbox import enqueue_message, validate_message


def resolve_upload_message(*, document_id: int, event_id: str | None) -> OutboxMessage | None:
    if type(document_id) is not int or document_id <= 0:
        return None
    if event_id is not None:
        try:
            identity = UUID(str(event_id))
        except (ValueError, TypeError, AttributeError):
            return None
        return OutboxMessage.objects.filter(pk=identity, document_id=document_id,
                                            kind="document.upload_requested").first()
    document = Document.objects.filter(pk=document_id, status="PENDING").first()
    if document is None:
        return None
    attempt = min(document.storage_attempts + 1, 4)
    with transaction.atomic():
        return enqueue_message(kind="document.upload_requested", document_id=document_id,
                               payload={"attempt_number": attempt}, dedupe_key=f"upload:{document_id}:{attempt}")


def _record_receipt(message):
    OutboxReceipt.objects.create(event_id=message.pk)
    # Processing can finish before the publisher returns. Fence its old token.
    message.status = OutboxMessage.Status.PUBLISHED
    message.published_at = message.published_at or timezone.now()
    message.lease_token = message.locked_until = None
    message.last_error_code = ""
    message.save(update_fields=["status", "published_at", "lease_token", "locked_until", "last_error_code", "updated_at"])


def _process_upload(*, document_id, event_id):
    from .background import _compensate_attempt, _process_staged_document_attempt
    attempted_key = None
    try:
        with transaction.atomic():
            message = OutboxMessage.objects.select_for_update().filter(
                pk=event_id, document_id=document_id, kind="document.upload_requested",
            ).first()
            if message is None or OutboxReceipt.objects.filter(event_id=message.pk).exists():
                return False
            validate_message(kind=message.kind, document_id=message.document_id,
                             payload=message.payload, schema_version=message.schema_version)
            if message.available_at > timezone.now():
                return False
            document = Document.objects.select_for_update().filter(pk=document_id).first()
            if document is None or document.status != Document.Status.PENDING:
                _record_receipt(message)
                return False
            attempt = message.payload["attempt_number"]
            if document.storage_attempts < 4 and attempt != document.storage_attempts + 1:
                if attempt <= document.storage_attempts:
                    _record_receipt(message)
                return False
            attempted_key = document.object_key
            retry = _process_staged_document_attempt(document)
            if retry:
                next_attempt = document.storage_attempts + 1
                enqueue_message(kind="document.upload_requested", document_id=document_id,
                                payload={"attempt_number": next_attempt}, dedupe_key=f"upload:{document_id}:{next_attempt}",
                                available_at=timezone.now() + timedelta(seconds=min(2 ** (document.storage_attempts - 1), 8)))
            _record_receipt(message)
            return retry
    except Exception:
        if attempted_key is not None:
            _compensate_attempt(document_id, attempted_key)
        raise


def process_outbox_upload(*, document_id: int, event_id: UUID) -> None:
    _process_upload(document_id=document_id, event_id=event_id)
