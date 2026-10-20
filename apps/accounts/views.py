from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.views import TokenBlacklistView, TokenObtainPairView, TokenRefreshView

from config.schema import AUTH_ERRORS, BAD_REQUEST, NOT_FOUND, UNAUTHORIZED

from .models import User
from .permissions import IsApplicationAdmin
from .serializers import UserSerializer


@extend_schema_view(
    get=extend_schema(
        tags=["Users"], summary="List users as an application admin",
        description="Only the admin application role may list users. Staff/superuser flags do not grant API access.",
        responses={200: UserSerializer(many=True), **AUTH_ERRORS},
    ),
    post=extend_schema(
        tags=["Users"], summary="Create a user as an application admin",
        description=(
            "Only admins may create users. Passwords use Django validation and hashing; new users default "
            "to viewer when role is omitted. Application roles are separate from staff/superuser flags."
        ),
        responses={201: UserSerializer, 400: BAD_REQUEST, **AUTH_ERRORS},
    ),
)
class UserListCreateView(generics.ListCreateAPIView):
    queryset = User.objects.order_by("id")
    serializer_class = UserSerializer
    authentication_classes = [JWTAuthentication]  # noqa: RUF012
    permission_classes = [IsApplicationAdmin]  # noqa: RUF012


@extend_schema_view(patch=extend_schema(
    tags=["Users"], summary="Update a user or role as an application admin",
    description="Only admins may change user details, passwords, or application roles. Password is optional on PATCH.",
    responses={200: UserSerializer, 400: BAD_REQUEST, 404: NOT_FOUND, **AUTH_ERRORS},
))
class UserUpdateView(generics.UpdateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    authentication_classes = [JWTAuthentication]  # noqa: RUF012
    permission_classes = [IsApplicationAdmin]  # noqa: RUF012
    http_method_names = ["patch", "options"]  # noqa: RUF012


@extend_schema(
    tags=["Authentication"], summary="Obtain an access and refresh token",
    description="Public login with username/password. Access tokens last 5 minutes; refresh tokens last 1 day.",
    responses={200: TokenObtainPairSerializer, 400: BAD_REQUEST, 401: UNAUTHORIZED},
)
class LoginView(TokenObtainPairView):
    pass


@extend_schema(
    tags=["Authentication"], summary="Refresh an access token",
    description="Submit a valid refresh token. Returns a new access token; refresh tokens are not rotated.",
    responses={200: TokenRefreshSerializer, 400: BAD_REQUEST, 401: UNAUTHORIZED},
)
class RefreshView(TokenRefreshView):
    pass


@extend_schema(
    tags=["Authentication"], summary="Blacklist a refresh token",
    description=(
        "Submit the refresh token to revoke it. Returns an empty JSON object. Existing access tokens "
        "remain valid until expiry; previously issued download URLs are not revoked."
    ),
    responses={
        200: OpenApiResponse(response={"type": "object", "additionalProperties": False}, description="Refresh token blacklisted."),
        400: BAD_REQUEST,
        401: UNAUTHORIZED,
    },
)
class LogoutView(TokenBlacklistView):
    pass
