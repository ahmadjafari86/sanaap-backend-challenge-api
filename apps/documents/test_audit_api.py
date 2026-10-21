import json
from io import BytesIO
from tempfile import TemporaryDirectory
from contextlib import nullcontext

from .background import staging_storage
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Document, DocumentAuditEvent
from .services import delete_document, update_document

User = get_user_model()


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class DocumentAuditAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="audit-owner")
        cls.editor = User.objects.create_user(username="audit-editor", role="editor")
        cls.admin = User.objects.create_user(username="audit-admin", role="admin")
        cls.viewer = User.objects.create_user(username="audit-viewer", role="viewer")
        buffer = BytesIO()
        Image.new("RGB", (10, 10), color="blue").save(buffer, format="PNG")
        cls.image_content = buffer.getvalue()
        cls.document = Document.objects.create(
            title="Original image", creator=cls.owner, original_filename="original.png",
            object_key="documents/audit-original.png", content_type="image/png",
            file_size=len(cls.image_content),
        )
        cls.collection = "/api/v1/documents/"
        cls.detail = f"{cls.collection}{cls.document.pk}/"
        cls.download = cls.detail + "download/"

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        setting = override_settings(DOCUMENT_STAGING_ROOT=self.directory.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.events = DocumentAuditEvent.objects
        self.started_at = timezone.now()
        self.storage = InMemoryStorage()
        self.storage.save(self.document.object_key, ContentFile(self.image_content))
        storage_patch = patch("apps.documents.services.default_storage", self.storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        self.client.force_authenticate(user=self.editor)

    def upload(self):
        return SimpleUploadedFile("upload.png", self.image_content, content_type="image/png")

    def assert_event(self, actor_id, action, document_id, outcome, status_code):
        self.assertEqual(
            list(self.events.values_list("actor_id", "action", "document_id", "outcome", "status_code")),
            [(actor_id, action, document_id, outcome, status_code)],
        )
        occurred_at = self.events.get().occurred_at
        self.assertTrue(timezone.is_aware(occurred_at))
        self.assertGreaterEqual(occurred_at, self.started_at)
        self.assertLessEqual(occurred_at, timezone.now())

    def test_admin_and_editor_creation_records_real_actor_and_new_document(self):
        for actor in (self.admin, self.editor):
            with self.subTest(role=actor.role):
                self.events.all().delete()
                self.client.force_authenticate(user=actor)
                response = self.client.post(
                    self.collection, {"title": "Audit upload", "file": self.upload()}, format="multipart",
                )
                self.assertEqual(response.status_code, 202)
                self.assert_event(actor.pk, "create", response.data["id"], "success", 202)

    def test_put_and_patch_record_the_editor_rather_than_document_creator(self):
        for method in ("put", "patch"):
            with self.subTest(method=method):
                self.events.all().delete()
                response = getattr(self.client, method)(self.detail, {"title": "Changed"}, format="json")
                self.assertEqual(response.status_code, 200)
                self.assert_event(self.editor.pk, "update", self.document.pk, "success", 200)

    def test_file_replacement_has_one_update_event(self):
        response = self.client.patch(self.detail, {"file": self.upload()}, format="multipart")
        self.assertEqual(response.status_code, 200)
        self.assert_event(self.editor.pk, "update", self.document.pk, "success", 200)

    def test_audit_identifiers_survive_document_and_actor_deletion(self):
        created = self.client.post(
            self.collection, {"title": "Delete later", "file": self.upload()}, format="multipart",
        )
        self.assertEqual(created.status_code, 202)
        document_id, actor_id = created.data["id"], self.editor.pk
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(f"{self.collection}{document_id}/")
        self.assertEqual(response.status_code, 204)
        self.editor.delete()
        self.assertEqual(
            set(self.events.values_list("actor_id", "action", "document_id", "outcome", "status_code")),
            {(actor_id, "create", document_id, "success", 202),
             (self.admin.pk, "delete", document_id, "success", 204)},
        )

    def test_all_roles_record_url_issuance_without_storing_bearer_url(self):
        bearer_url = "https://minio.example.test/private.png?X-Amz-Signature=private-signature"
        for actor in (self.admin, self.editor, self.viewer):
            with self.subTest(role=actor.role):
                self.events.all().delete()
                self.client.force_authenticate(user=actor)
                with patch.object(self.storage, "url", return_value=bearer_url):
                    response = self.client.get(self.download)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["download_url"], bearer_url)
                self.assert_event(actor.pk, "download_url", self.document.pk, "success", 200)
                self.assertNotIn("private-signature", json.dumps(list(self.events.values()), default=str))

    def test_denied_viewer_mutations_record_failure(self):
        self.client.force_authenticate(user=self.viewer)
        for method, path, action, document_id in (
            ("post", self.collection, "create", None),
            ("patch", self.detail, "update", self.document.pk),
            ("put", self.detail, "update", self.document.pk),
            ("delete", self.detail, "delete", self.document.pk),
        ):
            with self.subTest(method=method):
                self.events.all().delete()
                response = getattr(self.client, method)(path, {}, format="json")
                self.assertEqual(response.status_code, 403)
                self.assert_event(self.viewer.pk, action, document_id, "failure", 403)

    def test_anonymous_attempts_record_null_actor(self):
        self.client.force_authenticate(user=None)
        for method, path, action, document_id in (
            ("post", self.collection, "create", None),
            ("patch", self.detail, "update", self.document.pk),
            ("delete", self.detail, "delete", self.document.pk),
            ("get", self.download, "download_url", self.document.pk),
        ):
            with self.subTest(method=method):
                self.events.all().delete()
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 401)
                self.assert_event(None, action, document_id, "failure", 401)

    def test_invalid_jwt_attempt_does_not_derive_actor_from_request_data(self):
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid-private-token")
        response = self.client.post(self.collection, {"actor_id": self.admin.pk}, format="json")
        self.assertEqual(response.status_code, 401)
        self.assert_event(None, "create", None, "failure", 401)

    def test_unknown_application_role_is_audited_without_granting_access(self):
        self.editor.role = "unknown"
        response = self.client.get(self.download)
        self.assertEqual(response.status_code, 403)
        self.assert_event(self.editor.pk, "download_url", self.document.pk, "failure", 403)

    def test_bad_payload_and_image_record_validation_failure(self):
        for payload in ({"title": " "}, {"title": "Fake", "file": SimpleUploadedFile("fake.png", b"fake")}):
            with self.subTest(fields=list(payload)):
                self.events.all().delete()
                response = self.client.post(self.collection, payload, format="multipart")
                self.assertEqual(response.status_code, 400)
                self.assert_event(self.editor.pk, "create", None, "failure", 400)

    def test_malformed_json_records_parse_failure(self):
        response = self.client.patch(self.detail, b'{"title":', content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assert_event(self.editor.pk, "update", self.document.pk, "failure", 400)

    def test_missing_document_attempts_retain_requested_identifier(self):
        self.client.force_authenticate(user=self.admin)
        for method, suffix, action in (("patch", "", "update"), ("delete", "", "delete"), ("get", "download/", "download_url")):
            with self.subTest(action=action):
                self.events.all().delete()
                response = getattr(self.client, method)(f"{self.collection}999999/{suffix}", {}, format="json")
                self.assertEqual(response.status_code, 404)
                self.assert_event(self.admin.pk, action, 999999, "failure", 404)

    def test_route_id_outside_database_range_cannot_break_failure_logging(self):
        response = self.client.get(f"{self.collection}{2**80}/download/")
        self.assertEqual(response.status_code, 404)
        self.assert_event(self.editor.pk, "download_url", None, "failure", 404)

    def test_storage_failures_record_safe_failure_events(self):
        for method, path, action, document_id, storage_method in (
            ("post", self.collection, "create", None, "save"),
            ("patch", self.detail, "update", self.document.pk, "save"),
            ("get", self.download, "download_url", self.document.pk, "url"),
        ):
            with self.subTest(action=action):
                self.events.all().delete()
                target_storage = staging_storage() if method == "post" else self.storage
                with (
                    patch.object(target_storage, storage_method, side_effect=OSError("private-storage-secret")),
                    patch("apps.documents.background.staging_storage", return_value=target_storage) if method == "post" else nullcontext(),
                    self.assertLogs("apps.documents.views", level="ERROR"),
                ):
                    payload = {"title": "Attempt", "file": self.upload()} if method != "get" else {}
                    response = getattr(self.client, method)(path, payload, format="multipart")
                self.assertEqual(response.status_code, 503)
                self.assert_event(self.editor.pk, action, document_id, "failure", 503)
                self.document.refresh_from_db()
                self.assertEqual(self.document.title, "Original image")

    def test_database_failure_does_not_leave_success_audit(self):
        with (
            patch("apps.documents.services.Document.objects.create", side_effect=IntegrityError("private-db-secret")),
            self.assertLogs("apps.documents.views", level="ERROR"),
        ):
            response = self.client.post(
                self.collection, {"title": "Failed", "file": self.upload()}, format="multipart",
            )
        self.assertEqual(response.status_code, 503)
        self.assert_event(self.editor.pk, "create", None, "failure", 503)
        self.assertEqual(self.storage.listdir("documents")[1], ["audit-original.png"])

    def test_audit_write_failure_rolls_back_mutation_and_new_file(self):
        write = self.events.create

        def fail_after_success_insert(**values):
            event = write(**values)
            if values["outcome"] == "success":
                raise IntegrityError("private-audit-secret")
            return event

        for method, path, action, document_id in (
            ("post", self.collection, "create", None),
            ("patch", self.detail, "update", self.document.pk),
            ("delete", self.detail, "delete", self.document.pk),
            ("get", self.download, "download_url", self.document.pk),
        ):
            with self.subTest(action=action):
                self.events.all().delete()
                self.client.force_authenticate(user=self.admin)
                with (
                    patch.object(self.events, "create", side_effect=fail_after_success_insert),
                    patch.object(self.storage, "url", return_value="https://minio.example.test/signed"),
                    self.captureOnCommitCallbacks(execute=True),
                    self.assertLogs("apps.documents.views", level="ERROR"),
                ):
                    payload = {"title": "Must roll back", "file": self.upload()} if method in ("post", "patch") else {}
                    response = getattr(self.client, method)(path, payload, format="multipart")
                self.assertEqual(response.status_code, 503)
                self.assert_event(self.admin.pk, action, document_id, "failure", 503)
                self.document.refresh_from_db()
                self.assertEqual(self.document.title, "Original image")
                self.assertEqual(Document.objects.count(), 1)
                self.assertEqual(self.storage.listdir("documents")[1], ["audit-original.png"])

    def test_rolled_back_update_and_delete_have_no_committed_success_events(self):
        for operation in ("update", "delete"):
            with self.subTest(operation=operation):
                with self.assertRaisesMessage(RuntimeError, "Roll back"):
                    with transaction.atomic():
                        if operation == "update":
                            update_document(document_id=self.document.pk, title="Temporary", actor_id=self.editor.pk)
                        else:
                            delete_document(document_id=self.document.pk, actor_id=self.admin.pk)
                        self.assertEqual(self.events.count(), 1)
                        raise RuntimeError("Roll back")
                self.assertEqual(self.events.count(), 0)
                self.document.refresh_from_db()
                self.assertEqual(self.document.title, "Original image")
                self.assertTrue(self.storage.exists(self.document.object_key))

    def test_failed_post_commit_cleanup_keeps_successful_deletion_event(self):
        self.client.force_authenticate(user=self.admin)
        with (
            patch.object(self.storage, "delete", side_effect=OSError("private-cleanup-secret")),
            self.assertLogs("apps.documents.services", level="ERROR"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.delete(self.detail)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Document.objects.exists())
        self.assert_event(self.admin.pk, "delete", self.document.pk, "success", 204)

    def test_failure_audit_errors_preserve_denial_without_poisoning_connection(self):
        self.client.force_authenticate(user=self.viewer)
        write = self.events.create

        def violate_not_null(**values):
            return write(**{**values, "status_code": None})

        with patch.object(self.events, "create", side_effect=violate_not_null), self.assertLogs("apps.documents.audit", level="ERROR"):
            response = self.client.delete(self.detail)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Document.objects.filter(pk=self.document.pk).exists())
        self.assertEqual(self.events.count(), 0)

    def test_failure_audit_does_not_log_private_exception_details(self):
        self.client.force_authenticate(user=self.viewer)
        with (
            patch.object(self.events, "create", side_effect=IntegrityError("private-audit-password-token-url")),
            self.assertLogs("apps.documents.audit", level="ERROR") as logs,
        ):
            response = self.client.delete(self.detail)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("private-audit-password-token-url", "\n".join(logs.output))

    def test_audit_write_and_upload_cleanup_errors_do_not_log_secrets(self):
        secret = "private-audit-password-token-url"
        with (
            patch.object(self.events, "create", side_effect=IntegrityError(secret)),
            patch.object(self.storage, "delete", side_effect=OSError("private-cleanup-password")),
            self.assertLogs("apps.documents", level="ERROR") as logs,
        ):
            response = self.client.post(
                self.collection, {"title": "Failed", "file": self.upload()}, format="multipart",
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(self.events.count(), 0)
        self.assertNotIn(secret, "\n".join(logs.output))
        self.assertNotIn("private-cleanup-password", "\n".join(logs.output))

    def test_unexpected_service_error_records_failure_before_framework_500(self):
        self.client.raise_request_exception = False
        with (
            patch("apps.documents.views.stage_document", side_effect=RuntimeError("Unexpected staging failure")),
            self.assertLogs("django.request", level="ERROR"),
        ):
            response = self.client.post(
                self.collection, {"title": "Failed", "file": self.upload()}, format="multipart",
            )
        self.assertEqual(response.status_code, 500)
        self.assert_event(self.editor.pk, "create", None, "failure", 500)

    def test_payload_secrets_and_forged_audit_fields_are_not_persisted(self):
        token = RefreshToken.for_user(self.editor).access_token
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = self.client.post(self.collection, {
            "title": "private-title-secret", "file": self.upload(), "password": "private-password-secret",
            "actor_id": self.admin.pk, "document_id": 12345, "outcome": "forged-outcome",
            "download_url": "https://private-url-secret.example.test/", "action": "forged-action",
        }, format="multipart")
        self.assertEqual(response.status_code, 202)
        self.assert_event(self.editor.pk, "create", response.data["id"], "success", 202)
        stored = json.dumps(list(self.events.values()), default=str)
        for secret in (str(token), "private-title-secret", "private-password-secret", "private-url-secret", "forged-outcome", "forged-action", "upload.png", "object_key"):
            self.assertNotIn(secret, stored)

    def test_metadata_reads_options_and_unsupported_methods_do_not_create_events(self):
        for method, path in (
            ("get", self.collection), ("get", self.detail), ("head", self.detail),
            ("options", self.collection), ("options", self.detail), ("options", self.download),
            ("head", self.download), ("post", self.download),
        ):
            with self.subTest(method=method, path=path):
                response = getattr(self.client, method)(path)
                self.assertIn(response.status_code, (200, 405))
        self.assertEqual(self.events.count(), 0)
