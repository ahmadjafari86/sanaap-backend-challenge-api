"""Explicit in-process wiring supplied by domain apps during initialization."""

from dataclasses import dataclass
from typing import Callable

from django.core.exceptions import ImproperlyConfigured, ValidationError


@dataclass(frozen=True)
class Event:
    validator: Callable
    handler: Callable | None


@dataclass(frozen=True)
class Adapter:
    recover: Callable
    status: Callable


class Registry:
    def __init__(self):
        self._events = {}
        self._adapters = {}

    @staticmethod
    def _register(mapping, key, value):
        if key in mapping and mapping[key] != value:
            raise ImproperlyConfigured(f"Conflicting outbox registration: {key}.")
        mapping[key] = value

    def register_event(self, kind: str, *, validator, handler=None) -> None:
        self._register(self._events, kind, Event(validator, handler))

    def _event(self, kind):
        try:
            return self._events[kind]
        except KeyError:
            raise ValidationError("Unknown outbox kind.", code="invalid_payload") from None

    def validate_event(self, *, kind, document_id, payload) -> None:
        self._event(kind).validator(document_id=document_id, payload=payload)

    def dispatch(self, claim) -> None:
        self.validate_event(kind=claim.kind, document_id=claim.document_id, payload=claim.payload)
        handler = self._event(claim.kind).handler
        if handler is None:
            raise ValidationError("Destination is not active in this phase.", code="unsupported_kind")
        handler(claim)

    def register_adapter(self, name: str, *, recover, status) -> None:
        self._register(self._adapters, name, Adapter(recover, status))

    def run_recovery(self, *, now, limit) -> int:
        return sum(adapter.recover(now=now, limit=limit) for adapter in self._adapters.values())

    def get_status(self, *, now) -> dict:
        result = {}
        for adapter in self._adapters.values():
            values = adapter.status(now=now)
            if result.keys() & values.keys():
                raise ImproperlyConfigured("Conflicting outbox status fields.")
            result.update(values)
        return result


registry = Registry()
