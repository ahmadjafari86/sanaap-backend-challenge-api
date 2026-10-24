from datetime import timedelta
from io import BytesIO
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from .. import background
from ..models import DocumentAuditEvent, OutboxMessage, OutboxReceipt
from ..serializers import DocumentUploadSerializer


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class OutboxUploadTests(TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("apps.documents.outbox_uploads"), "Uploads need processing receipts and durable attempts")
        from .. import outbox_uploads
        self.uploads = outbox_uploads
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        setting = override_settings(DOCUMENT_STAGING_ROOT=self.directory.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.user = get_user_model().objects.create_user(username="outbox-upload", role="editor")
        self.storage = InMemoryStorage()
        storage_patch = patch("apps.documents.services.default_storage", self.storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)

    def accept(self):
        image = BytesIO()
        Image.new("RGB", (2, 2), "red").save(image, format="PNG")
        self.content = image.getvalue()
        serializer = DocumentUploadSerializer(data={
            "title": "Upload", "file": SimpleUploadedFile("private.png", self.content, content_type="image/png"),
        })
        serializer.is_valid(raise_exception=True)
        document = background.stage_document(title="Upload", uploaded_file=serializer.validated_data["file"], creator=self.user)
        event = OutboxMessage.objects.get(kind="document.upload_requested", document_id=document.pk)
        return document, event

    def process(self, document, event):
        with self.captureOnCommitCallbacks(execute=True):
            self.uploads.process_outbox_upload(document_id=document.pk, event_id=event.pk)

    def test_acceptance_persists_obligation_and_stage_without_broker_dependency(self):
        with patch("apps.documents.tasks.store_document.apply_async", side_effect=OSError("offline")):
            with self.captureOnCommitCallbacks(execute=True):
                document, event = self.accept()
        document.refresh_from_db()
        self.assertEqual((document.status, event.payload), ("PENDING", {"attempt_number": 1}))
        self.assertTrue((Path(self.directory.name) / document.staging_key).exists())
        self.assertEqual(list(DocumentAuditEvent.objects.values_list("action", "outcome")), [("create", "success")])

    def test_duplicate_and_legacy_task_delivery_store_and_audit_once(self):
        from ..tasks import store_document
        document, event = self.accept()
        with self.captureOnCommitCallbacks(execute=True):
            store_document.apply(args=[document.pk], throw=True)
        self.process(document, event)
        document.refresh_from_db()
        self.assertEqual((document.status, document.storage_attempts), ("READY", 1))
        self.assertEqual(self.storage.open(document.object_key).read(), self.content)
        self.assertEqual(OutboxReceipt.objects.filter(event_id=event.pk).count(), 1)
        self.assertEqual(DocumentAuditEvent.objects.filter(action="store").count(), 1)
        self.assertEqual(OutboxMessage.objects.filter(kind="storage.staging_delete_requested").count(), 1)
        self.assertFalse(OutboxMessage.objects.filter(kind__in=["document.notification_requested", "document.metadata_invalidate_requested"]).exists())

    def test_storage_retry_receipt_and_next_message_commit_together(self):
        document, event = self.accept()
        with patch.object(self.storage, "save", side_effect=OSError("offline")):
            self.process(document, event)
        self.process(document, event)
        document.refresh_from_db()
        next_event = OutboxMessage.objects.get(document_id=document.pk, payload={"attempt_number": 2})
        self.assertEqual((document.status, document.storage_attempts), ("PENDING", 1))
        self.assertTrue(OutboxReceipt.objects.filter(event_id=event.pk).exists())
        self.assertGreater(next_event.available_at, event.created_at)
        self.assertTrue((Path(self.directory.name) / document.staging_key).exists())
        self.assertFalse(DocumentAuditEvent.objects.filter(action="store").exists())

    def test_four_committed_storage_failures_are_terminal_without_broker_retry(self):
        document, event = self.accept()
        with patch.object(self.storage, "save", side_effect=OSError("offline")):
            for number in range(1, 5):
                event = OutboxMessage.objects.get(document_id=document.pk, payload={"attempt_number": number})
                OutboxMessage.objects.filter(pk=event.pk).update(available_at=timezone.now())
                self.process(document, event)
        document.refresh_from_db()
        self.assertEqual((document.status, document.storage_attempts), ("FAILED", 4))
        self.assertEqual(OutboxReceipt.objects.count(), 4)
        self.assertEqual(DocumentAuditEvent.objects.filter(action="store", outcome="failure").count(), 1)

    def test_future_attempt_is_not_executed_early(self):
        document, event = self.accept()
        OutboxMessage.objects.filter(pk=event.pk).update(available_at=timezone.now() + timedelta(seconds=60))
        self.process(document, event)
        document.refresh_from_db()
        self.assertEqual((document.status, document.storage_attempts), ("PENDING", 0))
        self.assertFalse(OutboxReceipt.objects.exists())

    def test_receipt_failure_rolls_back_ready_audit_cleanup_and_compensates(self):
        document, event = self.accept()
        with patch("apps.documents.outbox_uploads.OutboxReceipt.objects.create", side_effect=IntegrityError("private")):
            with self.assertRaises(IntegrityError):
                self.process(document, event)
        document.refresh_from_db()
        self.assertEqual((document.status, document.storage_attempts), ("PENDING", 0))
        self.assertFalse(self.storage.exists(document.object_key))
        self.assertFalse(OutboxReceipt.objects.exists())
        self.assertFalse(DocumentAuditEvent.objects.filter(action="store").exists())
        self.assertFalse(OutboxMessage.objects.filter(kind="storage.staging_delete_requested").exists())

    def test_retry_enqueue_failure_rolls_back_attempt_and_receipt(self):
        document, event = self.accept()
        with patch.object(self.storage, "save", side_effect=OSError("offline")):
            with patch("apps.documents.outbox_uploads.enqueue_message", side_effect=IntegrityError("private")):
                with self.assertRaises(IntegrityError):
                    self.process(document, event)
        document.refresh_from_db()
        self.assertEqual(document.storage_attempts, 0)
        self.assertFalse(OutboxReceipt.objects.exists())

    def test_completed_worker_fences_a_late_publisher_failure(self):
        from ..outbox_delivery import _finish, claim_batch
        document, event = self.accept()
        claim = claim_batch(now=timezone.now(), limit=1)[0]
        self.process(document, event)
        self.assertFalse(_finish(claim, now=timezone.now(), error=OSError("late failure")))
        event.refresh_from_db()
        self.assertEqual(event.status, "PUBLISHED")
        self.assertTrue(OutboxReceipt.objects.filter(event_id=event.pk).exists())
