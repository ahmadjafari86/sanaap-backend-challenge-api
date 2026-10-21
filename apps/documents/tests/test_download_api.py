from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import storages
from django.db import OperationalError
from django.test import override_settings
from minio.error import MinioException
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken
from urllib3 import PoolManager
from urllib3.exceptions import HTTPError

from ..models import Document

User = get_user_model()


class OfflinePoolManager(PoolManager):
    def urlopen(self, *args, **kwargs):
        raise AssertionError("Local URL signing must not contact object storage.")


class ForbiddenURLCache:
    def get(self, *args, **kwargs):
        raise AssertionError("Presigned URLs must not be read from cache.")

    def set(self, *args, **kwargs):
        raise AssertionError("Presigned URLs must not be written to cache.")


class DocumentDownloadAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="download-owner")
        cls.readers = {
            role: User.objects.create_user(username=f"download-{role}", role=role)
            for role in User.Role.values
        }
        cls.document = Document.objects.create(
            title="Downloadable image",
            original_filename="example.png",
            object_key="documents/downloadable.png",
            content_type="image/png",
            file_size=128,
            creator=cls.owner,
        )
        cls.url = f"/api/v1/documents/{cls.document.pk}/download/"

    def setUp(self):
        test_storages = deepcopy(settings.STORAGES)
        options = test_storages["default"]["OPTIONS"]
        options.update(
            MINIO_ENDPOINT="internal-minio.example.test:9000",
            MINIO_USE_HTTPS=False,
            MINIO_EXTERNAL_ENDPOINT="download.example.test:19000",
            MINIO_EXTERNAL_ENDPOINT_USE_HTTPS=False,
            MINIO_ACCESS_KEY="download-test-access",
            MINIO_SECRET_KEY="download-test-secret-not-a-real-credential",
            MINIO_HTTP_CLIENT=OfflinePoolManager(),
        )
        self.bucket = options["MINIO_DEFAULT_BUCKET"]
        storage_settings = override_settings(STORAGES=test_storages)
        storage_settings.enable()
        self.addCleanup(storage_settings.disable)
        self.storage = storages["default"]

        storage_patch = patch("apps.documents.services.default_storage", self.storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        cache_patch = patch("django_minio_backend.models.cache", ForbiddenURLCache())
        cache_patch.start()
        self.addCleanup(cache_patch.stop)

    def authenticate(self, role=User.Role.VIEWER):
        self.client.force_authenticate(user=self.readers[role])

    def assert_signed_download(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data), {"download_url", "expires_in"})
        self.assertEqual(response.data["expires_in"], 60)
        parsed = urlsplit(response.data["download_url"])
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "http")
        self.assertEqual(parsed.netloc, "download.example.test:19000")
        self.assertEqual(unquote(parsed.path), f"/{self.bucket}/{self.document.object_key}")
        self.assertEqual(query["X-Amz-Expires"], ["60"])
        self.assertTrue(query.get("X-Amz-Signature"))
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        return query

    def test_all_roles_can_request_signed_downloads_for_other_creators(self):
        for role in User.Role.values:
            with self.subTest(role=role):
                self.authenticate(role)

                response = self.client.get(self.url)

                self.assert_signed_download(response)

    def test_anonymous_request_is_rejected_before_signing(self):
        with patch.object(self.storage, "url", side_effect=AssertionError("Unauthorized signing")):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("no-store", response["Cache-Control"])

    def test_unknown_role_is_rejected_before_signing(self):
        reader = self.readers[User.Role.VIEWER]
        reader.role = "unknown"
        self.client.force_authenticate(user=reader)
        with patch.object(self.storage, "url", side_effect=AssertionError("Unauthorized signing")):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_jwt_is_rejected_before_signing(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid-token")
        with patch.object(self.storage, "url", side_effect=AssertionError("Unauthorized signing")):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inactive_jwt_user_is_rejected_before_signing(self):
        reader = self.readers[User.Role.VIEWER]
        token = RefreshToken.for_user(reader).access_token
        reader.is_active = False
        reader.save(update_fields=["is_active"])
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        with patch.object(self.storage, "url", side_effect=AssertionError("Unauthorized signing")):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_document_is_rejected_before_signing(self):
        self.authenticate()
        with patch.object(self.storage, "url", side_effect=AssertionError("Missing document signing")):
            response = self.client.get("/api/v1/documents/999999/download/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Document.objects.filter(pk=self.document.pk).exists())

    def test_repeated_requests_sign_again_without_url_cache(self):
        self.authenticate()
        first_time = datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
        with patch("minio.time.utcnow", return_value=first_time):
            first = self.client.get(self.url)
        with patch("minio.time.utcnow", return_value=first_time + timedelta(seconds=1)):
            second = self.client.get(self.url)

        first_query = self.assert_signed_download(first)
        second_query = self.assert_signed_download(second)
        self.assertEqual(first_query["X-Amz-Date"], ["20261020T100000Z"])
        self.assertEqual(second_query["X-Amz-Date"], ["20261020T100001Z"])
        self.assertNotEqual(first.data["download_url"], second.data["download_url"])

    def test_query_parameters_cannot_extend_url_lifetime(self):
        self.authenticate()

        response = self.client.get(self.url, {"expires_in": 86400, "expires": 86400})

        self.assert_signed_download(response)

    def test_viewer_can_request_download_with_jwt(self):
        token = RefreshToken.for_user(self.readers[User.Role.VIEWER]).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        response = self.client.get(self.url)

        self.assert_signed_download(response)

    def test_logout_does_not_revoke_existing_access_token(self):
        refresh = RefreshToken.for_user(self.readers[User.Role.VIEWER])
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        self.assert_signed_download(self.client.get(self.url))

        logout = self.client.post(
            "/api/v1/auth/logout/", {"refresh": str(refresh)}, format="json",
        )

        self.assertEqual(logout.status_code, status.HTTP_200_OK)
        self.assert_signed_download(self.client.get(self.url))

    def test_role_change_blocks_new_url_issuance(self):
        reader = self.readers[User.Role.VIEWER]
        token = RefreshToken.for_user(reader).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assert_signed_download(self.client.get(self.url))
        reader.role = "unknown"
        reader.save(update_fields=["role"])
        with patch.object(self.storage, "url", side_effect=AssertionError("Unauthorized signing")):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_signing_outages_return_safe_service_unavailable_response(self):
        self.authenticate()
        private_message = "private-presigned-url-and-credential-details"
        for error in (OSError(private_message), MinioException(private_message), HTTPError(private_message)):
            with self.subTest(error=type(error).__name__):
                with (
                    patch.object(self.storage, "url", side_effect=error),
                    self.assertLogs("apps.documents.views", level="ERROR") as logs,
                ):
                    response = self.client.get(self.url)

                self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
                self.assertIn("detail", response.data)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertNotIn(private_message, response.content.decode())
                self.assertNotIn(private_message, "\n".join(logs.output))

    def test_database_outage_returns_safe_service_unavailable_response(self):
        self.authenticate()
        with (
            patch("django.db.models.query.QuerySet.get", side_effect=OperationalError("private-database-details")),
            self.assertLogs("apps.documents.views", level="ERROR"),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertNotIn("private-database-details", response.content.decode())

    def test_options_and_unsupported_methods_do_not_generate_urls(self):
        self.authenticate(User.Role.ADMIN)
        with patch.object(self.storage, "url", side_effect=AssertionError("Unexpected signing")):
            options = self.client.options(self.url)
            self.assertEqual(options.status_code, status.HTTP_200_OK)
            for method in ("HEAD", "POST", "PUT", "PATCH", "DELETE"):
                with self.subTest(method=method):
                    response = self.client.generic(method, self.url)
                    self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
