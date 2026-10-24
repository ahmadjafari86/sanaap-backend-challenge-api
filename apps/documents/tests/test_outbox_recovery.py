from datetime import timedelta
from io import StringIO
import importlib.util
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from ..models import Document, DocumentAuditEvent, OutboxMessage, OutboxReceipt
from ..outbox import enqueue_message


class OutboxRecoveryTests(TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("apps.documents.outbox_recovery"), "Published tasks without receipts need recovery")
        from ..outbox_recovery import recover_uploads
        self.recover = recover_uploads
        self.now = timezone.now()
        self.user = get_user_model().objects.create_user(username="outbox-recovery")
        self.document = Document.objects.create(
            title="Recover", creator=self.user, object_key="documents/recover.png", file_size=1,
            status="PENDING", staging_key="a" * 32 + ".stage",
        )

    def event(self):
        event = enqueue_message(kind="document.upload_requested", document_id=self.document.pk,
                                payload={"attempt_number": 1}, dedupe_key=f"upload:{self.document.pk}:1")
        OutboxMessage.objects.filter(pk=event.pk).update(
            status="PUBLISHED", published_at=self.now - timedelta(seconds=301), attempts=1,
        )
        return event

    def test_lost_published_task_is_rearmed_with_same_identity_and_attempt_budget(self):
        event = self.event()
        self.assertEqual(self.recover(now=self.now, limit=100), 1)
        event.refresh_from_db()
        self.document.refresh_from_db()
        self.assertEqual((event.status, event.attempts, event.payload), ("PENDING", 1, {"attempt_number": 1}))
        self.assertEqual((self.document.status, self.document.storage_attempts), ("PENDING", 0))
        self.assertEqual(self.document.staging_key, "a" * 32 + ".stage")
        self.assertEqual(OutboxMessage.objects.count(), 1)

    def test_legacy_seeding_uses_remaining_attempt_and_deduplicates(self):
        Document.objects.filter(pk=self.document.pk).update(storage_attempts=2)
        self.assertEqual(self.recover(now=self.now, limit=100), 1)
        self.assertEqual(self.recover(now=self.now, limit=100), 0)
        self.assertEqual(OutboxMessage.objects.get().payload, {"attempt_number": 3})

    def test_receipted_terminal_and_deleted_documents_are_not_resurrected(self):
        event = self.event()
        OutboxReceipt.objects.create(event_id=event.pk)
        Document.objects.filter(pk=self.document.pk).update(status="READY")
        self.assertEqual(self.recover(now=self.now, limit=100), 0)
        self.document.delete()
        self.assertEqual(self.recover(now=self.now, limit=100), 0)
        event.refresh_from_db()
        self.assertEqual(event.status, "PUBLISHED")

    def test_dead_publication_is_not_automatically_replayed_or_replaced(self):
        event = self.event()
        OutboxMessage.objects.filter(pk=event.pk).update(status="DEAD")
        self.assertEqual(self.recover(now=self.now, limit=100), 0)
        self.assertEqual(OutboxMessage.objects.count(), 1)

    def test_dry_run_and_recent_publication_do_not_modify_work(self):
        event = self.event()
        self.assertEqual(self.recover(now=self.now, limit=100, dry_run=True), 1)
        event.refresh_from_db()
        self.assertEqual(event.status, "PUBLISHED")
        OutboxMessage.objects.filter(pk=event.pk).update(published_at=self.now)
        self.assertEqual(self.recover(now=self.now, limit=100), 0)

    def test_relay_initial_recovery_works_while_broker_is_down(self):
        with patch("apps.documents.tasks.store_document.apply_async", side_effect=OSError("offline")):
            call_command("run_outbox_relay", "--once", stdout=StringIO())
        self.assertEqual(OutboxMessage.objects.get().status, "PENDING")
        self.assertFalse(DocumentAuditEvent.objects.exists())

    def test_old_requeue_command_creates_durable_work_without_contacting_broker(self):
        with patch("apps.documents.tasks.store_document.delay", side_effect=AssertionError("must enqueue")):
            call_command("requeue_pending_documents", stdout=StringIO())
        self.assertEqual(OutboxMessage.objects.get().payload, {"attempt_number": 1})
