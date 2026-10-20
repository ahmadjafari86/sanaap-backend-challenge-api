from rest_framework import generics
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import User
from .permissions import IsApplicationAdmin
from .serializers import UserSerializer


class UserListCreateView(generics.ListCreateAPIView):
    queryset = User.objects.order_by("id")
    serializer_class = UserSerializer
    authentication_classes = [JWTAuthentication]  # noqa: RUF012
    permission_classes = [IsApplicationAdmin]  # noqa: RUF012


class UserUpdateView(generics.UpdateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    authentication_classes = [JWTAuthentication]  # noqa: RUF012
    permission_classes = [IsApplicationAdmin]  # noqa: RUF012
    http_method_names = ["patch", "options"]  # noqa: RUF012
