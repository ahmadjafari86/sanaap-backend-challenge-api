import asyncio
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase, override_settings
from PIL import Image

from .. import background, services
from ..models import Document, DocumentAuditEvent
from ..serializers import DocumentUploadSerializer


@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}, CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class DocumentNotificationTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        setting = override_settings(DOCUMENT_STAGING_ROOT=directory.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.user = get_user_model().objects.create_user(username="notifications", role="editor")
        self.document = Document.objects.create(title="Before", original_filename="private.png", object_key="documents/private.png", content_type="image/png", file_size=1, creator=self.user)
        self.layer = get_channel_layer()
        self.channel = async_to_sync(self.layer.new_channel)()
        async_to_sync(self.layer.group_add)("documents.notifications", self.channel)
        storage = patch("apps.documents.services.default_storage", InMemoryStorage())
        storage.start()
        self.addCleanup(storage.stop)

    def event(self):
        async def receive():
            return await asyncio.wait_for(self.layer.receive(self.channel), timeout=.05)
        return async_to_sync(receive)()

    def no_event(self):
        with self.assertRaises(TimeoutError):
            self.event()

    def upload(self):
        image = BytesIO()
        Image.new("RGB", (2, 2), "red").save(image, format="PNG")
        serializer = DocumentUploadSerializer(data={"title": "Queued", "file": SimpleUploadedFile("private.png", image.getvalue(), content_type="image/png")})
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data["file"]

    def stage(self):
        # Keep task dispatch outside these deterministic worker/transaction tests.
        return background.stage_document(title="Queued", uploaded_file=self.upload(), creator=self.user)

    def test_update_sends_minimal_event_only_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            services.update_document(document_id=self.document.pk, title="After", actor_id=self.user.pk)
            self.no_event()
        self.assertEqual(self.event(), {"type": "document.event", "event": "document.updated", "document_id": self.document.pk})
        self.no_event()

    def test_replacement_notifies_after_successful_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            services.update_document(document_id=self.document.pk, uploaded_file=self.upload(), actor_id=self.user.pk)
        self.assertEqual(self.event()["event"], "document.updated")

    def test_outer_rollback_sends_no_event_and_preserves_metadata(self):
        with self.captureOnCommitCallbacks(execute=True), self.assertRaises(RuntimeError):
            with transaction.atomic():
                services.update_document(document_id=self.document.pk, title="Rolled back")
                raise RuntimeError("rollback")
        self.document.refresh_from_db()
        self.assertEqual(self.document.title, "Before")
        self.no_event()

    def test_failed_audit_sends_no_event(self):
        with self.captureOnCommitCallbacks(execute=True), patch("apps.documents.services.record_document_event", side_effect=RuntimeError("audit unavailable")), self.assertRaises(RuntimeError):
            services.update_document(document_id=self.document.pk, title="Rolled back")
        self.no_event()

    def test_ready_worker_transition_notifies_once_despite_redelivery(self):
        queued = self.stage()
        self.no_event()
        with self.captureOnCommitCallbacks(execute=True):
            background.process_staged_document(queued.pk)
            self.no_event()
        self.assertEqual(self.event(), {"type": "document.event", "event": "document.ready", "document_id": queued.pk})
        with self.captureOnCommitCallbacks(execute=True):
            background.process_staged_document(queued.pk)
        self.no_event()

    def test_failed_worker_and_delete_do_not_notify(self):
        queued = self.stage()
        with self.captureOnCommitCallbacks(execute=True), patch("apps.documents.services.default_storage.save", side_effect=ValueError("terminal")), self.assertLogs("apps.documents.background", level="WARNING"):
            background.process_staged_document(queued.pk)
        with self.captureOnCommitCallbacks(execute=True):
            services.delete_document(document_id=queued.pk)
        self.no_event()

    def test_worker_outer_rollback_drops_ready_notification(self):
        queued = self.stage()
        with self.captureOnCommitCallbacks(execute=True), self.assertRaises(RuntimeError):
            with transaction.atomic():
                background.process_staged_document(queued.pk)
                raise RuntimeError("rollback")
        queued.refresh_from_db()
        self.assertEqual(queued.status, "PENDING")
        self.no_event()

    def test_internal_synchronous_create_notifies_when_ready_commits(self):
        with self.captureOnCommitCallbacks(execute=True):
            document = services.create_document(title="Internal", uploaded_file=self.upload(), creator=self.user)
            self.no_event()
        self.assertEqual(self.event(), {"type": "document.event", "event": "document.ready", "document_id": document.pk})

    def test_publication_failure_cannot_undo_update_or_expose_private_error(self):
        with self.assertLogs("apps.documents.notifications", level="WARNING") as logs, patch.object(self.layer, "group_send", side_effect=OSError("private-token-url")), self.captureOnCommitCallbacks(execute=True):
            services.update_document(document_id=self.document.pk, title="Committed", actor_id=self.user.pk)
        self.document.refresh_from_db()
        self.assertEqual(self.document.title, "Committed")
        self.assertTrue(DocumentAuditEvent.objects.filter(document_id=self.document.pk, action="update", outcome="success").exists())
        self.assertNotIn("private-token-url", "\n".join(logs.output))

    def test_unresponsive_publication_is_bounded(self):
        async def blocked(*args):
            await asyncio.Event().wait()
        with patch.object(self.layer, "group_send", side_effect=blocked), patch("apps.documents.notifications.PUBLICATION_TIMEOUT", .02), self.assertLogs("apps.documents.notifications", level="WARNING"):
            with self.captureOnCommitCallbacks(execute=True):
                services.update_document(document_id=self.document.pk, title="Committed")
        self.document.refresh_from_db()
        self.assertEqual(self.document.title, "Committed")
