from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

from ..models import Document


User = get_user_model()


class DocumentAdminTests(TestCase):
    list_url = "/admin/documents/document/"

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(username="site-admin", password=None)
        cls.staff = User.objects.create_user(username="document-staff", is_staff=True)
        cls.document = Document.objects.create(
            title="Quarterly report", creator=cls.admin, original_filename="report.png",
            content_type="image/png", file_size=123, object_key="documents/private-object.png",
            staging_key="private-staging-key",
        )
        cls.other = Document.objects.create(
            title="Other image", creator=cls.admin, original_filename="other.jpg",
            content_type="image/jpeg", file_size=456, object_key="documents/other.jpg",
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_admin_index_lists_the_documents_application(self):
        self.assertContains(self.client.get("/admin/"), self.list_url)

    def test_changelist_can_search_and_filter_document_metadata(self):
        response = self.client.get(self.list_url, {"q": "Quarterly", "status__exact": "READY"})
        self.assertContains(response, "Quarterly report")
        self.assertNotContains(response, "Other image")
        self.assertContains(response, "site-admin")

    def test_detail_shows_metadata_without_exposing_internal_storage_identifiers(self):
        response = self.client.get(f"{self.list_url}{self.document.pk}/change/")
        self.assertContains(response, "Quarterly report")
        self.assertContains(response, "report.png")
        self.assertNotContains(response, "private-object.png")
        self.assertNotContains(response, "private-staging-key")
        self.assertNotContains(response, 'name="_save"')
        self.assertNotContains(response, "deletelink")

    def test_even_superusers_cannot_mutate_documents_through_the_admin(self):
        for path in ("add/", f"{self.document.pk}/change/", f"{self.document.pk}/delete/"):
            with self.subTest(path=path):
                response = self.client.post(self.list_url + path, {"title": "Changed", "post": "yes"})
                self.assertEqual(response.status_code, 403)
        self.client.post(self.list_url, {
            "action": "delete_selected", "_selected_action": [self.document.pk], "post": "yes",
        })
        self.document.refresh_from_db()
        self.assertEqual(self.document.title, "Quarterly report")
        self.assertEqual(Document.objects.count(), 2)

    def test_staff_without_model_permission_cannot_browse_documents(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.list_url).status_code, 403)

    def test_staff_with_view_permission_can_browse_documents(self):
        self.staff.user_permissions.add(Permission.objects.get(
            content_type__app_label="documents", codename="view_document",
        ))
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(self.list_url), "Quarterly report")
        self.assertContains(self.client.get(f"{self.list_url}{self.document.pk}/change/"), "report.png")
