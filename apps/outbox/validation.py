"""Validate the envelope before delegating payload rules to its domain."""

from django.core.exceptions import ValidationError

from .registry import registry


def validate_message(*, kind, document_id, payload, schema_version=1):
    if type(schema_version) is not int or schema_version != 1:
        raise ValidationError("Unsupported outbox schema version.", code="invalid_schema")
    if document_id is not None and (type(document_id) is not int or not 0 < document_id <= 2**63 - 1):
        raise ValidationError("Invalid document snapshot.", code="invalid_document")
    if not isinstance(kind, str) or not isinstance(payload, dict):
        raise ValidationError("Invalid outbox payload.", code="invalid_payload")
    registry.validate_event(kind=kind, document_id=document_id, payload=payload)
