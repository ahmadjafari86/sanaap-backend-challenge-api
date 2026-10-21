from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import override_settings
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from minio.error import MinioException
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken
from urllib3.exceptions import HTTPError

from .models import Document

User = get_user_model()


class DocumentUploadAPITests(APITestCase):
    url = "/api/v1/documents/"

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        setting = override_settings(DOCUMENT_STAGING_ROOT=self.directory.name)
        setting.enable()
        self.addCleanup(setting.disable)

    def make_payload(self, image_format="PNG"):
        buffer = BytesIO()
        Image.new("RGB", (10, 10), color="blue").save(
            buffer,
            format=image_format,
        )

        return {
            "title": "API image",
            "file": SimpleUploadedFile(
                name=f"sample.{image_format.lower()}",
                content=buffer.getvalue(),
                content_type=f"image/{image_format.lower()}",
            ),
        }

    def authenticate_editor(self):
        editor = User.objects.create_user(
            username="api-editor",
            role=User.Role.EDITOR,
        )
        self.client.force_authenticate(user=editor)
        return editor

    @patch("apps.documents.services.default_storage")
    def test_admin_and_editor_can_upload(self, storage):
        storage.save.side_effect = lambda name, content: name

        for role in (User.Role.ADMIN, User.Role.EDITOR):
            with self.subTest(role=role):
                user = User.objects.create_user(
                    username=f"uploader-{role}",
                    role=role,
                )
                self.client.force_authenticate(user=user)

                response = self.client.post(
                    self.url,
                    self.make_payload(),
                    format="multipart",
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_202_ACCEPTED,
                    response.content,
                )

                document = Document.objects.get(pk=response.data["id"])
                self.assertEqual(document.title, "API image")
                self.assertEqual(document.creator_id, user.pk)
                self.assertEqual(response.data["creator"], user.pk)

        self.assertEqual(Document.objects.count(), 2)
        storage.save.assert_not_called()

    @patch("apps.documents.services.default_storage")
    def test_viewer_cannot_upload(self, storage):
        viewer = User.objects.create_user(
            username="upload-viewer",
            role=User.Role.VIEWER,
        )
        self.client.force_authenticate(user=viewer)

        response = self.client.post(
            self.url,
            self.make_payload(),
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Document.objects.exists())
        storage.save.assert_not_called()

    @patch("apps.documents.services.default_storage")
    def test_anonymous_user_cannot_upload(self, storage):
        response = self.client.post(
            self.url,
            self.make_payload(),
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(Document.objects.exists())
        storage.save.assert_not_called()

    @patch("apps.documents.services.default_storage")
    def test_jpeg_upload_returns_document_metadata(self, storage):
        self.authenticate_editor()
        storage.save.side_effect = lambda name, content: name

        response = self.client.post(
            self.url,
            self.make_payload("JPEG"),
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["content_type"], "image/jpeg")
        self.assertEqual(response.data["original_filename"], "sample.jpeg")
        self.assertGreater(response.data["file_size"], 0)
        self.assertTrue(response.data["created_at"])
        self.assertTrue(response.data["updated_at"])
        self.assertNotIn("object_key", response.data)
        self.assertNotIn("file", response.data)
        self.assertNotIn("download_url", response.data)

    @patch("apps.documents.services.default_storage")
    def test_image_content_determines_mime_type_and_storage_extension(self, storage):
        self.authenticate_editor()
        storage.save.side_effect = lambda name, content: name
        payload = self.make_payload()
        payload["file"].name = "misleading.jpeg"
        payload["file"].content_type = "image/jpeg"

        response = self.client.post(self.url, payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        document = Document.objects.get(pk=response.data["id"])
        self.assertEqual(document.content_type, "image/png")
        self.assertEqual(document.original_filename, "misleading.jpeg")
        self.assertRegex(document.object_key, r"\Adocuments/[0-9a-f]{32}\.png\Z")

    @patch("apps.documents.services.default_storage")
    def test_missing_or_blank_required_fields_are_rejected(self, storage):
        self.authenticate_editor()
        payloads = (
            ({}, "title"),
            ({"title": "Missing file"}, "file"),
            ({"file": self.make_payload()["file"]}, "title"),
            ({**self.make_payload(), "title": "   "}, "title"),
        )

        for payload, field in payloads:
            with self.subTest(field=field, fields=list(payload)):
                response = self.client.post(self.url, payload, format="multipart")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)

        self.assertFalse(Document.objects.exists())
        storage.save.assert_not_called()

    @patch("apps.documents.services.default_storage")
    def test_invalid_and_unsupported_images_are_rejected_before_storage(self, storage):
        self.authenticate_editor()
        fake_image = SimpleUploadedFile(
            "fake.png",
            b"Not an image.",
            content_type="image/png",
        )
        payloads = (
            {"title": "Fake image", "file": fake_image},
            self.make_payload("GIF"),
        )

        for payload in payloads:
            with self.subTest(filename=payload["file"].name):
                response = self.client.post(self.url, payload, format="multipart")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("file", response.data)

        self.assertFalse(Document.objects.exists())
        storage.save.assert_not_called()

    @patch("apps.documents.services.default_storage")
    def test_truncated_jpeg_is_rejected_before_storage(self, storage):
        self.authenticate_editor()
        storage.save.side_effect = lambda name, content: name
        buffer = BytesIO()
        Image.new("RGB", (100, 100), color="blue").save(buffer, format="JPEG")
        uploaded_file = SimpleUploadedFile(
            "truncated.jpeg", buffer.getvalue()[:-200], content_type="image/jpeg",
        )

        response = self.client.post(
            self.url, {"title": "Corrupt image", "file": uploaded_file}, format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("file", response.data)
        self.assertFalse(Document.objects.exists())
        storage.save.assert_not_called()

    @patch("apps.documents.services.default_storage")
    def test_upload_size_boundary(self, storage):
        self.authenticate_editor()
        storage.save.side_effect = lambda name, content: name
        image_bytes = self.make_payload()["file"].read()
        max_size = 10 * 1024 * 1024

        for size, expected_status in (
            (max_size, status.HTTP_202_ACCEPTED),
            (max_size + 1, status.HTTP_400_BAD_REQUEST),
        ):
            with self.subTest(size=size):
                uploaded_file = SimpleUploadedFile(
                    "large.png",
                    image_bytes + b"\0" * (size - len(image_bytes)),
                    content_type="image/png",
                )
                response = self.client.post(
                    self.url,
                    {"title": "Large image", "file": uploaded_file},
                    format="multipart",
                )
                self.assertEqual(response.status_code, expected_status)
                if expected_status == status.HTTP_202_ACCEPTED:
                    self.assertEqual(response.data["file_size"], max_size)
                else:
                    self.assertIn("file", response.data)

        self.assertEqual(Document.objects.count(), 1)
        storage.save.assert_not_called()

    @patch("apps.documents.services.default_storage")
    def test_client_metadata_cannot_override_server_values(self, storage):
        editor = self.authenticate_editor()
        other_user = User.objects.create_user(username="other-creator")
        storage.save.side_effect = lambda name, content: name
        payload = self.make_payload()
        actual_size = payload["file"].size
        payload.update(
            creator=other_user.pk,
            object_key="client-controlled-key",
            content_type="text/plain",
            file_size=1,
            original_filename="client-controlled.txt",
        )

        response = self.client.post(self.url, payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        document = Document.objects.get(pk=response.data["id"])
        self.assertEqual(document.creator_id, editor.pk)
        self.assertEqual(response.data["creator"], editor.pk)
        self.assertEqual(document.content_type, "image/png")
        self.assertEqual(document.file_size, actual_size)
        self.assertEqual(document.original_filename, "sample.png")
        self.assertNotEqual(document.object_key, "client-controlled-key")

    @patch("apps.documents.services.default_storage")
    def test_unknown_role_cannot_upload(self, storage):
        user = User.objects.create_user(username="unknown-role", role="unknown")
        self.client.force_authenticate(user=user)

        response = self.client.post(self.url, self.make_payload(), format="multipart")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Document.objects.exists())
        storage.save.assert_not_called()

    @patch("apps.documents.services.default_storage")
    def test_editor_can_upload_with_jwt_authentication(self, storage):
        editor = User.objects.create_user(
            username="jwt-editor",
            role=User.Role.EDITOR,
        )
        access_token = RefreshToken.for_user(editor).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        storage.save.side_effect = lambda name, content: name

        response = self.client.post(self.url, self.make_payload(), format="multipart")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["creator"], editor.pk)
        storage.save.assert_not_called()

    @patch("apps.documents.services.default_storage")
    def test_invalid_jwt_is_rejected_before_storage(self, storage):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid-token")

        response = self.client.post(self.url, self.make_payload(), format="multipart")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(Document.objects.exists())
        storage.save.assert_not_called()

    @patch("apps.documents.background.staging_storage")
    def test_staging_outages_return_safe_service_unavailable_response(self, staging_factory):
        storage = staging_factory.return_value
        self.authenticate_editor()
        private_message = "private-storage-connection-details"

        for error in (
            OSError(private_message),
            MinioException(private_message),
            HTTPError(private_message),
        ):
            with self.subTest(error=type(error).__name__):
                storage.save.side_effect = error
                with self.assertLogs("apps.documents.views", level="ERROR") as logs:
                    response = self.client.post(
                        self.url,
                        self.make_payload(),
                        format="multipart",
                    )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                )
                self.assertIn("detail", response.data)
                self.assertNotIn(private_message, response.content.decode())
                self.assertNotIn(private_message, "\n".join(logs.output))

        self.assertFalse(Document.objects.exists())
        attempted_keys = [call.args[0] for call in storage.save.call_args_list]
        self.assertEqual([call.args[0] for call in storage.delete.call_args_list], attempted_keys)

    @patch("apps.documents.services.default_storage")
    def test_database_failure_cleans_up_and_returns_service_unavailable(self, storage):
        self.authenticate_editor()
        storage.save.return_value = "documents/failed-creation.png"

        with (
            patch(
                "apps.documents.services.Document.objects.create",
                side_effect=IntegrityError("private-database-details"),
            ),
            self.assertLogs("apps.documents.views", level="ERROR"),
        ):
            response = self.client.post(
                self.url,
                self.make_payload(),
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertNotIn("private-database-details", response.content.decode())
        self.assertFalse(Document.objects.exists())
        storage.delete.assert_not_called()
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
