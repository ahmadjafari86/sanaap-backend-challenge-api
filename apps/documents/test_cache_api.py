from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event, current_thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.cache.backends.locmem import LocMemCache
from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.db import OperationalError, close_old_connections, transaction
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Document
from .test_update_api import image_bytes, image_upload

User = get_user_model()
TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "document-metadata-tests",
        "TIMEOUT": 300,
    },
}


class MetadataCacheFixture:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.owner = User.objects.create_user(username="cache-owner")
        self.readers = {
            role: User.objects.create_user(username=f"cache-{role}", role=role)
            for role in User.Role.values
        }
        self.storage = InMemoryStorage()
        self.storage.save("documents/cached.png", ContentFile(image_bytes()))
        storage_patch = patch("apps.documents.services.default_storage", self.storage)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        self.document = Document.objects.create(
            title="Cached original",
            original_filename="cached.png",
            object_key="documents/cached.png",
            content_type="image/png",
            file_size=len(image_bytes()),
            creator=self.owner,
        )
        self.url = f"/api/v1/documents/{self.document.pk}/"
        self.client = APIClient()
        self.client.force_authenticate(user=self.readers[User.Role.VIEWER])

    def warm_metadata(self, url=None):
        keys = []
        original_set = LocMemCache.set

        def record_set(backend, key, value, *args, **kwargs):
            keys.append(key)
            return original_set(backend, key, value, *args, **kwargs)

        with patch.object(LocMemCache, "set", new=record_set):
            response = self.client.get(url or self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(keys), 1, "A cold detail read must populate the metadata cache.")
        return keys[0], response.data


@override_settings(CACHES=TEST_CACHES)
class DocumentMetadataCacheTests(MetadataCacheFixture, APITestCase):
    def test_shared_cache_hits_need_only_the_database_version_probe(self):
        with self.assertNumQueries(2):
            cold = self.client.get(self.url)
        self.assertEqual(cold.status_code, status.HTTP_200_OK)
        self.assertEqual(set(cold.data), {
            "id", "title", "original_filename", "content_type", "file_size",
            "creator", "created_at", "updated_at",
        })
        for role, reader in self.readers.items():
            with self.subTest(role=role):
                self.client.force_authenticate(user=reader)
                with self.assertNumQueries(1):
                    hit = self.client.get(self.url)
                self.assertEqual(hit.status_code, status.HTTP_200_OK)
                self.assertEqual(hit.data, cold.data)

    def test_metadata_expires_at_sixty_seconds_even_with_a_longer_cache_default(self):
        with patch("django.core.cache.backends.locmem.time.time", return_value=1000):
            self.warm_metadata()

        with patch("django.core.cache.backends.locmem.time.time", return_value=1059):
            with self.assertNumQueries(1):
                hit = self.client.get(self.url)
        self.assertEqual(hit.status_code, status.HTTP_200_OK)

        with patch("django.core.cache.backends.locmem.time.time", return_value=1060):
            with self.assertNumQueries(2):
                expired = self.client.get(self.url)
        self.assertEqual(expired.status_code, status.HTTP_200_OK)
        self.assertEqual(expired.data, hit.data)

    def test_anonymous_and_invalid_jwt_requests_never_read_warm_cache(self):
        self.warm_metadata()
        self.client.force_authenticate(user=None)
        for token in (None, "invalid-token"):
            self.client.credentials(**({"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}))
            with patch.object(LocMemCache, "get", side_effect=AssertionError("Unauthorized cache access")) as get:
                response = self.client.get(self.url)
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
            get.assert_not_called()

    def test_changed_role_and_inactive_jwt_users_cannot_read_warm_cache(self):
        self.warm_metadata()
        reader = self.readers[User.Role.VIEWER]
        token = RefreshToken.for_user(reader).access_token
        self.client.force_authenticate(user=None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        reader.role = "unknown"
        reader.save(update_fields=["role"])
        with patch.object(LocMemCache, "get", side_effect=AssertionError("Unauthorized cache access")) as get:
            denied = self.client.get(self.url)
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        get.assert_not_called()

        reader.is_active = False
        reader.save(update_fields=["is_active"])
        with patch.object(LocMemCache, "get", side_effect=AssertionError("Unauthorized cache access")) as get:
            inactive = self.client.get(self.url)
        self.assertEqual(inactive.status_code, status.HTTP_401_UNAUTHORIZED)
        get.assert_not_called()

    def test_missing_row_is_not_served_from_a_surviving_cache_entry(self):
        key, old = self.warm_metadata()
        Document.objects.filter(pk=self.document.pk).delete()
        self.assertEqual(cache.get(key), old)

        with patch.object(LocMemCache, "get", side_effect=AssertionError("Missing row cache access")) as get:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        get.assert_not_called()

    def test_metadata_update_invalidates_only_after_commit(self):
        key, old = self.warm_metadata()
        self.client.force_authenticate(user=self.readers[User.Role.EDITOR])

        with self.captureOnCommitCallbacks(execute=True):
            updated = self.client.patch(self.url, {"title": "Changed title"}, format="json")
            self.assertEqual(updated.status_code, status.HTTP_200_OK)
            self.assertEqual(cache.get(key), old)
            current = self.client.get(self.url)
            self.assertEqual(current.data["title"], "Changed title")

        self.assertIsNone(cache.get(key))
        self.assertEqual(self.client.get(self.url).data["title"], "Changed title")

    def test_deletion_invalidates_after_commit_and_never_returns_cached_metadata(self):
        key, old = self.warm_metadata()
        self.client.force_authenticate(user=self.readers[User.Role.ADMIN])

        with self.captureOnCommitCallbacks(execute=True):
            deleted = self.client.delete(self.url)
            self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
            self.assertEqual(cache.get(key), old)
            self.assertEqual(self.client.get(self.url).status_code, status.HTTP_404_NOT_FOUND)

        self.assertIsNone(cache.get(key))

    def test_update_rollback_preserves_the_previous_cache_entry(self):
        key, old = self.warm_metadata()
        self.client.force_authenticate(user=self.readers[User.Role.EDITOR])
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaisesMessage(RuntimeError, "outer rollback"):
                with transaction.atomic():
                    changed = self.client.patch(self.url, {"title": "Rolled back"}, format="json")
                    self.assertEqual(changed.status_code, status.HTTP_200_OK)
                    self.assertEqual(self.client.get(self.url).data["title"], "Rolled back")
                    raise RuntimeError("outer rollback")

        self.assertEqual(cache.get(key), old)
        self.assertEqual(self.client.get(self.url).data, old)

    def test_same_timestamp_after_rollback_cannot_reuse_uncommitted_cached_metadata(self):
        self.warm_metadata()
        self.client.force_authenticate(user=self.readers[User.Role.EDITOR])
        same_time = datetime(2026, 10, 20, 12, tzinfo=UTC)
        with patch("django.utils.timezone.now", return_value=same_time):
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaisesMessage(RuntimeError, "discard this version"):
                    with transaction.atomic():
                        self.client.patch(self.url, {"title": "Uncommitted title"}, format="json")
                        self.assertEqual(self.client.get(self.url).data["title"], "Uncommitted title")
                        raise RuntimeError("discard this version")
                saved = self.client.patch(self.url, {"title": "Committed title"}, format="json")
            self.assertEqual(saved.status_code, status.HTTP_200_OK)
            self.assertEqual(self.client.get(self.url).data["title"], "Committed title")

    def test_delete_rollback_keeps_cached_metadata_available(self):
        key, old = self.warm_metadata()
        self.client.force_authenticate(user=self.readers[User.Role.ADMIN])
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaisesMessage(RuntimeError, "outer rollback"):
                with transaction.atomic():
                    self.assertEqual(self.client.delete(self.url).status_code, status.HTTP_204_NO_CONTENT)
                    raise RuntimeError("outer rollback")

        self.assertEqual(cache.get(key), old)
        self.assertEqual(self.client.get(self.url).data, old)
        self.assertTrue(self.storage.exists(self.document.object_key))

    def test_database_update_failure_does_not_invalidate_the_previous_entry(self):
        key, old = self.warm_metadata()
        self.client.force_authenticate(user=self.readers[User.Role.EDITOR])
        with (
            patch.object(Document, "save", side_effect=OperationalError("database unavailable")),
            self.assertLogs("apps.documents.views", level="ERROR"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.patch(self.url, {"title": "Not saved"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(cache.get(key), old)
        self.assertEqual(self.client.get(self.url).data, old)

    def test_cache_read_failures_fall_back_to_database_without_exposing_details(self):
        self.warm_metadata()
        private_message = "private-redis-password-and-endpoint"
        for error in (RedisConnectionError(private_message), RedisTimeoutError(private_message)):
            with self.subTest(error=type(error).__name__):
                with (
                    patch.object(LocMemCache, "get", side_effect=error),
                    self.assertLogs("apps.documents.caching", level="WARNING") as logs,
                    self.assertNumQueries(2),
                ):
                    response = self.client.get(self.url)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data["title"], "Cached original")
                self.assertNotIn(private_message, response.content.decode())
                self.assertNotIn(private_message, "\n".join(logs.output))

    def test_cache_write_failure_still_returns_database_metadata(self):
        private_message = "private-cache-write-credential"
        with (
            patch.object(LocMemCache, "set", side_effect=RedisTimeoutError(private_message)),
            self.assertLogs("apps.documents.caching", level="WARNING") as logs,
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "Cached original")
        self.assertNotIn(private_message, "\n".join(logs.output))

    def test_failed_update_invalidation_cannot_make_the_old_entry_current(self):
        key, old = self.warm_metadata()
        self.client.force_authenticate(user=self.readers[User.Role.EDITOR])
        with (
            patch.object(LocMemCache, "delete", side_effect=RedisConnectionError("private invalidation details")),
            self.assertLogs("apps.documents.caching", level="WARNING") as logs,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.patch(self.url, {"title": "Current title"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(cache.get(key), old)
        self.assertEqual(self.client.get(self.url).data["title"], "Current title")
        self.assertNotIn("private invalidation details", "\n".join(logs.output))

    def test_failed_delete_invalidation_preserves_success_and_storage_cleanup(self):
        key, old = self.warm_metadata()
        self.client.force_authenticate(user=self.readers[User.Role.ADMIN])
        with (
            patch.object(LocMemCache, "delete", side_effect=RedisConnectionError("private invalidation details")),
            self.assertLogs("apps.documents.caching", level="WARNING"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(cache.get(key), old)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(self.storage.exists(self.document.object_key))

    def test_image_replacement_refreshes_cached_file_metadata(self):
        key, _ = self.warm_metadata()
        self.client.force_authenticate(user=self.readers[User.Role.EDITOR])
        with self.captureOnCommitCallbacks(execute=True):
            updated = self.client.patch(self.url, {"file": image_upload("JPEG")}, format="multipart")

        self.assertEqual(updated.status_code, status.HTTP_200_OK)
        self.assertIsNone(cache.get(key))
        metadata = self.client.get(self.url)
        self.assertEqual(metadata.data["content_type"], "image/jpeg")
        self.assertEqual(metadata.data["original_filename"], "replacement.jpeg")
        self.assertEqual(metadata.data["file_size"], updated.data["file_size"])

    def test_updating_one_document_keeps_another_document_cached(self):
        self.warm_metadata()
        other = Document.objects.create(
            title="Other document", original_filename="other.png", object_key="documents/other.png",
            content_type="image/png", file_size=128, creator=self.owner,
        )
        other_url = f"/api/v1/documents/{other.pk}/"
        key, old = self.warm_metadata(other_url)
        self.client.force_authenticate(user=self.readers[User.Role.EDITOR])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(self.url, {"title": "Updated one"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(cache.get(key), old)
        with self.assertNumQueries(1):
            unchanged = self.client.get(other_url)
        self.assertEqual(unchanged.data, old)

    def test_download_urls_never_enter_or_use_the_metadata_cache(self):
        key, metadata = self.warm_metadata()
        signed_url = "http://download.example.test/file?signature=private"
        with (
            patch.object(self.storage, "url", return_value=signed_url),
            patch.object(LocMemCache, "get", side_effect=AssertionError("Download cache read")) as get,
            patch.object(LocMemCache, "set", side_effect=AssertionError("Download cache write")) as set_,
        ):
            response = self.client.get(f"{self.url}download/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["download_url"], signed_url)
        get.assert_not_called()
        set_.assert_not_called()
        self.assertEqual(cache.get(key), metadata)
        self.assertNotIn("download_url", metadata)

    def test_lists_do_not_read_or_write_individual_metadata_cache(self):
        with (
            patch.object(LocMemCache, "get", side_effect=AssertionError("List cache read")) as get,
            patch.object(LocMemCache, "set", side_effect=AssertionError("List cache write")) as set_,
        ):
            response = self.client.get("/api/v1/documents/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["id"], self.document.pk)
        get.assert_not_called()
        set_.assert_not_called()

    def test_deletion_between_version_probe_and_cache_fill_returns_not_found(self):
        admin_client = APIClient()
        admin_client.force_authenticate(user=self.readers[User.Role.ADMIN])

        def delete_before_miss(backend, key, default=None, version=None):
            with self.captureOnCommitCallbacks(execute=True):
                deletion = admin_client.delete(self.url)
            self.assertEqual(deletion.status_code, status.HTTP_204_NO_CONTENT)
            return default

        with patch.object(LocMemCache, "get", new=delete_before_miss):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(CACHES=TEST_CACHES)
class ConcurrentMetadataCacheTests(MetadataCacheFixture, TransactionTestCase):
    def read_during_mutation(self, mutate):
        paused = Event()
        resume = Event()
        original_set = LocMemCache.set

        def pause_cache_fill(backend, key, value, *args, **kwargs):
            if current_thread().name.startswith("metadata-reader"):
                paused.set()
                if not resume.wait(timeout=10):
                    raise TimeoutError("Mutation did not complete during cache fill")
            return original_set(backend, key, value, *args, **kwargs)

        def read():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(user=self.readers[User.Role.VIEWER])
                response = client.get(self.url)
                return response.status_code, response.data
            finally:
                close_old_connections()

        with (
            patch.object(LocMemCache, "set", new=pause_cache_fill),
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="metadata-reader") as executor,
        ):
            future = executor.submit(read)
            try:
                self.assertTrue(paused.wait(timeout=10))
                mutation = mutate()
            finally:
                resume.set()
            read_status, old_metadata = future.result(timeout=20)

        self.assertEqual(read_status, status.HTTP_200_OK)
        self.assertEqual(old_metadata["title"], "Cached original")
        return mutation

    @skipUnlessDBFeature("has_select_for_update")
    def test_delayed_reader_fill_after_committed_update_cannot_restore_old_metadata(self):
        self.client.force_authenticate(user=self.readers[User.Role.EDITOR])
        updated = self.read_during_mutation(
            lambda: self.client.patch(self.url, {"title": "Concurrent update"}, format="json"),
        )

        self.assertEqual(updated.status_code, status.HTTP_200_OK)
        current = self.client.get(self.url)
        self.assertEqual(current.status_code, status.HTTP_200_OK)
        self.assertEqual(current.data["title"], "Concurrent update")

    @skipUnlessDBFeature("has_select_for_update")
    def test_delayed_reader_fill_after_committed_delete_cannot_restore_the_document(self):
        self.client.force_authenticate(user=self.readers[User.Role.ADMIN])
        deleted = self.read_during_mutation(lambda: self.client.delete(self.url))

        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_404_NOT_FOUND)
