from io import BytesIO
from unittest.mock import ANY, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase
from PIL import Image
from rest_framework.test import APIRequestFactory

from .models import Document
from .permissions import DocumentRolePermission
from .serializers import DocumentUploadSerializer
from .services import create_document

User = get_user_model()


class DocumentModelTests(TestCase):
    def test_document_stores_metadata_and_creator(self):
        creator = User.objects.create_user(
            username="document-editor",
            role=User.Role.EDITOR,
        )

        document = Document.objects.create(
            title="Sample image",
            original_filename="photo.png",
            object_key="documents/sample-image.png",
            content_type="image/png",
            file_size=1024,
            creator=creator,
        )

        document.refresh_from_db()

        self.assertEqual(document.title, "Sample image")
        self.assertEqual(document.original_filename, "photo.png")
        self.assertEqual(
            document.object_key,
            "documents/sample-image.png",
        )
        self.assertEqual(document.content_type, "image/png")
        self.assertEqual(document.file_size, 1024)
        self.assertEqual(document.creator, creator)
        self.assertIsNotNone(document.created_at)
        self.assertIsNotNone(document.updated_at)


class DocumentPermissionTests(SimpleTestCase):
    def test_each_role_has_expected_permissions(self):
        allowed_methods = {
            User.Role.ADMIN: {
                "GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE",
            },
            User.Role.EDITOR: {
                "GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH",
            },
            User.Role.VIEWER: {"GET", "HEAD", "OPTIONS"},
            "unknown": set(),
        }

        factory = APIRequestFactory()
        permission = DocumentRolePermission()

        for role, allowed in allowed_methods.items():
            user = User(username=f"user-{role}", role=role)

            for method in (
                "GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE",
            ):
                with self.subTest(role=role, method=method):
                    request = factory.generic(method, "/api/v1/documents/")
                    request.user = user

                    self.assertEqual(
                        permission.has_permission(request, None),
                        method in allowed,
                    )

    def test_anonymous_user_cannot_access_documents(self):
        factory = APIRequestFactory()
        permission = DocumentRolePermission()

        for method in (
            "GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE",
        ):
            with self.subTest(method=method):
                request = factory.generic(method, "/api/v1/documents/")
                request.user = AnonymousUser()

                self.assertFalse(
                    permission.has_permission(request, None),
                )


class DocumentUploadValidationTests(SimpleTestCase):
    def make_image_file(self, image_format="PNG"):
        buffer = BytesIO()
        image = Image.new("RGB", (10, 10), color="blue")
        image.save(buffer, format=image_format)

        return SimpleUploadedFile(
            name=f"image.{image_format.lower()}",
            content=buffer.getvalue(),
            content_type=f"image/{image_format.lower()}",
        )

    def test_png_and_jpeg_are_accepted(self):
        for image_format in ("PNG", "JPEG"):
            with self.subTest(image_format=image_format):
                serializer = DocumentUploadSerializer(
                    data={
                        "title": "Sample image",
                        "file": self.make_image_file(image_format),
                    },
                )

                self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_non_image_with_png_name_and_mime_type_is_rejected(self):
        uploaded_file = SimpleUploadedFile(
            name="fake.png",
            content=b"This is not an image.",
            content_type="image/png",
        )
        serializer = DocumentUploadSerializer(
            data={"title": "Fake image", "file": uploaded_file},
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("file", serializer.errors)

    def test_gif_is_rejected(self):
        serializer = DocumentUploadSerializer(
            data={
                "title": "GIF image",
                "file": self.make_image_file("GIF"),
            },
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("file", serializer.errors)

    def test_image_size_limit(self):
        max_size = 10 * 1024 * 1024
        image_bytes = self.make_image_file().read()

        for size, expected_valid in (
            (max_size, True),
            (max_size + 1, False),
        ):
            with self.subTest(size=size):
                uploaded_file = SimpleUploadedFile(
                    name="large.png",
                    content=image_bytes + b"\0" * (size - len(image_bytes)),
                    content_type="image/png",
                )
                serializer = DocumentUploadSerializer(
                    data={"title": "Large image", "file": uploaded_file},
                )

                self.assertEqual(
                    serializer.is_valid(),
                    expected_valid,
                    serializer.errors,
                )

                if not expected_valid:
                    self.assertIn("file", serializer.errors)


class DocumentCreationTests(TestCase):
    def setUp(self):
        self.creator = User.objects.create_user(
            username="storage-test-editor",
            role=User.Role.EDITOR,
        )

        buffer = BytesIO()
        Image.new("RGB", (10, 10), color="blue").save(
            buffer,
            format="PNG",
        )
        uploaded_file = SimpleUploadedFile(
            name="sample.png",
            content=buffer.getvalue(),
            content_type="image/png",
        )

        serializer = DocumentUploadSerializer(
            data={"title": "Sample image", "file": uploaded_file},
        )
        serializer.is_valid(raise_exception=True)
        self.validated_file = serializer.validated_data["file"]

    @patch("apps.documents.services.default_storage")
    def test_upload_creates_document_with_storage_key(self, storage):
        creator = User.objects.create_user(
            username="upload-editor",
            role=User.Role.EDITOR,
        )

        buffer = BytesIO()
        Image.new("RGB", (10, 10), color="blue").save(
            buffer,
            format="PNG",
        )
        uploaded_file = SimpleUploadedFile(
            name="sample.png",
            content=buffer.getvalue(),
            content_type="image/png",
        )

        serializer = DocumentUploadSerializer(
            data={"title": "Uploaded image", "file": uploaded_file},
        )
        serializer.is_valid(raise_exception=True)
        validated_file = serializer.validated_data["file"]

        storage.save.return_value = "documents/stored-image.png"

        document = create_document(
            title=serializer.validated_data["title"],
            uploaded_file=validated_file,
            creator=creator,
        )

        storage.save.assert_called_once_with(ANY, validated_file)

        document.refresh_from_db()

        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(document.title, "Uploaded image")
        self.assertEqual(document.original_filename, "sample.png")
        self.assertEqual(
            document.object_key,
            "documents/stored-image.png",
        )
        self.assertEqual(document.content_type, "image/png")
        self.assertEqual(document.file_size, uploaded_file.size)
        self.assertEqual(document.creator, creator)

    @patch("apps.documents.services.default_storage")
    def test_database_failure_deletes_uploaded_file(self, storage):
        creator = User.objects.create_user(
            username="upload-failure-editor",
            role=User.Role.EDITOR,
        )

        buffer = BytesIO()
        Image.new("RGB", (10, 10), color="blue").save(
            buffer,
            format="PNG",
        )
        uploaded_file = SimpleUploadedFile(
            name="sample.png",
            content=buffer.getvalue(),
            content_type="image/png",
        )

        serializer = DocumentUploadSerializer(
            data={"title": "Failed upload", "file": uploaded_file},
        )
        serializer.is_valid(raise_exception=True)

        storage.save.return_value = "documents/orphan.png"

        with patch(
            "apps.documents.services.Document.objects.create",
            side_effect=IntegrityError("Database write failed"),
        ), self.assertRaisesMessage(
            IntegrityError,
            "Database write failed",
        ):
            create_document(
                title=serializer.validated_data["title"],
                uploaded_file=serializer.validated_data["file"],
                creator=creator,
            )

        storage.delete.assert_called_once_with("documents/orphan.png")
        self.assertFalse(Document.objects.exists())

    @patch("apps.documents.services.default_storage")
    def test_storage_failure_does_not_create_document(self, storage):
        storage.save.side_effect = OSError("Storage unavailable")

        with self.assertRaisesMessage(OSError, "Storage unavailable"):
            create_document(
                title="Sample image",
                uploaded_file=self.validated_file,
                creator=self.creator,
            )

        self.assertFalse(Document.objects.exists())
        storage.delete.assert_called_once_with(storage.save.call_args.args[0])

    def test_lost_upload_acknowledgement_removes_persisted_object(self):
        storage = InMemoryStorage()
        save = storage.save

        def save_then_fail(name, content):
            save(name, content)
            raise OSError("Upload acknowledgement was lost")

        with (
            patch("apps.documents.services.default_storage", storage),
            patch.object(storage, "save", side_effect=save_then_fail),
            self.assertRaisesMessage(OSError, "Upload acknowledgement was lost"),
        ):
            create_document(
                title="Lost acknowledgement",
                uploaded_file=self.validated_file,
                creator=self.creator,
            )

        self.assertFalse(Document.objects.exists())
        self.assertEqual(storage.listdir("documents")[1], [])

    @patch("apps.documents.services.default_storage")
    def test_upload_cleanup_failure_preserves_original_storage_error(self, storage):
        storage.save.side_effect = OSError("Upload acknowledgement was lost")
        storage.delete.side_effect = OSError("private-cleanup-details")

        with self.assertLogs("apps.documents.services", level="ERROR") as logs:
            with self.assertRaisesMessage(OSError, "Upload acknowledgement was lost"):
                create_document(
                    title="Failed cleanup",
                    uploaded_file=self.validated_file,
                    creator=self.creator,
                )

        self.assertFalse(Document.objects.exists())
        storage.delete.assert_called_once_with(storage.save.call_args.args[0])
        self.assertNotIn("private-cleanup-details", "\n".join(logs.output))

    @patch("apps.documents.services.default_storage")
    def test_cleanup_failure_preserves_database_error(self, storage):
        storage.save.return_value = "documents/orphan.png"
        storage.delete.side_effect = OSError("Cleanup failed")

        with patch(  # noqa: SIM117
            "apps.documents.services.Document.objects.create",
            side_effect=IntegrityError("Database write failed"),
        ), self.assertLogs("apps.documents.services", level="ERROR"):
            with self.assertRaisesMessage(
                IntegrityError,
                "Database write failed",
            ):
                create_document(
                    title="Sample image",
                    uploaded_file=self.validated_file,
                    creator=self.creator,
                )

        storage.delete.assert_called_once_with("documents/orphan.png")
        self.assertFalse(Document.objects.exists())
