from django.urls import path
from .views import LoginView, LogoutView, RefreshView

app_name = "accounts"

urlpatterns = [
    path(
        "token/",
        LoginView.as_view(),
        name="token-obtain",
    ),
    path(
        "token/refresh/",
        RefreshView.as_view(),
        name="token-refresh",
    ),
    path(
        "logout/",
        LogoutView.as_view(),
        name="logout",
    ),
]
