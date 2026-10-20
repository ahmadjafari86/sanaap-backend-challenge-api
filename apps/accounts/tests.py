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