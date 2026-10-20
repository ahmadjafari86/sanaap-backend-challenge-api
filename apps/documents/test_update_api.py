from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Document

User = get_user_model()


def image_bytes(image_format="PNG", color="blue"):
    buffer = BytesIO()
    with Image.new("RGB", (10, 10), color=color) as image:
        image.save(buffer, format=image_format)
    return buffer.getvalue()


def image_upload(image_format="PNG", color="red"):
    return SimpleUploadedFile(
        f"replacement.{image_format.lower()}",
        image_bytes(image_format, color),
        content_type=f"image/{image_format.lower()}",
    )


class DocumentUpdateAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="update-owner")
        cls.admin = User.objects.create_user(username="update-admin", role=User.Role.ADMIN)
        cls.editor = User.objects.create_user(username="update-editor", role=User.Role.EDITOR)
        cls.viewer = User.objects.create_user(username="update-viewer", role=User.Role.VIEWER)
        cls.document = Document.objects.create(
            title="Original image",
            original_filename="original.png",
            object_key="documents/original.png",
            content_type="image/png",
            file_size=len(image_bytes()),
            creator=cls.owner,
        )
        Document.objects.filter(pk=cls.document.pk).update(
            updated_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        cls.document.refresh_from_db()
        cls.url = f"/api/v1/documents/{cls.document.pk}/"

    def setUp(self):
        self.old_key = self.document.object_key
        self.storage = InMemoryStorage()
        self.storage.save(self.old_key, ContentFile(image_bytes()))
        storage_patch = patch("apps.documents.services.default_storage", self.storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)

    def assert_original_is_intact(self):
        self.document.refresh_from_db()
        self.assertEqual(self.document.title, "Original image")
        self.assertEqual(self.document.object_key, self.old_key)
        self.assertEqual(self.document.original_filename, "original.png")
        self.assertEqual(self.document.creator_id, self.owner.pk)
        self.assertEqual(self.document.content_type, "image/png")
        self.assertEqual(self.document.file_size, len(image_bytes()))
        self.assertEqual(self.storage.open(self.old_key).read(), image_bytes())

    def test_admin_and_editor_can_patch_another_users_title(self):
        created_at = self.document.created_at
        for user in (self.admin, self.editor):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                previous_updated_at = self.document.updated_at

                response = self.client.patch(self.url, {"title": user.role}, format="json")

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.document.refresh_from_db()
                self.assertEqual(self.document.title, user.role)
                self.assertEqual(self.document.object_key, self.old_key)
                self.assertEqual(self.document.creator_id, self.owner.pk)
                self.assertEqual(self.document.created_at, created_at)
                self.assertGreater(self.document.updated_at, previous_updated_at)
                self.assertEqual(response.data["creator"], self.owner.pk)
                self.assertEqual(self.storage.listdir("documents")[1], ["original.png"])

    def test_admin_and_editor_can_put_metadata_without_replacing_the_file(self):
        for user in (self.admin, self.editor):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)

                response = self.client.put(self.url, {"title": user.role}, format="json")

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.document.refresh_from_db()
                self.assertEqual(self.document.title, user.role)
                self.assertEqual(self.document.object_key, self.old_key)
                self.assertEqual(self.document.creator_id, self.owner.pk)
                self.assertTrue(self.storage.exists(self.old_key))

    def test_png_and_jpeg_replacements_delete_previous_files_only_after_commit(self):
        self.client.force_authenticate(user=self.editor)
        for image_format in ("PNG", "JPEG"):
            with self.subTest(image_format=image_format):
                previous_key = self.document.object_key
                uploaded_file = image_upload(image_format)
                expected_bytes = uploaded_file.read()
                uploaded_file.seek(0)

                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.patch(
                        self.url, {"file": uploaded_file}, format="multipart",
                    )
                    self.assertEqual(response.status_code, status.HTTP_200_OK)
                    self.document.refresh_from_db()
                    self.assertNotEqual(self.document.object_key, previous_key)
                    self.assertTrue(self.storage.exists(previous_key))
                    self.assertEqual(
                        self.storage.open(self.document.object_key).read(), expected_bytes,
                    )

                self.assertFalse(self.storage.exists(previous_key))
                self.assertEqual(self.document.title, "Original image")
                self.assertEqual(self.document.creator_id, self.owner.pk)
                self.assertEqual(response.data["content_type"], f"image/{image_format.lower()}")
                self.assertEqual(response.data["file_size"], len(expected_bytes))

    def test_put_can_replace_title_and_image(self):
        self.client.force_authenticate(user=self.admin)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.put(
                self.url,
                {"title": "Replacement title", "file": image_upload("JPEG")},
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.document.refresh_from_db()
        self.assertEqual(self.document.title, "Replacement title")
        self.assertEqual(self.document.content_type, "image/jpeg")
        self.assertEqual(self.document.creator_id, self.owner.pk)
        self.assertFalse(self.storage.exists(self.old_key))

    def test_viewer_cannot_update(self):
        self.client.force_authenticate(user=self.viewer)
        for method in ("patch", "put"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    self.url, {"title": "Denied", "file": image_upload()}, format="multipart",
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.assert_original_is_intact()
                self.assertEqual(self.storage.listdir("documents")[1], ["original.png"])

    def test_anonymous_user_cannot_update(self):
        for method in ("patch", "put"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    self.url, {"title": "Denied", "file": image_upload()}, format="multipart",
                )
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
                self.assert_original_is_intact()

    def test_unknown_role_cannot_update(self):
        self.editor.role = "unknown"
        self.client.force_authenticate(user=self.editor)

        response = self.client.patch(self.url, {"title": "Denied"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assert_original_is_intact()

    def test_editor_can_update_with_jwt(self):
        token = RefreshToken.for_user(self.editor).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.patch(self.url, {"title": "JWT update"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.document.refresh_from_db()
        self.assertEqual(self.document.title, "JWT update")
        self.assertEqual(self.document.creator_id, self.owner.pk)

    def test_missing_document_returns_not_found_without_upload(self):
        self.client.force_authenticate(user=self.editor)

        response = self.client.patch(
            "/api/v1/documents/999999/", {"file": image_upload()}, format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assert_original_is_intact()
        self.assertEqual(self.storage.listdir("documents")[1], ["original.png"])

    def test_invalid_titles_are_rejected(self):
        self.client.force_authenticate(user=self.editor)
        for title in ("", "   ", None, "a" * 256):
            with self.subTest(title=title):
                response = self.client.patch(self.url, {"title": title}, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("title", response.data)
                self.assert_original_is_intact()

    def test_patch_requires_a_change_and_put_requires_a_title(self):
        self.client.force_authenticate(user=self.editor)
        for payload in ({}, {"creator": self.admin.pk}):
            with self.subTest(payload=payload):
                response = self.client.patch(self.url, payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.put(self.url, {"file": image_upload()}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("title", response.data)
        self.assert_original_is_intact()

    def test_invalid_and_unsupported_replacements_preserve_original(self):
        self.client.force_authenticate(user=self.editor)
        uploads = (
            SimpleUploadedFile("fake.png", b"Not an image", content_type="image/png"),
            image_upload("GIF"),
        )
        for uploaded_file in uploads:
            with self.subTest(filename=uploaded_file.name):
                response = self.client.patch(
                    self.url, {"file": uploaded_file}, format="multipart",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("file", response.data)
                self.assert_original_is_intact()
                self.assertEqual(self.storage.listdir("documents")[1], ["original.png"])

    def test_replacement_size_boundary(self):
        self.client.force_authenticate(user=self.editor)
        content = image_bytes(color="red")
        max_size = 10 * 1024 * 1024
        for size, expected_status in (
            (max_size + 1, status.HTTP_400_BAD_REQUEST),
            (max_size, status.HTTP_200_OK),
        ):
            with self.subTest(size=size):
                uploaded_file = SimpleUploadedFile(
                    "large.png", content + b"\0" * (size - len(content)), content_type="image/png",
                )
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.patch(
                        self.url, {"file": uploaded_file}, format="multipart",
                    )
                self.assertEqual(response.status_code, expected_status)
                if expected_status == status.HTTP_400_BAD_REQUEST:
                    self.assert_original_is_intact()
                else:
                    self.assertEqual(response.data["file_size"], max_size)

    def test_client_cannot_override_server_owned_metadata(self):
        self.client.force_authenticate(user=self.editor)
        created_at = self.document.created_at

        response = self.client.patch(
            self.url,
            {
                "title": "Allowed title",
                "creator": self.editor.pk,
                "object_key": "forged-key",
                "file_size": 1,
                "content_type": "text/plain",
                "original_filename": "forged.txt",
                "created_at": "2000-01-01T00:00:00Z",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.document.refresh_from_db()
        self.assertEqual(self.document.title, "Allowed title")
        self.assertEqual(self.document.creator_id, self.owner.pk)
        self.assertEqual(self.document.object_key, self.old_key)
        self.assertEqual(self.document.file_size, len(image_bytes()))
        self.assertEqual(self.document.content_type, "image/png")
        self.assertEqual(self.document.original_filename, "original.png")
        self.assertEqual(self.document.created_at, created_at)

    def test_upload_failure_preserves_the_original(self):
        self.client.force_authenticate(user=self.editor)
        with (
            patch.object(self.storage, "save", side_effect=OSError("private-storage-details")),
            self.assertLogs("apps.documents.views", level="ERROR"),
        ):
            response = self.client.patch(self.url, {"file": image_upload()}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertNotIn("private-storage-details", response.content.decode())
        self.assert_original_is_intact()

    def test_database_failure_cleans_new_file_and_preserves_original(self):
        self.client.force_authenticate(user=self.editor)
        with (
            patch.object(Document, "save", side_effect=IntegrityError("private-database-details")),
            self.assertLogs("apps.documents.views", level="ERROR"),
        ):
            response = self.client.patch(
                self.url, {"title": "Failed title", "file": image_upload()}, format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertNotIn("private-database-details", response.content.decode())
        self.assert_original_is_intact()
        self.assertEqual(self.storage.listdir("documents")[1], ["original.png"])

    def test_upload_error_after_object_write_cleans_the_attempted_key(self):
        self.client.force_authenticate(user=self.editor)
        original_save = self.storage.save

        def write_then_fail(name, content):
            original_save(name, content)
            raise OSError("Upload acknowledgement was lost")

        with (
            patch.object(self.storage, "save", side_effect=write_then_fail),
            self.assertLogs("apps.documents.views", level="ERROR"),
        ):
            response = self.client.patch(self.url, {"file": image_upload()}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assert_original_is_intact()
        self.assertEqual(self.storage.listdir("documents")[1], ["original.png"])

    def test_metadata_database_failure_preserves_original(self):
        self.client.force_authenticate(user=self.editor)
        with (
            patch.object(Document, "save", side_effect=IntegrityError("write failed")),
            self.assertLogs("apps.documents.views", level="ERROR"),
        ):
            response = self.client.patch(self.url, {"title": "Failed title"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assert_original_is_intact()

    def test_old_file_cleanup_failure_does_not_fail_successful_update(self):
        self.client.force_authenticate(user=self.editor)
        with (
            patch.object(self.storage, "delete", side_effect=OSError("cleanup failed")),
            self.assertLogs("apps.documents.services", level="ERROR"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.patch(self.url, {"file": image_upload()}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.document.refresh_from_db()
        self.assertNotEqual(self.document.object_key, self.old_key)
        self.assertTrue(self.storage.exists(self.document.object_key))
        self.assertTrue(self.storage.exists(self.old_key))

    def test_new_file_cleanup_failure_preserves_original_database_error(self):
        self.client.force_authenticate(user=self.editor)
        with (
            patch.object(Document, "save", side_effect=IntegrityError("database write failed")),
            patch.object(self.storage, "delete", side_effect=OSError("cleanup failed")),
            self.assertLogs("apps.documents.services", level="ERROR"),
            self.assertLogs("apps.documents.views", level="ERROR") as logs,
        ):
            response = self.client.patch(self.url, {"file": image_upload()}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertIn("IntegrityError", "\n".join(logs.output))
        self.assert_original_is_intact()

    def test_outer_rollback_preserves_original_reference_and_file(self):
        self.client.force_authenticate(user=self.editor)
        with self.assertRaisesMessage(RuntimeError, "outer rollback"):
            with transaction.atomic():
                response = self.client.patch(self.url, {"file": image_upload()}, format="multipart")
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertTrue(self.storage.exists(self.old_key))
                raise RuntimeError("outer rollback")

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            pass
        self.assertEqual(callbacks, [])
        self.assert_original_is_intact()

    def test_document_disappearing_after_upload_cleans_the_new_file(self):
        self.client.force_authenticate(user=self.editor)
        original_save = self.storage.save

        def save_then_remove_document(name, content):
            key = original_save(name, content)
            Document.objects.filter(pk=self.document.pk).delete()
            return key

        with patch.object(self.storage, "save", side_effect=save_then_remove_document):
            response = self.client.patch(self.url, {"file": image_upload()}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(Document.objects.filter(pk=self.document.pk).exists())
        self.assertEqual(self.storage.listdir("documents")[1], ["original.png"])


class ConcurrentDocumentReplacementTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_replacements_leave_only_the_current_file(self):
        editor = User.objects.create_user(username="concurrent-editor", role=User.Role.EDITOR)
        storage = InMemoryStorage()
        old_key = storage.save("documents/original.png", ContentFile(image_bytes()))
        document = Document.objects.create(
            title="Original image",
            original_filename="original.png",
            object_key=old_key,
            content_type="image/png",
            file_size=len(image_bytes()),
            creator=editor,
        )
        barrier = Barrier(2)
        original_save = storage.save

        def save_and_synchronize(name, content):
            key = original_save(name, content)
            barrier.wait(timeout=10)
            return key

        def replace(color):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user=editor)
                response = client.patch(
                    f"/api/v1/documents/{document.pk}/",
                    {"file": image_upload(color=color)},
                    format="multipart",
                )
                return response.status_code
            finally:
                close_old_connections()

        with (
            patch("apps.documents.services.default_storage", storage),
            patch.object(storage, "save", side_effect=save_and_synchronize),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            futures = [executor.submit(replace, color) for color in ("red", "green")]
            responses = [future.result(timeout=20) for future in futures]

        self.assertEqual(responses, [status.HTTP_200_OK, status.HTTP_200_OK])
        document.refresh_from_db()
        self.assertNotEqual(document.object_key, old_key)
        self.assertTrue(storage.exists(document.object_key))
        self.assertFalse(storage.exists(old_key))
        self.assertEqual(storage.listdir("documents")[1], [document.object_key.split("/")[-1]])
