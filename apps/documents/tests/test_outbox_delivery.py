from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from django.db import OperationalError, close_old_connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from ..models import OutboxMessage
from ..outbox import enqueue_message


class OutboxDeliveryTests(TransactionTestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("apps.documents.outbox_delivery"), "Committed obligations need a relay")
        from .. import outbox_delivery
        self.delivery = outbox_delivery
        self.now = timezone.now()

    def event(self, **changes):
        arguments = dict(kind="document.upload_requested", document_id=42,
                         payload={"attempt_number": 1}, dedupe_key="upload:42:1", available_at=self.now)
        with transaction.atomic():
            return enqueue_message(**{**arguments, **changes})

    def test_other_connection_cannot_claim_an_uncommitted_message(self):
        with transaction.atomic():
            self.event()
            def claim():
                close_old_connections()
                try:
                    return self.delivery.claim_batch(now=self.now, limit=1)
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(claim).result(timeout=5), [])
        self.assertEqual(len(self.delivery.claim_batch(now=self.now, limit=1)), 1)

    def test_concurrent_claims_do_not_share_a_live_lease(self):
        self.event()
        self.event(dedupe_key="upload:43:1", document_id=43)
        barrier = Barrier(2)
        def claim():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return self.delivery.claim_batch(now=self.now, limit=1)[0].event_id
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim) for _ in range(2)]
            identifiers = [future.result(timeout=5) for future in futures]
        self.assertEqual(len(set(identifiers)), 2)

    def test_future_work_is_not_published_early(self):
        self.event(available_at=self.now + timedelta(seconds=60))
        self.assertEqual(self.delivery.claim_batch(now=self.now, limit=10), [])

    def test_expired_lease_is_reclaimed_and_old_token_cannot_finalize(self):
        event = self.event()
        old = self.delivery.claim_batch(now=self.now, limit=1)[0]
        newer = self.delivery.claim_batch(now=self.now + timedelta(seconds=61), limit=1)[0]
        self.assertNotEqual(old.lease_token, newer.lease_token)
        self.assertFalse(self.delivery._finish(old, now=self.now + timedelta(seconds=62)))
        event.refresh_from_db()
        self.assertEqual((event.status, event.lease_token, event.attempts), ("PROCESSING", newer.lease_token, 2))

    def test_delivery_does_not_hold_a_database_lock_during_network_io(self):
        event = self.event()
        def publish(*args, **kwargs):
            def acquire():
                close_old_connections()
                try:
                    with transaction.atomic():
                        return OutboxMessage.objects.select_for_update(nowait=True).get(pk=event.pk).pk
                finally:
                    close_old_connections()
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(acquire).result(timeout=5), event.pk)
        with patch("apps.documents.tasks.store_document.apply_async", side_effect=publish):
            self.assertEqual(self.delivery.relay_once(now=self.now), 1)
        event.refresh_from_db()
        self.assertEqual(event.status, "PUBLISHED")

    def test_broker_failure_retains_obligation_with_sanitized_backoff(self):
        event = self.event()
        with patch("apps.documents.tasks.store_document.apply_async", side_effect=OSError("private-token-url")):
            self.delivery.relay_once(now=self.now)
        event.refresh_from_db()
        self.assertEqual((event.status, event.attempts, event.last_error_code), ("PENDING", 1, "io_error"))
        self.assertGreaterEqual(event.available_at, self.now + timedelta(seconds=2))
        self.assertLessEqual(event.available_at, self.now + timedelta(seconds=2.4))
        self.assertIsNone(event.lease_token)

    def test_publication_retry_is_capped_and_twentieth_failure_becomes_dead(self):
        event = self.event()
        OutboxMessage.objects.filter(pk=event.pk).update(attempts=18)
        with patch("apps.documents.tasks.store_document.apply_async", side_effect=OSError("offline")):
            self.delivery.relay_once(now=self.now)
            event.refresh_from_db()
            self.assertGreaterEqual(event.available_at, self.now + timedelta(seconds=300))
            self.assertLessEqual(event.available_at, self.now + timedelta(seconds=360))
            self.delivery.relay_once(now=self.now + timedelta(seconds=361))
        event.refresh_from_db()
        self.assertEqual((event.status, event.attempts), ("DEAD", 20))

    def test_expired_twentieth_lease_does_not_publish_a_twenty_first_time(self):
        event = self.event()
        OutboxMessage.objects.filter(pk=event.pk).update(attempts=19)
        self.delivery.claim_batch(now=self.now, limit=1)
        self.assertEqual(self.delivery.claim_batch(now=self.now + timedelta(seconds=61), limit=1), [])
        event.refresh_from_db()
        self.assertEqual((event.status, event.attempts), ("DEAD", 20))

    def test_invalid_payload_is_dead_without_network_or_private_error(self):
        event = self.event()
        OutboxMessage.objects.filter(pk=event.pk).update(payload={"token": "private"})
        with patch("apps.documents.tasks.store_document.apply_async", side_effect=AssertionError("must not publish")):
            self.delivery.relay_once(now=self.now)
        event.refresh_from_db()
        self.assertEqual((event.status, event.last_error_code), ("DEAD", "invalid_payload"))

    def test_send_then_database_failure_is_recovered_by_lease_and_can_duplicate(self):
        event = self.event()
        sent = []
        def publish(*, kwargs, task_id):
            sent.append((kwargs, task_id))
        with patch("apps.documents.tasks.store_document.apply_async", side_effect=publish):
            with patch.object(self.delivery, "_finish", side_effect=OperationalError("private")):
                with self.assertRaises(OperationalError):
                    self.delivery.relay_once(now=self.now)
            event.refresh_from_db()
            self.assertEqual(event.status, "PROCESSING")
            self.delivery.relay_once(now=self.now + timedelta(seconds=61))
        self.assertEqual(sent, [({"document_id": 42, "event_id": str(event.pk)}, str(event.pk))] * 2)
        event.refresh_from_db()
        self.assertEqual(event.status, "PUBLISHED")

    def test_staging_cleanup_is_real_and_missing_file_replay_succeeds(self):
        with TemporaryDirectory() as directory, override_settings(DOCUMENT_STAGING_ROOT=directory):
            key = "a" * 32 + ".stage"
            path = Path(directory) / key
            path.write_bytes(b"private image")
            event = self.event(kind="storage.staging_delete_requested", payload={"staging_key": key})
            self.delivery.relay_once(now=self.now)
            self.assertFalse(path.exists())
            OutboxMessage.objects.filter(pk=event.pk).update(status="PENDING")
            self.delivery.relay_once(now=self.now)
            event.refresh_from_db()
            self.assertEqual(event.status, "PUBLISHED")
