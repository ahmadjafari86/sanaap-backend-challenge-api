"""Strict Phase 1 adapters. Exceptions leave obligations retryable."""

from django.core.exceptions import ValidationError

from .models import OutboxMessage
from .outbox import validate_message


def dispatch_message(claim):
    validate_message(kind=claim.kind, document_id=claim.document_id,
                     payload=claim.payload, schema_version=claim.schema_version)
    if claim.kind == OutboxMessage.Kind.UPLOAD_REQUESTED:
        from .tasks import store_document
        store_document.apply_async(
            kwargs={"document_id": claim.document_id, "event_id": str(claim.event_id)},
            task_id=str(claim.event_id),
        )
    elif claim.kind == OutboxMessage.Kind.OBJECT_DELETE:
        from .services import default_storage
        default_storage.delete(claim.payload["object_key"])
    elif claim.kind == OutboxMessage.Kind.STAGING_DELETE:
        from .background import staging_storage
        staging_storage().delete(claim.payload["staging_key"])
    else:
        raise ValidationError("Destination is not active in this phase.", code="unsupported_kind")
