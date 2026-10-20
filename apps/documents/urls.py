from django.urls import path

from .views import DocumentCreateView

app_name = "documents"

urlpatterns = [
    path("", DocumentCreateView.as_view(), name="document-list"),
]
