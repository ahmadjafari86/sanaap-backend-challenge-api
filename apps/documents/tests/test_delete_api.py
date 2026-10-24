from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models.signals import post_delete
from django.test import TransactionTestCase, skipUnlessDBFeature
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from ..models import Document
from ..views import DocumentDetailView
from .outbox_helpers import deliver_outbox_once

User = get_user_model()


def png_upload():
    buffer = BytesIO()
    with Image.new("RGB", (10, 10), color="blue") as image:
        image.save(buffer, format="PNG")
    return SimpleUploadedFile("image.png", buffer.getvalue(), content_type="image/png")


class DocumentDeleteAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="delete-owner")
        cls.admin = User.objects.create_user(username="delete-admin", role=User.Role.ADMIN)
        cls.editor = User.objects.create_user(username="delete-editor", role=User.Role.EDITOR)
        cls.viewer = User.objects.create_user(username="delete-viewer", role=User.Role.VIEWER)
        metadata = {
            "original_filename": "image.png",
            "content_type": "image/png",
            "file_size": png_upload().size,
            "creator": cls.owner,
        }
        cls.document = Document.objects.create(
            title="Delete this image", object_key="documents/deletable.png", **metadata,
        )
        cls.other_document = Document.objects.create(
            title="Keep this image", object_key="documents/keep.png", **metadata,
        )
        cls.url = f"/api/v1/documents/{cls.document.pk}/"

    def setUp(self):
        self.storage = InMemoryStorage()
        self.image_bytes = png_upload().read()
        for document in (self.document, self.other_document):
            self.storage.save(document.object_key, ContentFile(self.image_bytes))
        storage_patch = patch("apps.documents.services.default_storage", self.storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)

    def assert_document_is_intact(self):
        self.assertTrue(Document.objects.filter(pk=self.document.pk).exists())
        self.assertEqual(self.storage.open(self.document.object_key).read(), self.image_bytes)
        self.assertTrue(Document.objects.filter(pk=self.other_document.pk).exists())
        self.assertTrue(self.storage.exists(self.other_document.object_key))

    def test_admin_can_delete_another_users_document_only_after_commit(self):
        self.client.force_authenticate(user=self.admin)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.url)

            self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
            self.assertEqual(response.content, b"")
            self.assertFalse(Document.objects.filter(pk=self.document.pk).exists())
            self.assertTrue(self.storage.exists(self.document.object_key))

        self.assertTrue(self.storage.exists(self.document.object_key))
        deliver_outbox_once()
        self.assertFalse(self.storage.exists(self.document.object_key))
        self.assertTrue(Document.objects.filter(pk=self.other_document.pk).exists())
        self.assertTrue(self.storage.exists(self.other_document.object_key))
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())

    def test_editor_and_viewer_cannot_delete(self):
        for user in (self.editor, self.viewer):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)

                response = self.client.delete(self.url)

                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.assert_document_is_intact()

    def test_django_superuser_without_application_admin_role_cannot_delete(self):
        self.viewer.is_staff = True
        self.viewer.is_superuser = True
        self.viewer.save(update_fields=["is_staff", "is_superuser"])
        self.client.force_authenticate(user=self.viewer)

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_document_is_intact()

    def test_anonymous_user_cannot_delete(self):
        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assert_document_is_intact()

    def test_unknown_role_cannot_delete(self):
        self.admin.role = "unknown"
        self.client.force_authenticate(user=self.admin)

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_document_is_intact()

    def test_admin_can_delete_with_jwt(self):
        token = RefreshToken.for_user(self.admin).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.url)

        deliver_outbox_once()
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Document.objects.filter(pk=self.document.pk).exists())
        self.assertFalse(self.storage.exists(self.document.object_key))

    def test_invalid_jwt_cannot_delete(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid-token")

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assert_document_is_intact()

    def test_inactive_admin_jwt_cannot_delete(self):
        token = RefreshToken.for_user(self.admin).access_token
        self.admin.is_active = False
        self.admin.save(update_fields=["is_active"])
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assert_document_is_intact()

    def test_missing_document_returns_not_found_without_touching_other_files(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.delete("/api/v1/documents/999999/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assert_document_is_intact()
        self.assertEqual(set(self.storage.listdir("documents")[1]), {"deletable.png", "keep.png"})

    def test_repeated_deletion_returns_not_found(self):
        self.client.force_authenticate(user=self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            first = self.client.delete(self.url)
            second = self.client.delete(self.url)

        deliver_outbox_once()
        self.assertEqual(first.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(second.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.storage.listdir("documents")[1], ["keep.png"])

    def test_database_error_preserves_document_and_file(self):
        self.client.force_authenticate(user=self.admin)
        with (
            patch.object(Document, "delete", side_effect=IntegrityError("private-database-details")),
            self.assertLogs("apps.documents.views", level="ERROR"),
        ):
            response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertNotIn("private-database-details", response.content.decode())
        self.assert_document_is_intact()

    def test_error_after_row_deletion_rolls_back_and_preserves_file(self):
        self.client.force_authenticate(user=self.admin)

        def fail_after_delete(sender, instance, **kwargs):
            if instance.pk == self.document.pk:
                self.assertFalse(Document.objects.filter(pk=instance.pk).exists())
                raise IntegrityError("Delete acknowledgement failed")

        post_delete.connect(fail_after_delete, sender=Document, weak=False)
        try:
            with self.assertLogs("apps.documents.views", level="ERROR"):
                response = self.client.delete(self.url)
        finally:
            post_delete.disconnect(fail_after_delete, sender=Document)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assert_document_is_intact()

    def test_storage_cleanup_failure_does_not_undo_committed_deletion(self):
        self.client.force_authenticate(user=self.admin)
        with (
            patch.object(self.storage, "delete", side_effect=OSError("private-storage-details")),
            self.assertLogs("apps.documents.outbox_delivery", level="WARNING") as logs,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.delete(self.url)
            deliver_outbox_once()

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Document.objects.filter(pk=self.document.pk).exists())
        self.assertTrue(self.storage.exists(self.document.object_key))
        self.assertTrue(Document.objects.filter(pk=self.other_document.pk).exists())
        self.assertNotIn("private-storage-details", "\n".join(logs.output))
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_404_NOT_FOUND)

    def test_outer_rollback_preserves_document_and_file(self):
        self.client.force_authenticate(user=self.admin)
        with self.assertRaisesMessage(RuntimeError, "outer rollback"):
            with transaction.atomic():
                response = self.client.delete(self.url)
                self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
                self.assertFalse(Document.objects.filter(pk=self.document.pk).exists())
                self.assertTrue(self.storage.exists(self.document.object_key))
                raise RuntimeError("outer rollback")

        self.assert_document_is_intact()

    def test_missing_storage_object_does_not_prevent_database_deletion(self):
        self.client.force_authenticate(user=self.admin)
        self.storage.delete(self.document.object_key)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Document.objects.filter(pk=self.document.pk).exists())
        self.assertTrue(self.storage.exists(self.other_document.object_key))

    def test_deletion_rereads_current_key_after_replacement(self):
        self.client.force_authenticate(user=self.admin)
        editor_client = APIClient()
        editor_client.force_authenticate(user=self.editor)
        original_get_object = DocumentDetailView.get_object

        def get_stale_document_then_replace(view):
            document = original_get_object(view)
            if view.request.method == "DELETE":
                replacement = editor_client.patch(
                    self.url, {"file": png_upload()}, format="multipart",
                )
                self.assertEqual(replacement.status_code, status.HTTP_200_OK)
            return document

        with (
            patch.object(DocumentDetailView, "get_object", new=get_stale_document_then_replace),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Document.objects.filter(pk=self.document.pk).exists())
        deliver_outbox_once()
        self.assertEqual(self.storage.listdir("documents")[1], ["keep.png"])


class ConcurrentDocumentDeletionTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_deletion_during_replacement_upload_cleans_both_files(self):
        admin = User.objects.create_user(username="race-admin", role=User.Role.ADMIN)
        editor = User.objects.create_user(username="race-editor", role=User.Role.EDITOR)
        storage = InMemoryStorage()
        original_bytes = png_upload().read()
        old_key = storage.save("documents/original.png", ContentFile(original_bytes))
        document = Document.objects.create(
            title="Concurrent image",
            original_filename="original.png",
            object_key=old_key,
            content_type="image/png",
            file_size=len(original_bytes),
            creator=editor,
        )
        url = f"/api/v1/documents/{document.pk}/"
        uploaded = Event()
        resume_upload = Event()
        original_save = storage.save

        def pause_after_upload(name, content):
            key = original_save(name, content)
            uploaded.set()
            if not resume_upload.wait(timeout=10):
                raise TimeoutError("Deletion did not complete during upload")
            return key

        def replace():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user=editor)
                response = client.patch(url, {"file": png_upload()}, format="multipart")
                return response.status_code
            finally:
                close_old_connections()

        with (
            patch("apps.documents.services.default_storage", storage),
            patch.object(storage, "save", side_effect=pause_after_upload),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            future = executor.submit(replace)
            try:
                self.assertTrue(uploaded.wait(timeout=10))
                client = APIClient()
                client.force_authenticate(user=admin)
                deletion = client.delete(url)
            finally:
                resume_upload.set()
            replacement_status = future.result(timeout=20)
            deliver_outbox_once()

        self.assertEqual(deletion.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(replacement_status, status.HTTP_404_NOT_FOUND)
        self.assertFalse(Document.objects.filter(pk=document.pk).exists())
        self.assertEqual(storage.listdir("documents")[1], [])
