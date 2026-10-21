from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.accounts.views import UserListCreateView, UserUpdateView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/schema/", admin.site.admin_view(SpectacularAPIView.as_view()), name="schema"),
    path(
        "api/docs/",
        admin.site.admin_view(SpectacularSwaggerView.as_view(url_name="schema")),
        name="swagger-ui",
    ),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/documents/", include("apps.documents.urls")),
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
