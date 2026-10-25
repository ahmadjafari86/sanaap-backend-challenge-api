from datetime import datetime, timezone
from types import SimpleNamespace

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import SimpleTestCase


class RegistryTests(SimpleTestCase):
    def setUp(self):
        from apps.outbox.registry import Registry

        self.registry = Registry()
        self.delivered = []

    def validate(self, *, document_id, payload):
        if document_id != 42 or payload != {"attempt_number": 1}:
            raise ValidationError("Invalid fixture payload.")

    def deliver(self, claim):
        self.delivered.append(claim.document_id)

    def claim(self, **changes):
        return SimpleNamespace(**{
            "kind": "document.upload_requested", "document_id": 42,
            "payload": {"attempt_number": 1}, **changes,
        })

    def register(self):
        self.registry.register_event(
            "document.upload_requested", validator=self.validate, handler=self.deliver,
        )

    def test_dispatch_validates_before_delivering(self):
        self.register()
        self.registry.dispatch(self.claim())
        self.assertEqual(self.delivered, [42])
        with self.assertRaises(ValidationError):
            self.registry.dispatch(self.claim(payload={"attempt_number": 5}))
        self.assertEqual(self.delivered, [42])

    def test_unknown_kind_never_delivers(self):
        self.register()
        with self.assertRaises(ValidationError):
            self.registry.dispatch(self.claim(kind="unknown"))
        self.assertEqual(self.delivered, [])

    def test_reserved_event_validates_but_cannot_deliver(self):
        self.registry.register_event("document.upload_requested", validator=self.validate)
        self.registry.validate_event(kind="document.upload_requested", document_id=42,
                                     payload={"attempt_number": 1})
        with self.assertRaises(ValidationError):
            self.registry.dispatch(self.claim())

    def test_registration_is_repeatable_but_conflicting_handler_is_rejected(self):
        self.register()
        self.register()
        with self.assertRaises(ImproperlyConfigured):
            self.registry.register_event("document.upload_requested", validator=self.validate)
        self.registry.dispatch(self.claim())
        self.assertEqual(self.delivered, [42])

    def test_adapter_callbacks_receive_time_and_limit_and_extend_status(self):
        now = datetime(2026, 10, 25, tzinfo=timezone.utc)
        recovered = []

        def recover(*, now, limit):
            recovered.append((now, limit))
            return 2

        def status(*, now):
            return {"oldest_unreceipted_age_seconds": int(now.timestamp())}

        self.registry.register_adapter("documents", recover=recover, status=status)
        self.registry.register_adapter("documents", recover=recover, status=status)
        self.assertEqual(self.registry.run_recovery(now=now, limit=100), 2)
        self.assertEqual(recovered, [(now, 100)])
        self.assertEqual(self.registry.get_status(now=now), {
            "oldest_unreceipted_age_seconds": 1792886400,
        })
        with self.assertRaises(ImproperlyConfigured):
            self.registry.register_adapter("documents", recover=lambda **_: 0, status=status)

    def test_status_callbacks_cannot_silently_overwrite_each_other(self):
        self.registry.register_adapter("first", recover=lambda **_: 0, status=lambda **_: {"age": 1})
        self.registry.register_adapter("second", recover=lambda **_: 0, status=lambda **_: {"age": 2})
        with self.assertRaises(ImproperlyConfigured):
            self.registry.get_status(now=datetime(2026, 10, 25, tzinfo=timezone.utc))
