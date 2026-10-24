from io import BytesIO, StringIO
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from threading import Event

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import FileSystemStorage, InMemoryStorage
from django.db import IntegrityError, OperationalError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.test import SimpleTestCase
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase

from ..models import Document, DocumentAuditEvent, OutboxMessage, OutboxReceipt
from .outbox_helpers import deliver_outbox_once
from .. import background
from ..serializers import DocumentUploadSerializer


class StorageInitializationTests(SimpleTestCase):
    def test_startup_initializes_bucket_for_the_configured_minio_subclass(self):
        from django.core.management import call_command
        from ..storage import DocumentStorage
        class Client:
            created = False
            def bucket_exists(self, bucket_name):
                return self.created
            def make_bucket(self, bucket_name):
                self.created = True
        client = Client()
        with patch.object(DocumentStorage, "_create_new_client", return_value=client):
            call_command("initialize_document_storage", stdout=StringIO())
        self.assertTrue(client.created)


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class BackgroundAcceptanceTests(APITestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        setting = override_settings(DOCUMENT_STAGING_ROOT=self.directory.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.editor = get_user_model().objects.create_user(username="background-editor", role="editor")
        self.client.force_authenticate(self.editor)

    def upload(self):
        buffer = BytesIO()
        Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
        return SimpleUploadedFile("private-name.png", buffer.getvalue(), content_type="image/png")

    @patch("apps.documents.services.default_storage")
    def test_creation_accepts_pending_metadata_without_contacting_minio(self, storage):
        storage.save.side_effect = lambda name, content: name
        response = self.client.post("/api/v1/documents/", {"title": "Queued", "file": self.upload()}, format="multipart")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "PENDING")
        document = Document.objects.get(pk=response.data["id"])
        self.assertEqual(document.status, "PENDING")
        self.assertEqual((Path(self.directory.name) / document.staging_key).read_bytes(), self.upload().read())
        self.assertNotIn("staging_key", response.data)
        self.assertNotIn("storage_attempts", response.data)
        self.assertNotIn("object_key", response.data)
        storage.save.assert_not_called()
        event = DocumentAuditEvent.objects.get()
        self.assertEqual((event.action, event.outcome, event.status_code), ("create", "success", 202))

    @patch("apps.documents.services.default_storage")
    def test_client_cannot_choose_ready_status(self, storage):
        storage.save.side_effect = lambda name, content: name
        response = self.client.post("/api/v1/documents/", {"title": "Queued", "file": self.upload(), "status": "READY"}, format="multipart")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "PENDING")

    def test_metadata_exposes_legacy_documents_as_ready(self):
        doc = Document.objects.create(title="Legacy", creator=self.editor, object_key="documents/legacy.png", file_size=1)
        response = self.client.get(f"/api/v1/documents/{doc.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("status"), "READY")

    def test_nonready_download_and_update_return_conflict(self):
        self.assertIn("status", {field.name for field in Document._meta.fields})
        for state in ("PENDING", "FAILED"):
            doc = Document.objects.create(title="Unavailable", creator=self.editor, object_key=f"documents/{state}.png", file_size=1, status=state)
            for method, suffix, payload in (("get", "download/", {}), ("patch", "", {"title": "Changed"})):
                with self.subTest(state=state, method=method):
                    response = getattr(self.client, method)(f"/api/v1/documents/{doc.pk}/{suffix}", payload, format="json")
                    self.assertEqual(response.status_code, 409)
            self.assertEqual(self.client.get(f"/api/v1/documents/{doc.pk}/").data["status"], state)

    @patch("apps.documents.services.default_storage")
    def test_pending_file_replacement_is_rejected_before_storage(self, storage):
        doc = Document.objects.create(title="Pending", creator=self.editor, object_key="documents/pending.png", file_size=1, status="PENDING")
        storage.save.side_effect = OSError("offline")
        response = self.client.patch(f"/api/v1/documents/{doc.pk}/", {"file": self.upload()}, format="multipart")
        self.assertEqual(response.status_code, 409)
        storage.save.assert_not_called()


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class BackgroundWorkerTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        setting = override_settings(DOCUMENT_STAGING_ROOT=self.directory.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.editor = get_user_model().objects.create_user(username="worker-editor", role="editor")
        image = BytesIO()
        Image.new("RGB", (2, 2), "red").save(image, format="PNG")
        self.content = image.getvalue()
        self.storage = InMemoryStorage()
        storage_patch = patch("apps.documents.services.default_storage", self.storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)

    def accept(self):
        serializer = DocumentUploadSerializer(data={"title": "Queued", "file": SimpleUploadedFile("upload.png", self.content, content_type="image/png")})
        serializer.is_valid(raise_exception=True)
        return background.stage_document(title="Queued", uploaded_file=serializer.validated_data["file"], creator=self.editor)

    def process(self, document):
        # Explicitly advance the next persisted attempt in these bounded unit tests.
        OutboxMessage.objects.filter(document_id=document.pk, kind="document.upload_requested", status="PENDING").update(available_at=timezone.now())
        with self.captureOnCommitCallbacks(execute=True):
            retry = background.process_staged_document(document.pk)
        deliver_outbox_once()
        return retry

    def test_worker_stores_exact_bytes_and_atomically_marks_ready_and_audits(self):
        document = self.accept()
        old_version, stage = document.metadata_version, document.staging_key
        self.assertFalse(self.process(document))
        document.refresh_from_db()
        self.assertEqual(document.status, "READY")
        self.assertEqual(document.storage_attempts, 1)
        self.assertNotEqual(document.metadata_version, old_version)
        self.assertEqual(document.staging_key, "")
        self.assertFalse((Path(self.directory.name) / stage).exists())
        self.assertEqual(self.storage.open(document.object_key).read(), self.content)
        self.assertEqual(list(DocumentAuditEvent.objects.filter(action="store").values_list("actor_id", "outcome", "status_code")), [(self.editor.pk, "success", 200)])

    def test_duplicate_delivery_does_not_upload_again_or_duplicate_event(self):
        document = self.accept()
        self.process(document)
        with patch.object(self.storage, "save", side_effect=AssertionError("duplicate upload")):
            self.assertFalse(self.process(document))
        self.assertEqual(DocumentAuditEvent.objects.filter(action="store").count(), 1)

    def test_transient_failure_retains_stage_and_recovery_is_successful(self):
        document = self.accept()
        stage = document.staging_key
        with patch.object(self.storage, "save", side_effect=OSError("private-storage-details")), self.assertLogs("apps.documents.background", level="WARNING") as logs:
            self.assertTrue(self.process(document))
        document.refresh_from_db()
        self.assertEqual((document.status, document.storage_attempts), ("PENDING", 1))
        self.assertTrue((Path(self.directory.name) / stage).exists())
        self.assertEqual(DocumentAuditEvent.objects.filter(action="store").count(), 0)
        self.assertNotIn("private-storage-details", "\n".join(logs.output))
        self.process(document)
        document.refresh_from_db()
        self.assertEqual((document.status, document.storage_attempts), ("READY", 2))

    def test_four_failed_attempts_mark_failed_clean_stage_and_stop_redelivery(self):
        document = self.accept()
        stage = document.staging_key
        with patch.object(self.storage, "save", side_effect=OSError("private-storage-details")), self.assertLogs("apps.documents.background", level="WARNING"):
            self.assertEqual([self.process(document) for _ in range(4)], [True, True, True, False])
        document.refresh_from_db()
        self.assertEqual((document.status, document.storage_attempts), ("FAILED", 4))
        self.assertFalse((Path(self.directory.name) / stage).exists())
        self.assertFalse(self.process(document))
        self.assertEqual(list(DocumentAuditEvent.objects.filter(action="store").values_list("outcome", "status_code")), [("failure", 503)])

    def test_missing_staged_file_fails_without_retry(self):
        document = self.accept()
        (Path(self.directory.name) / document.staging_key).unlink()
        with self.assertLogs("apps.documents.background", level="WARNING"):
            self.assertFalse(self.process(document))
        document.refresh_from_db()
        self.assertEqual(document.status, "FAILED")
        self.assertFalse(self.storage.exists(document.object_key))

    def test_lost_upload_acknowledgement_cleans_object_before_retry(self):
        document = self.accept()
        save = self.storage.save
        def save_then_fail(name, content):
            save(name, content)
            raise OSError("lost acknowledgement")
        with patch.object(self.storage, "save", side_effect=save_then_fail), self.assertLogs("apps.documents.background", level="WARNING"):
            self.assertTrue(self.process(document))
        self.assertFalse(self.storage.exists(document.object_key))
        self.process(document)
        self.assertEqual(self.storage.listdir("documents")[1], [document.object_key.split("/")[1]])

    def test_audit_failure_rolls_back_ready_and_compensates_uploaded_object(self):
        document = self.accept()
        with patch("apps.documents.background.record_document_event", side_effect=IntegrityError("private-audit")), self.assertRaises(IntegrityError):
            self.process(document)
        document.refresh_from_db()
        self.assertEqual(document.status, "PENDING")
        self.assertTrue((Path(self.directory.name) / document.staging_key).exists())
        self.assertFalse(self.storage.exists(document.object_key))
        self.assertEqual(DocumentAuditEvent.objects.filter(action="store").count(), 0)

    def test_stage_is_kept_until_committed_cleanup_is_delivered(self):
        document = self.accept()
        stage = Path(self.directory.name) / document.staging_key
        with self.captureOnCommitCallbacks(execute=True):
            background.process_staged_document(document.pk)
            self.assertTrue(stage.exists())
        self.assertTrue(stage.exists())
        deliver_outbox_once()
        self.assertFalse(stage.exists())

    def test_acceptance_records_work_without_inline_publication(self):
        with patch("apps.documents.tasks.store_document.delay", side_effect=AssertionError("must enqueue")):
            with self.captureOnCommitCallbacks(execute=True):
                document = self.accept()
        self.assertEqual(OutboxMessage.objects.get(document_id=document.pk).status, "PENDING")

    def test_rollback_never_publishes_or_records_acceptance(self):
        with patch("apps.documents.tasks.store_document.delay") as publish:
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(RuntimeError):
                    with transaction.atomic():
                        self.accept()
                        raise RuntimeError("rollback")
            publish.assert_not_called()
        self.assertFalse(Document.objects.exists())
        self.assertFalse(DocumentAuditEvent.objects.exists())
        self.assertFalse(OutboxMessage.objects.exists())

    def test_dispatch_failure_retains_pending_stage_and_redacted_retry(self):
        from ..outbox_delivery import relay_once
        document = self.accept()
        with patch("apps.documents.tasks.store_document.apply_async", side_effect=OSError("private-broker")), self.assertLogs("apps.documents.outbox_delivery", level="WARNING") as logs:
            relay_once()
        document.refresh_from_db()
        self.assertEqual(document.status, "PENDING")
        self.assertTrue((Path(self.directory.name) / document.staging_key).exists())
        self.assertEqual(DocumentAuditEvent.objects.filter(action="store", outcome="failure").count(), 0)
        self.assertNotIn("private-broker", "\n".join(logs.output))

    def test_database_failure_during_acceptance_cleans_staging(self):
        with patch("apps.documents.background.Document.objects.create", side_effect=IntegrityError("db failure")), self.assertRaises(IntegrityError):
            self.accept()
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
        self.assertFalse(Document.objects.exists())

    def test_deleted_pending_document_is_not_resurrected(self):
        from ..services import delete_document
        document = self.accept()
        stage = Path(self.directory.name) / document.staging_key
        with self.captureOnCommitCallbacks(execute=True):
            delete_document(document_id=document.pk, actor_id=self.editor.pk)
        self.assertFalse(stage.exists())
        self.assertFalse(self.process(document))
        self.assertFalse(Document.objects.exists())
        self.assertFalse(self.storage.exists(document.object_key))

    def test_state_transition_invalidates_cached_pending_metadata(self):
        from ..caching import get_document_metadata
        document = self.accept()
        self.assertEqual(get_document_metadata(document=document)["status"], "PENDING")
        self.process(document)
        document.refresh_from_db()
        self.assertEqual(get_document_metadata(document=document)["status"], "READY")

    def test_celery_task_runs_real_storage_processing(self):
        from ..tasks import store_document
        document = self.accept()
        with self.captureOnCommitCallbacks(execute=True):
            result = store_document.apply(args=[document.pk], throw=True)
        self.assertTrue(result.successful())
        document.refresh_from_db()
        self.assertEqual(document.status, "READY")

    def test_each_celery_message_executes_one_persisted_attempt(self):
        from ..tasks import store_document
        document = self.accept()
        with patch.object(self.storage, "save", side_effect=OSError("private-error")), self.assertLogs("apps.documents.background", level="WARNING"):
            for number in range(1, 5):
                event = OutboxMessage.objects.get(document_id=document.pk, payload={"attempt_number": number})
                OutboxMessage.objects.filter(pk=event.pk).update(available_at=timezone.now())
                with self.captureOnCommitCallbacks(execute=True):
                    store_document.apply(args=[document.pk], kwargs={"event_id": str(event.pk)}, throw=True)
                document.refresh_from_db()
                self.assertEqual(document.storage_attempts, number)
        deliver_outbox_once()
        document.refresh_from_db()
        self.assertEqual((document.status, document.storage_attempts), ("FAILED", 4))

    def test_requeue_command_keeps_existing_work_without_broker_contact(self):
        from django.core.management import call_command
        document = self.accept()
        ready = self.accept()
        self.process(ready)
        with patch("apps.documents.tasks.store_document.delay", side_effect=AssertionError("must enqueue")):
            call_command("requeue_pending_documents", stdout=StringIO())
        self.assertEqual(OutboxMessage.objects.filter(document_id=document.pk, kind="document.upload_requested").count(), 1)
        self.assertEqual(OutboxReceipt.objects.count(), 1)

    def test_unexpected_storage_error_marks_failed_with_redacted_logs(self):
        document = self.accept()
        with patch.object(self.storage, "save", side_effect=ValueError("private-unexpected")), self.assertLogs("apps.documents.background", level="WARNING") as logs:
            self.assertFalse(self.process(document))
        document.refresh_from_db()
        self.assertEqual(document.status, "FAILED")
        self.assertNotIn("private-unexpected", "\n".join(logs.output))

    def test_compensation_preserves_the_authoritative_ready_key(self):
        document = self.accept()
        self.process(document)
        background._compensate_attempt(document.pk, document.object_key)
        self.assertEqual(self.storage.open(document.object_key).read(), self.content)

    def test_compensation_defers_deletion_if_current_ownership_cannot_be_read(self):
        from django.core.files.base import ContentFile
        document = self.accept()
        self.storage.save(document.object_key, ContentFile(self.content))
        with patch("apps.documents.background.Document.objects.select_for_update", side_effect=OperationalError("private-db-details")), self.assertLogs("apps.documents.background", level="ERROR") as logs:
            background._compensate_attempt(document.pk, document.object_key)
        self.assertEqual(self.storage.open(document.object_key).read(), self.content)
        self.assertNotIn("private-db-details", "\n".join(logs.output))


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class ConcurrentBackgroundTests(TransactionTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        setting = override_settings(DOCUMENT_STAGING_ROOT=self.directory.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.creator = get_user_model().objects.create_user(username="concurrent-background", role="editor")
        content = BytesIO()
        Image.new("RGB", (2, 2), "red").save(content, format="PNG")
        self.content = content.getvalue()
        serializer = DocumentUploadSerializer(data={"title": "Queued", "file": SimpleUploadedFile("image.png", self.content, content_type="image/png")})
        serializer.is_valid(raise_exception=True)
        with patch("apps.documents.background.publish_document"):
            self.document = background.stage_document(title="Queued", uploaded_file=serializer.validated_data["file"], creator=self.creator)
        self.storage = InMemoryStorage()

    def run_race(self, other):
        paused, release, entered = Event(), Event(), Event()
        save = self.storage.save
        def pause_upload(name, content):
            paused.set()
            if not release.wait(timeout=10):
                raise TimeoutError("Concurrent operation did not start")
            return save(name, content)
        def execute(operation, started=None):
            close_old_connections()
            try:
                if started:
                    started.set()
                return operation()
            finally:
                close_old_connections()
        with patch("apps.documents.services.default_storage", self.storage), patch.object(self.storage, "save", side_effect=pause_upload), ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(execute, lambda: background.process_staged_document(self.document.pk))
            try:
                self.assertTrue(paused.wait(timeout=10))
                second = executor.submit(execute, other, entered)
                self.assertTrue(entered.wait(timeout=10))
                with self.assertRaises(TimeoutError):
                    second.result(timeout=0.1)
            finally:
                release.set()
            first.result(timeout=20)
            second.result(timeout=20)
            deliver_outbox_once()

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_deliveries_store_and_audit_once(self):
        self.run_race(lambda: background.process_staged_document(self.document.pk))
        self.document.refresh_from_db()
        self.assertEqual((self.document.status, self.document.storage_attempts), ("READY", 1))
        self.assertEqual(self.storage.open(self.document.object_key).read(), self.content)
        self.assertEqual(len(self.storage.listdir("documents")[1]), 1)
        self.assertEqual(DocumentAuditEvent.objects.filter(action="store").count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_delete_waits_for_upload_then_removes_committed_object(self):
        from ..services import delete_document
        self.run_race(lambda: delete_document(document_id=self.document.pk, actor_id=self.creator.pk))
        self.assertFalse(Document.objects.exists())
        self.assertFalse(self.storage.exists(self.document.object_key))
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
        self.assertEqual(DocumentAuditEvent.objects.filter(action="store", outcome="success").count(), 1)
        self.assertEqual(DocumentAuditEvent.objects.filter(action="delete", outcome="success").count(), 1)

    def test_rollback_compensation_cannot_delete_a_duplicate_deliverys_ready_object(self):
        from ..services import _delete_document_object
        storage = FileSystemStorage(location=Path(self.directory.name) / "objects", allow_overwrite=True)
        write = background.record_document_event
        failed_once = False
        def fail_first_storage_event(**values):
            nonlocal failed_once
            if not failed_once:
                failed_once = True
                raise IntegrityError("first attempt audit failure")
            return write(**values)
        def compensate(key):
            # Reproduce a redelivery winning the released lock before old cleanup.
            if not connection.in_atomic_block:
                background.process_staged_document(self.document.pk)
            _delete_document_object(key)
        with patch("apps.documents.services.default_storage", storage), patch("apps.documents.background.record_document_event", side_effect=fail_first_storage_event), patch("apps.documents.services._delete_document_object", side_effect=compensate):
            with self.assertRaises(IntegrityError):
                background.process_staged_document(self.document.pk)
            background.process_staged_document(self.document.pk)
        self.document.refresh_from_db()
        self.assertEqual(self.document.status, "READY")
        self.assertTrue(storage.exists(self.document.object_key), "Earlier rollback deleted a newer READY object's stable key")
        self.assertEqual(storage.open(self.document.object_key).read(), self.content)
        self.assertEqual(DocumentAuditEvent.objects.filter(action="store", outcome="success").count(), 1)
