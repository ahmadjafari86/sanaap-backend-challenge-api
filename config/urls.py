from django.contrib import admin
from django.urls import include, path

from apps.accounts.views import UserListCreateView, UserUpdateView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path(
        "api/v1/users/",
        UserListCreateView.as_view(),
        name="user-list",
    ),
    path(
        "api/v1/users/<int:pk>/",
        UserUpdateView.as_view(),
        name="user-update",
    ),
]