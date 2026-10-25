from uuid import UUID

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone

from ..models import OutboxMessage, OutboxReceipt


class OutboxAdminTests(TestCase):
    messages_url = "/admin/outbox/outboxmessage/"
    receipts_url = "/admin/outbox/outboxreceipt/"

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_superuser(username="outbox-admin", password=None)
        cls.staff = User.objects.create_user(username="outbox-staff", is_staff=True)
        cls.message = OutboxMessage.objects.create(
            id=UUID("11111111-1111-4111-8111-111111111111"),
            kind="document.upload_requested", document_id=42, payload={"attempt_number": 1},
            dedupe_key="upload:42:1", status="DEAD", attempts=3, last_error_code="io_error",
            lease_token=UUID("33333333-3333-4333-8333-333333333333"), locked_until=timezone.now(),
        )
        cls.other = OutboxMessage.objects.create(
            id=UUID("22222222-2222-4222-8222-222222222222"),
            kind="storage.object_delete_requested", document_id=142,
            payload={"object_key": "<script>alert(1)</script>"}, dedupe_key="cleanup:other", status="PENDING",
        )
        cls.receipt = OutboxReceipt.objects.create(event_id=cls.message.pk)

    def setUp(self):
        self.client.force_login(self.admin)

    def message_url(self):
        return f"{self.messages_url}{self.message.pk}/change/"

    def receipt_url(self):
        return f"{self.receipts_url}{self.receipt.pk}/change/"

    def test_index_lists_messages_and_receipts(self):
        response = self.client.get("/admin/")
        self.assertContains(response, self.messages_url)
        self.assertContains(response, self.receipts_url)

    def test_messages_can_filter_delivery_state_kind_and_error(self):
        response = self.client.get(self.messages_url, {
            "status__exact": "DEAD", "kind": "document.upload_requested", "last_error_code": "io_error",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["cl"].result_list), [self.message])
        self.assertContains(response, "io_error")

    def test_message_search_accepts_event_document_dedupe_kind_and_error_queries(self):
        for query in (str(self.message.pk), "42", "upload:42:1", "document.upload_requested", "io_error"):
            with self.subTest(query=query):
                response = self.client.get(self.messages_url, {"q": query})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(list(response.context["cl"].result_list), [self.message])
        response = self.client.get(self.messages_url, {"q": "not-a-uuid-or-number"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["cl"].result_list), [])

    def test_message_detail_shows_diagnostics_and_receipt_without_edit_controls(self):
        response = self.client.get(self.message_url())
        self.assertContains(response, "upload:42:1")
        self.assertContains(response, "io_error")
        self.assertContains(response, str(self.message.lease_token))
        self.assertContains(response, "attempt_number")
        self.assertContains(response, self.receipt_url())
        self.assertNotContains(response, 'name="_save"')
        self.assertNotContains(response, "deletelink")
        self.assertNotContains(response, 'name="status"')

    def test_payload_html_is_escaped_in_detail_and_hidden_from_list(self):
        response = self.client.get(f"{self.messages_url}{self.other.pk}/change/")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertNotContains(self.client.get(self.messages_url), "alert(1)")

    def test_receipts_can_search_and_link_back_to_the_message(self):
        response = self.client.get(self.receipts_url, {"q": str(self.receipt.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["cl"].result_list), [self.receipt])
        response = self.client.get(self.receipt_url())
        self.assertContains(response, self.message_url())
        self.assertNotContains(response, 'name="_save"')
        self.assertNotContains(response, "deletelink")

    def test_receipt_remains_readable_when_message_is_no_longer_retained(self):
        message_url = self.message_url()
        self.message.delete()
        response = self.client.get(self.receipt_url())
        self.assertContains(response, str(self.receipt.pk))
        self.assertNotContains(response, f'href="{message_url}"')

    def test_even_superusers_cannot_mutate_outbox_records(self):
        for url, record in ((self.messages_url, self.message), (self.receipts_url, self.receipt)):
            for path in ("add/", f"{record.pk}/change/", f"{record.pk}/delete/"):
                with self.subTest(url=url, path=path):
                    response = self.client.post(url + path, {"status": "PENDING", "post": "yes"})
                    self.assertEqual(response.status_code, 403)
            self.client.post(url, {"action": "delete_selected", "_selected_action": [record.pk], "post": "yes"})
        self.message.refresh_from_db()
        self.assertEqual((self.message.status, self.message.attempts), ("DEAD", 3))
        self.assertEqual(OutboxMessage.objects.count(), 2)
        self.assertEqual(OutboxReceipt.objects.count(), 1)

    def test_staff_without_model_permissions_cannot_browse_outbox(self):
        self.client.force_login(self.staff)
        for url in (self.messages_url, self.message_url(), self.receipts_url, self.receipt_url()):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_staff_view_permissions_are_scoped_to_each_outbox_model(self):
        for model, allowed, forbidden in (
            ("outboxmessage", (self.messages_url, self.message_url()), self.receipts_url),
            ("outboxreceipt", (self.receipts_url, self.receipt_url()), self.messages_url),
        ):
            with self.subTest(model=model):
                self.staff.user_permissions.set([Permission.objects.get(
                    content_type__app_label="outbox", codename=f"view_{model}",
                )])
                self.client.force_login(self.staff)
                for url in allowed:
                    self.assertEqual(self.client.get(url).status_code, 200)
                self.assertEqual(self.client.get(forbidden).status_code, 403)
