import importlib.util
import json
from io import StringIO
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from ..models import Document, OutboxMessage, OutboxReceipt
from ..outbox import enqueue_message


class OutboxOperationsTests(TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("apps.documents.outbox_operations"), "DEAD messages need inspection and explicit replay")
        from .. import outbox_operations
        self.operations = outbox_operations
        self.now = timezone.now()
        self.event = enqueue_message(kind="document.upload_requested", document_id=42,
                                     payload={"attempt_number": 1}, dedupe_key="upload:42:1")

    def test_explicit_dead_replay_preserves_identity_receipt_and_document_state(self):
        OutboxMessage.objects.filter(pk=self.event.pk).update(status="DEAD", attempts=20)
        OutboxReceipt.objects.create(event_id=self.event.pk)
        self.assertEqual(self.operations.replay_dead_messages(event_ids=[self.event.pk], now=self.now), 1)
        self.event.refresh_from_db()
        self.assertEqual((self.event.status, self.event.attempts, self.event.available_at), ("PENDING", 0, self.now))
        self.assertTrue(OutboxReceipt.objects.filter(event_id=self.event.pk).exists())
        self.assertFalse(Document.objects.exists())

    def test_replay_rejects_active_or_missing_events_without_partial_changes(self):
        with self.assertRaises(ValidationError):
            self.operations.replay_dead_messages(event_ids=[self.event.pk], now=self.now)
        OutboxMessage.objects.filter(pk=self.event.pk).update(status="DEAD")
        with self.assertRaises(ValidationError):
            self.operations.replay_dead_messages(event_ids=[self.event.pk, uuid4()], now=self.now)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, "DEAD")

    def test_status_and_commands_show_ids_and_counts_without_payloads(self):
        OutboxMessage.objects.filter(pk=self.event.pk).update(status="DEAD", last_error_code="io_error")
        stream = StringIO()
        call_command("outbox_status", stdout=stream)
        status = json.loads(stream.getvalue())
        self.assertEqual(status["counts"]["DEAD"], 1)
        self.assertEqual(status["dead_event_ids"], [str(self.event.pk)])
        self.assertNotIn("attempt_number", stream.getvalue())
        call_command("replay_outbox", str(self.event.pk), stdout=StringIO())
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, "PENDING")
