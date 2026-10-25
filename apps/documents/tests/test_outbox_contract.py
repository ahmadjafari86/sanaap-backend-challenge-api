from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase

from apps.outbox import models
from ..models import Document, DocumentAuditEvent


class DocumentOutboxContractTests(TestCase):
    def setUp(self):
        self.assertTrue(hasattr(models, "OutboxMessage"), "Document obligations need a durable OutboxMessage")
        from apps.outbox.services import enqueue_message
        self.enqueue = enqueue_message
        self.user = get_user_model().objects.create_user(username="outbox-models")

    def message(self, **changes):
        arguments = {
            "kind": "document.upload_requested", "document_id": 42,
            "payload": {"attempt_number": 1}, "dedupe_key": "upload:42:1",
        }
        return self.enqueue(**{**arguments, **changes})

    def document(self):
        return Document.objects.create(
            title="Atomic", creator=self.user, object_key="documents/atomic.png", file_size=1,
        )

    def test_mutation_and_message_rollback_together(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                document = self.document()
                DocumentAuditEvent.objects.create(
                    document_id=document.pk, action="create", outcome="success", status_code=202,
                )
                self.message(document_id=document.pk)
                raise RuntimeError("rollback")
        self.assertFalse(Document.objects.exists())
        self.assertFalse(DocumentAuditEvent.objects.exists())
        self.assertFalse(models.OutboxMessage.objects.exists())

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
