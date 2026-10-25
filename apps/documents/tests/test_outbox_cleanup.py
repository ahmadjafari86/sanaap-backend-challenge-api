from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, OperationalError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image

from .. import background, services
from apps.outbox.models import OutboxMessage
from ..models import Document, DocumentAuditEvent
from apps.outbox.services import enqueue_message
from apps.outbox.delivery import relay_once
from ..serializers import DocumentUploadSerializer


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class OutboxCleanupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="outbox-cleanup", role="admin")
        self.storage = InMemoryStorage()
        storage_patch = patch("apps.documents.services.default_storage", self.storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        self.document = Document.objects.create(
            title="Cleanup", creator=self.user, object_key="documents/old.png", file_size=3,
        )
        self.storage.save(self.document.object_key, ContentFile(b"old"))

    def replacement(self):
        image = BytesIO()
        Image.new("RGB", (2, 2), "blue").save(image, format="PNG")
        serializer = DocumentUploadSerializer(data={"title": "Replacement", "file": SimpleUploadedFile(
            "private.png", image.getvalue(), content_type="image/png")})
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data["file"]

    def test_delete_commits_a_cleanup_snapshot_independent_of_document_lifetime(self):
        old_key, document_id = self.document.object_key, self.document.pk
        with self.captureOnCommitCallbacks(execute=True):
            services.delete_document(document_id=document_id, actor_id=self.user.pk)
        self.assertFalse(Document.objects.exists())
        message = OutboxMessage.objects.get(kind="storage.object_delete_requested")
        self.assertEqual((message.document_id, message.payload), (document_id, {"object_key": old_key}))
        self.assertTrue(self.storage.exists(old_key))
        relay_once()
        self.assertFalse(self.storage.exists(old_key))

    def test_replacement_cleanup_survives_delay_and_cannot_remove_new_key(self):
        old_key = self.document.object_key
        with self.captureOnCommitCallbacks(execute=True):
            current = services.update_document(document_id=self.document.pk, uploaded_file=self.replacement())
        self.assertTrue(self.storage.exists(old_key))
        message = OutboxMessage.objects.get(kind="storage.object_delete_requested")
        self.assertEqual(message.payload, {"object_key": old_key})
        relay_once()
        self.assertFalse(self.storage.exists(old_key))
        self.assertTrue(self.storage.exists(current.object_key))

    def test_cleanup_failure_retries_after_metadata_deletion_and_missing_file_is_success(self):
        old_key = self.document.object_key
        services.delete_document(document_id=self.document.pk)
        with patch.object(self.storage, "delete", side_effect=OSError("private")):
            relay_once()
        message = OutboxMessage.objects.get(kind="storage.object_delete_requested")
        self.assertEqual((message.status, message.last_error_code), ("PENDING", "io_error"))
        self.assertTrue(self.storage.exists(old_key))
        OutboxMessage.objects.filter(pk=message.pk).update(available_at=timezone.now())
        relay_once()
        self.assertFalse(self.storage.exists(old_key))
        OutboxMessage.objects.filter(pk=message.pk).update(status="PENDING")
        relay_once()
        message.refresh_from_db()
        self.assertEqual(message.status, "PUBLISHED")

    def test_rollback_preserves_file_and_discards_cleanup_and_audit(self):
        with self.assertRaises(RuntimeError), transaction.atomic():
            services.delete_document(document_id=self.document.pk)
            raise RuntimeError("rollback")
        self.assertTrue(self.storage.exists(self.document.object_key))
        self.assertTrue(Document.objects.filter(pk=self.document.pk).exists())
        self.assertFalse(OutboxMessage.objects.exists())
        self.assertFalse(DocumentAuditEvent.objects.exists())

    def test_cleanup_enqueue_failure_rolls_back_deletion_and_audit(self):
        with patch("apps.documents.services.enqueue_message", side_effect=IntegrityError("private"), create=True):
            with self.assertRaises(IntegrityError):
                services.delete_document(document_id=self.document.pk)
        self.assertTrue(Document.objects.exists())
        self.assertTrue(self.storage.exists(self.document.object_key))
        self.assertFalse(DocumentAuditEvent.objects.exists())

    def test_late_compensation_message_preserves_a_current_ready_or_pending_key(self):
        for status in ("PENDING", "READY"):
            Document.objects.filter(pk=self.document.pk).update(status=status)
            enqueue_message(kind="storage.object_delete_requested", document_id=self.document.pk,
                            payload={"object_key": self.document.object_key}, dedupe_key=f"compensate:{status}")
            relay_once()
            self.assertTrue(self.storage.exists(self.document.object_key))
        # A real deletion has a distinct origin key, so an earlier skipped
        # compensation message cannot suppress the final cleanup obligation.
        services.delete_document(document_id=self.document.pk)
        relay_once()
        self.assertFalse(self.storage.exists(self.document.object_key))

    def test_failed_compensation_is_persisted_then_rechecks_new_ownership(self):
        Document.objects.filter(pk=self.document.pk).update(status="PENDING")
        with patch.object(self.storage, "delete", side_effect=OSError("private")):
            background._compensate_attempt(self.document.pk, self.document.object_key)
        message = OutboxMessage.objects.get(kind="storage.object_delete_requested")
        Document.objects.filter(pk=self.document.pk).update(status="READY")
        relay_once()
        message.refresh_from_db()
        self.assertEqual(message.status, "PUBLISHED")
        self.assertTrue(self.storage.exists(self.document.object_key))

    def test_uncertain_commit_compensation_preserves_a_ready_reference(self):
        # The database may have committed even when its acknowledgement failed.
        services._delete_document_object(self.document.object_key)
        self.assertTrue(self.storage.exists(self.document.object_key))

    def test_compensation_defers_when_database_ownership_cannot_be_verified(self):
        with patch("apps.documents.services.Document.objects.filter", side_effect=OperationalError("private")):
            services._delete_document_object(self.document.object_key)
        self.assertTrue(self.storage.exists(self.document.object_key))

    def test_staged_deletion_retries_and_preserves_an_active_upload(self):
        with TemporaryDirectory() as directory, override_settings(DOCUMENT_STAGING_ROOT=directory):
            key = "a" * 32 + ".stage"
            path = Path(directory) / key
            path.write_bytes(b"private")
            Document.objects.filter(pk=self.document.pk).update(status="PENDING", staging_key=key)
            enqueue_message(kind="storage.staging_delete_requested", document_id=self.document.pk,
                            payload={"staging_key": key}, dedupe_key="staging-compensation")
            relay_once()
            self.assertTrue(path.exists())
            services.delete_document(document_id=self.document.pk)
            with patch("django.core.files.storage.FileSystemStorage.delete", side_effect=OSError("offline")):
                relay_once()
            message = OutboxMessage.objects.filter(kind="storage.staging_delete_requested", status="PENDING").get()
            OutboxMessage.objects.filter(pk=message.pk).update(available_at=timezone.now())
            relay_once()
            self.assertFalse(path.exists())

    def assert_failed_upload_replaces_skipped_compensation(self, storage_attempts):
        from ..outbox.uploads import process_outbox_upload, resolve_upload_message

        with TemporaryDirectory() as directory, override_settings(DOCUMENT_STAGING_ROOT=directory):
            Document.objects.filter(pk=self.document.pk).update(
                status="PENDING", staging_key="c" * 32 + ".stage", storage_attempts=storage_attempts,
            )
            compensation = enqueue_message(
                kind="storage.object_delete_requested", document_id=self.document.pk,
                payload={"object_key": self.document.object_key}, dedupe_key="partial-put-compensation",
            )
            relay_once()
            compensation.refresh_from_db()
            self.assertEqual(compensation.status, "PUBLISHED")
            self.assertTrue(self.storage.exists(self.document.object_key))

            event = resolve_upload_message(document_id=self.document.pk, event_id=None)
            process_outbox_upload(document_id=self.document.pk, event_id=event.pk)
            self.document.refresh_from_db()
            self.assertEqual(self.document.status, "FAILED")
            self.assertEqual(DocumentAuditEvent.objects.filter(action="store", outcome="failure").count(), 1)
            relay_once()
            self.assertFalse(self.storage.exists(self.document.object_key))

    def test_missing_staging_failure_cleans_partial_object_after_skipped_compensation(self):
        self.assert_failed_upload_replaces_skipped_compensation(storage_attempts=1)

    def test_exhausted_legacy_upload_cleans_partial_object_after_skipped_compensation(self):
        self.assert_failed_upload_replaces_skipped_compensation(storage_attempts=4)

    def test_staging_cleanup_is_real_and_missing_file_replay_succeeds(self):
        with TemporaryDirectory() as directory, override_settings(DOCUMENT_STAGING_ROOT=directory):
            key = "a" * 32 + ".stage"
            path = Path(directory) / key
            path.write_bytes(b"private image")
            event = enqueue_message(dedupe_key="staging-replay", document_id=None, kind="storage.staging_delete_requested", payload={"staging_key": key})
            relay_once()
            self.assertFalse(path.exists())
            OutboxMessage.objects.filter(pk=event.pk).update(status="PENDING")
            relay_once()
            event.refresh_from_db()
            self.assertEqual(event.status, "PUBLISHED")

@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class OutboxAcceptanceCompensationTests(TransactionTestCase):
    def test_committed_acceptance_preserves_staging_after_lost_commit_acknowledgement(self):
        user = get_user_model().objects.create_user(username="outbox-commit-ack", role="editor")
        image = BytesIO()
        Image.new("RGB", (2, 2), "blue").save(image, format="PNG")
        serializer = DocumentUploadSerializer(data={"title": "Accepted", "file": SimpleUploadedFile(
            "private.png", image.getvalue(), content_type="image/png")})
        serializer.is_valid(raise_exception=True)
        atomic, depth = transaction.atomic, 0

        @contextmanager
        def commit_then_disconnect(*args, **kwargs):
            nonlocal depth
            depth += 1
            try:
                with atomic(*args, **kwargs):
                    yield
            finally:
                depth -= 1
            if depth == 0:
                raise OperationalError("commit acknowledgement lost")

        with TemporaryDirectory() as directory, override_settings(DOCUMENT_STAGING_ROOT=directory):
            with patch("apps.documents.background.transaction.atomic", commit_then_disconnect):
                with self.assertRaises(OperationalError):
                    background.stage_document(title="Accepted", uploaded_file=serializer.validated_data["file"], creator=user)
            document = Document.objects.get()
            self.assertEqual(document.status, "PENDING")
            self.assertTrue(OutboxMessage.objects.filter(document_id=document.pk, kind="document.upload_requested").exists())
            self.assertEqual((Path(directory) / document.staging_key).read_bytes(), image.getvalue())

    def test_immediate_staging_compensation_defers_when_ownership_lookup_is_unavailable(self):
        with TemporaryDirectory() as directory, override_settings(DOCUMENT_STAGING_ROOT=directory):
            key = "d" * 32 + ".stage"
            path = Path(directory) / key
            path.write_bytes(b"private")
            with patch("apps.documents.background.Document.objects.filter", side_effect=OperationalError("private")):
                self.assertFalse(background.delete_staged_file(key))
            self.assertTrue(path.exists())
