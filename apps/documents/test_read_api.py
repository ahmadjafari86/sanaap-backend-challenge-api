from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Document

User = get_user_model()


class DocumentReadAPITests(APITestCase):
    url = "/api/v1/documents/"

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username="document-owner")
        cls.other_owner = User.objects.create_user(username="other-document-owner")
        cls.readers = {
            role: User.objects.create_user(username=f"reader-{role}", role=role)
            for role in User.Role.values
        }
        metadata = {
            "original_filename": "sample.png",
            "content_type": "image/png",
            "file_size": 128,
        }
        cls.first = Document.objects.create(
            title="First image",
            object_key="documents/first.png",
            creator=cls.owner,
            **metadata,
        )
        cls.second = Document.objects.create(
            title="Older image",
            object_key="documents/second.png",
            creator=cls.other_owner,
            **metadata,
        )
        cls.third = Document.objects.create(
            title="Third image",
            object_key="documents/third.png",
            creator=cls.other_owner,
            **metadata,
        )
        Document.objects.filter(pk__in=(cls.first.pk, cls.third.pk)).update(
            created_at=datetime(2026, 2, 2, tzinfo=UTC),
        )
        Document.objects.filter(pk=cls.second.pk).update(
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        cls.detail_url = f"{cls.url}{cls.first.pk}/"

    def test_all_roles_can_list_documents_from_other_creators(self):
        for role, reader in self.readers.items():
            with self.subTest(role=role):
                self.client.force_authenticate(user=reader)

                response = self.client.get(self.url)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(
                    {document["id"] for document in response.data},
                    {self.first.pk, self.second.pk, self.third.pk},
                )
                self.assertEqual(
                    {document["creator"] for document in response.data},
                    {self.owner.pk, self.other_owner.pk},
                )

    def test_all_roles_can_retrieve_another_users_document(self):
        for role, reader in self.readers.items():
            with self.subTest(role=role):
                self.client.force_authenticate(user=reader)

                response = self.client.get(self.detail_url)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data["id"], self.first.pk)
                self.assertEqual(response.data["title"], "First image")
                self.assertEqual(response.data["creator"], self.owner.pk)

    def test_list_orders_by_creation_time_with_descending_id_tie_breaker(self):
        self.client.force_authenticate(user=self.readers[User.Role.VIEWER])

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [document["id"] for document in response.data],
            [self.third.pk, self.first.pk, self.second.pk],
        )

    def test_empty_list_returns_an_empty_array(self):
        self.client.force_authenticate(user=self.readers[User.Role.VIEWER])
        Document.objects.all().delete()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_missing_document_returns_not_found(self):
        self.client.force_authenticate(user=self.readers[User.Role.VIEWER])
        self.first.delete()

        response = self.client.get(self.detail_url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Document.objects.count(), 2)

    def test_anonymous_reads_are_rejected(self):
        for url in (self.url, self.detail_url):
            for method in ("get", "head", "options"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url)
                    self.assertEqual(
                        response.status_code,
                        status.HTTP_401_UNAUTHORIZED,
                    )

    def test_unknown_role_cannot_read_documents(self):
        user = User.objects.create_user(username="invalid-reader", role="unknown")
        self.client.force_authenticate(user=user)

        for url in (self.url, self.detail_url):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_jwt_cannot_read_documents(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer invalid-token")

        for url in (self.url, self.detail_url):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_viewer_can_read_with_jwt_authentication(self):
        access_token = RefreshToken.for_user(
            self.readers[User.Role.VIEWER],
        ).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        for url in (self.url, self.detail_url):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_inactive_user_jwt_cannot_read_documents(self):
        reader = self.readers[User.Role.VIEWER]
        access_token = RefreshToken.for_user(reader).access_token
        reader.is_active = False
        reader.save(update_fields=["is_active"])
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")

        for url in (self.url, self.detail_url):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_all_roles_can_use_head_and_options(self):
        for role, reader in self.readers.items():
            self.client.force_authenticate(user=reader)
            for url in (self.url, self.detail_url):
                for method in ("head", "options"):
                    with self.subTest(role=role, url=url, method=method):
                        response = getattr(self.client, method)(url)
                        self.assertEqual(response.status_code, status.HTTP_200_OK)
                        if method == "head":
                            self.assertEqual(response.content, b"")

    def test_reads_expose_metadata_without_storage_keys_or_download_urls(self):
        self.client.force_authenticate(user=self.readers[User.Role.VIEWER])
        fields = {
            "id",
            "title",
            "original_filename",
            "content_type",
            "file_size",
            "creator",
            "created_at",
            "updated_at",
        }

        detail = self.client.get(self.detail_url)
        listing = self.client.get(self.url)

        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(set(detail.data), fields)
        self.assertEqual(detail.data["content_type"], "image/png")
        self.assertEqual(detail.data["file_size"], 128)
        self.assertEqual(detail.data["original_filename"], "sample.png")
        for document in listing.data:
            self.assertEqual(set(document), fields)
