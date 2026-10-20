from datetime import timedelta

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


class AuthenticationTests(APITestCase):
    token_url = "/api/v1/auth/token/"
    refresh_url = "/api/v1/auth/token/refresh/"
    logout_url = "/api/v1/auth/logout/"

    @classmethod
    def setUpTestData(cls):
        cls.password = "Assessment-test-password-92!"
        cls.user = User.objects.create_user(
            username="test-viewer",
            password=cls.password,
            role=User.Role.VIEWER,
        )

    def obtain_tokens(self):
        response = self.client.post(
            self.token_url,
            {
                "username": self.user.username,
                "password": self.password,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def test_valid_credentials_return_token_pair(self):
        tokens = self.obtain_tokens()

        self.assertTrue(tokens["access"])
        self.assertTrue(tokens["refresh"])

    def test_wrong_password_is_rejected(self):
        response = self.client.post(
            self.token_url,
            {
                "username": self.user.username,
                "password": "wrong-password",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.client.post(
            self.token_url,
            {
                "username": self.user.username,
                "password": self.password,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_token_returns_access_token(self):
        tokens = self.obtain_tokens()

        response = self.client.post(
            self.refresh_url,
            {"refresh": tokens["refresh"]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["access"])

    def test_logout_revokes_refresh_but_preserves_access_token(self):
        tokens = self.obtain_tokens()
        authorization = f"Bearer {tokens['access']}"
        self.client.credentials(HTTP_AUTHORIZATION=authorization)

        response = self.client.post(
            self.logout_url,
            {"refresh": tokens["refresh"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.post(
            self.refresh_url,
            {"refresh": tokens["refresh"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        request = APIRequestFactory().get(
            "/",
            HTTP_AUTHORIZATION=authorization,
        )
        user, _ = JWTAuthentication().authenticate(request)
        self.assertEqual(user.pk, self.user.pk)

    def test_login_requires_username_and_password(self):
        for payload in (
            {},
            {"username": self.user.username},
            {"password": self.password},
        ):
            with self.subTest(fields=list(payload)):
                response = self.client.post(
                    self.token_url,
                    payload,
                    format="json",
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )

    def test_refresh_and_logout_require_refresh_token(self):
        for url in (self.refresh_url, self.logout_url):
            with self.subTest(url=url):
                response = self.client.post(url, {}, format="json")
                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )

    def test_refresh_and_logout_reject_invalid_token(self):
        for url in (self.refresh_url, self.logout_url):
            with self.subTest(url=url):
                response = self.client.post(
                    url,
                    {"refresh": "not-a-valid-token"},
                    format="json",
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_401_UNAUTHORIZED,
                )

    def test_refresh_and_logout_reject_expired_token(self):
        token = RefreshToken.for_user(self.user)
        token.set_exp(lifetime=timedelta(seconds=-1))

        for url in (self.refresh_url, self.logout_url):
            with self.subTest(url=url):
                response = self.client.post(
                    url,
                    {"refresh": str(token)},
                    format="json",
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_401_UNAUTHORIZED,
                )


class UserManagementTests(APITestCase):
    users_url = "/api/v1/users/"

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="application-admin",
            role=User.Role.ADMIN,
        )
        cls.editor = User.objects.create_user(
            username="editor",
            role=User.Role.EDITOR,
        )
        cls.viewer = User.objects.create_user(
            username="viewer",
            role=User.Role.VIEWER,
        )

    def test_anonymous_user_cannot_list_users(self):
        response = self.client.get(self.users_url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_editor_and_viewer_cannot_manage_users(self):
        for user in (self.editor, self.viewer):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)

                response = self.client.get(self.users_url)
                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                )

                response = self.client.post(
                    self.users_url,
                    {
                        "username": f"created-by-{user.role}",
                        "password": "Assessment-new-password-93!",
                        "role": User.Role.ADMIN,
                    },
                    format="json",
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                )

        self.assertFalse(
            User.objects.filter(
                username__in=["created-by-editor", "created-by-viewer"],
            ).exists()
        )

    def test_django_superuser_without_admin_role_is_denied(self):
        self.viewer.is_staff = True
        self.viewer.is_superuser = True
        self.viewer.save(update_fields=["is_staff", "is_superuser"])
        self.client.force_authenticate(user=self.viewer)

        response = self.client.get(self.users_url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_application_admin_can_create_user_with_hashed_password(self):
        self.client.force_authenticate(user=self.admin)
        password = "Assessment-new-password-93!"

        response = self.client.post(
            self.users_url,
            {
                "username": "new-editor",
                "email": "editor@example.com",
                "password": password,
                "role": User.Role.EDITOR,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("password", response.data)

        user = User.objects.get(username="new-editor")
        self.assertTrue(user.check_password(password))
        self.assertEqual(user.role, User.Role.EDITOR)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_application_admin_can_change_user_role(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            f"{self.users_url}{self.viewer.pk}/",
            {"role": User.Role.EDITOR},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.viewer.refresh_from_db()
        self.assertEqual(self.viewer.role, User.Role.EDITOR)
