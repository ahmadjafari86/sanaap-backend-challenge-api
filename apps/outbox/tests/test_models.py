from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase

from .. import models


class OutboxModelTests(TestCase):
    def setUp(self):
        from apps.outbox.services import enqueue_message
        self.enqueue = enqueue_message

    def message(self, **changes):
        arguments = {
            "kind": "document.upload_requested", "document_id": 42,
            "payload": {"attempt_number": 1}, "dedupe_key": "upload:42:1",
        }
        return self.enqueue(**{**arguments, **changes})

    def test_identical_dedupe_returns_same_event(self):
        first, duplicate = self.message(), self.message()
        self.assertEqual(first.pk, duplicate.pk)
        self.assertEqual(models.OutboxMessage.objects.count(), 1)
        self.assertEqual((first.status, first.attempts, first.schema_version), ("PENDING", 0, 1))

    def test_dedupe_content_mismatch_fails_without_breaking_outer_transaction(self):
        first = self.message()
        with self.assertRaises(ValidationError):
            self.message(payload={"attempt_number": 2})
        models.OutboxReceipt.objects.create(event_id=first.pk)
        first.refresh_from_db()
        self.assertEqual(first.payload, {"attempt_number": 1})
        self.assertTrue(models.OutboxReceipt.objects.filter(event_id=first.pk).exists())

    def test_receipt_event_identity_is_unique(self):
        event = self.message()
        models.OutboxReceipt.objects.create(event_id=event.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.OutboxReceipt.objects.create(event_id=event.pk)
        self.assertEqual(models.OutboxReceipt.objects.count(), 1)


class OutboxEnqueueTransactionTests(TransactionTestCase):
    def setUp(self):
        self.assertTrue(hasattr(models, "OutboxMessage"), "Document obligations need a durable OutboxMessage")
        from apps.outbox.services import enqueue_message
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
