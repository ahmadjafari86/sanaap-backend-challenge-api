import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APITestCase


User = get_user_model()


class SuperuserCreationTests(APITestCase):
    def test_createsuperuser_creates_a_django_and_application_admin(self):
        password = "Bootstrap-admin-test-password-92!"
        with patch.dict(os.environ, {"DJANGO_SUPERUSER_PASSWORD": password}):
            call_command(
                "createsuperuser", username="bootstrap-admin", email="admin@example.test",
                interactive=False, stdout=StringIO(),
            )
        user = User.objects.get(username="bootstrap-admin")
        self.assertEqual(user.role, User.Role.ADMIN)
        self.assertTrue(user.is_staff and user.is_superuser and user.is_active)
        self.assertTrue(user.check_password(password))
        self.assertTrue(self.client.login(username=user.username, password=password))
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        login = self.client.post("/api/v1/auth/token/", {
            "username": user.username, "password": password,
        })
        self.assertEqual(login.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        self.assertEqual(self.client.get("/api/v1/users/").status_code, 200)

    def test_an_explicit_superuser_application_role_is_preserved(self):
        user = User.objects.create_superuser(username="deliberate-viewer", role=User.Role.VIEWER)
        self.assertEqual(user.role, User.Role.VIEWER)
        self.assertEqual(User.objects.create_user(username="ordinary-user").role, User.Role.VIEWER)
