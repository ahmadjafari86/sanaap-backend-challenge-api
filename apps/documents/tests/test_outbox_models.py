from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase

from .. import models


class OutboxModelTests(TestCase):
    def setUp(self):
        self.assertTrue(hasattr(models, "OutboxMessage"), "Document obligations need a durable OutboxMessage")
        from ..outbox import enqueue_message
        self.enqueue = enqueue_message
        self.user = get_user_model().objects.create_user(username="outbox-models")

    def message(self, **changes):
        arguments = {
            "kind": "document.upload_requested", "document_id": 42,
            "payload": {"attempt_number": 1}, "dedupe_key": "upload:42:1",
        }
        return self.enqueue(**{**arguments, **changes})

    def document(self):
        return models.Document.objects.create(
            title="Atomic", creator=self.user, object_key="documents/atomic.png", file_size=1,
        )

    def test_mutation_and_message_rollback_together(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                document = self.document()
                models.DocumentAuditEvent.objects.create(
                    document_id=document.pk, action="create", outcome="success", status_code=202,
                )
                self.message(document_id=document.pk)
                raise RuntimeError("rollback")
        self.assertFalse(models.Document.objects.exists())
        self.assertFalse(models.DocumentAuditEvent.objects.exists())
        self.assertFalse(models.OutboxMessage.objects.exists())

    def test_identical_dedupe_returns_same_event(self):
        first, duplicate = self.message(), self.message()
        self.assertEqual(first.pk, duplicate.pk)
        self.assertEqual(models.OutboxMessage.objects.count(), 1)
        self.assertEqual((first.status, first.attempts, first.schema_version), ("PENDING", 0, 1))

    def test_dedupe_content_mismatch_fails_without_breaking_outer_transaction(self):
        first = self.message()
        with self.assertRaises(ValidationError):
            self.message(payload={"attempt_number": 2})
        self.document()
        first.refresh_from_db()
        self.assertEqual(first.payload, {"attempt_number": 1})
        self.assertTrue(models.Document.objects.exists())

    def test_document_deletion_preserves_message(self):
        document = self.document()
        document_id = document.pk
        event = self.message(document_id=document_id)
        document.delete()
        event.refresh_from_db()
        self.assertEqual(event.document_id, document_id)

    def test_unknown_kind_version_or_private_payload_rejected(self):
        invalid = [
            {"kind": "unknown"},
            {"payload": {"attempt_number": 1, "token": "private"}},
            {"payload": {"attempt_number": True}},
            {"payload": {"attempt_number": 0}},
            {"payload": {"attempt_number": 5}},
            {"document_id": True}, {"document_id": None},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.message(**changes)
        event = models.OutboxMessage(
            kind="document.upload_requested", document_id=42, payload={"attempt_number": 1},
            dedupe_key="unknown-version", schema_version=2,
        )
        with self.assertRaises(ValidationError):
            event.full_clean()
        self.assertFalse(models.OutboxMessage.objects.exists())

    def test_cleanup_payload_rejects_staging_traversal_and_unexpected_fields(self):
        for payload in ({"staging_key": "../private"}, {"staging_key": "a" * 32 + ".stage", "url": "private"}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                self.message(kind="storage.staging_delete_requested", payload=payload)
        event = self.message(
            kind="storage.staging_delete_requested", document_id=None,
            payload={"staging_key": "a" * 32 + ".stage"},
        )
        self.assertIsNone(event.document_id)

    def test_receipt_event_identity_is_unique(self):
        event = self.message()
        models.OutboxReceipt.objects.create(event_id=event.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.OutboxReceipt.objects.create(event_id=event.pk)
        self.assertEqual(models.OutboxReceipt.objects.count(), 1)


class OutboxEnqueueTransactionTests(TransactionTestCase):
    def setUp(self):
        self.assertTrue(hasattr(models, "OutboxMessage"), "Document obligations need a durable OutboxMessage")
        from ..outbox import enqueue_message
        self.enqueue = enqueue_message

    def test_enqueue_requires_the_mutation_transaction(self):
        with self.assertRaises(RuntimeError):
            self.enqueue(kind="document.upload_requested", document_id=42,
                         payload={"attempt_number": 1}, dedupe_key="upload:42:1")
        self.assertFalse(models.OutboxMessage.objects.exists())

    def test_concurrent_dedupe_is_safe_and_keeps_transactions_usable(self):
        barrier = Barrier(2)

        def insert():
            close_old_connections()
            try:
                with transaction.atomic():
                    barrier.wait(timeout=5)
                    event = self.enqueue(kind="document.upload_requested", document_id=42,
                                         payload={"attempt_number": 1}, dedupe_key="upload:42:1")
                    self.assertEqual(models.OutboxMessage.objects.filter(pk=event.pk).count(), 1)
                    return event.pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: insert(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(models.OutboxMessage.objects.count(), 1)
