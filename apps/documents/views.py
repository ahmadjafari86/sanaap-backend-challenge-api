import logging

from django.conf import settings
from django.db import DatabaseError
from django.http import Http404
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from minio.error import MinioException
from rest_framework import generics, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from urllib3.exceptions import HTTPError

from .exceptions import (
    DocumentCreationUnavailable,
    DocumentDeletionUnavailable,
    DocumentDownloadUnavailable,
    DocumentUpdateUnavailable,
)
from .models import Document
from .pagination import DocumentPagination
from .permissions import DocumentRolePermission
from .serializers import (
    DocumentDownloadSerializer,
    DocumentListFilterSerializer,
    DocumentSerializer,
    DocumentUpdateSerializer,
    DocumentUploadSerializer,
)
from .services import (
    create_document,
    delete_document,
    get_document_download_url,
    update_document,
)

logger = logging.getLogger(__name__)


class DocumentListCreateView(generics.ListCreateAPIView):
    queryset = Document.objects.order_by("-created_at", "-id")
    serializer_class = DocumentSerializer
    pagination_class = DocumentPagination
    permission_classes = [DocumentRolePermission]
    parser_classes = [MultiPartParser, FormParser]
    http_method_names = ["get", "head", "post", "options"]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return DocumentUploadSerializer
        return super().get_serializer_class()

    def filter_queryset(self, queryset):
        filters = DocumentListFilterSerializer(data=self.request.query_params.dict())
        filters.is_valid(raise_exception=True)
        queryset = super().filter_queryset(queryset)
        search = filters.validated_data.get("search")
        if search:
            queryset = queryset.filter(title__icontains=search)
        creator = filters.validated_data.get("creator")
        if creator is not None:
            queryset = queryset.filter(creator_id=creator)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            document = create_document(
                title=serializer.validated_data["title"],
                uploaded_file=serializer.validated_data["file"],
                creator=request.user,
            )
        except (DatabaseError, OSError, MinioException, HTTPError) as error:
            logger.error("Document creation failed (%s).", type(error).__name__)
            raise DocumentCreationUnavailable() from error

        return Response(
            DocumentSerializer(document, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )


class DocumentDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer
    permission_classes = [DocumentRolePermission]
    http_method_names = ["get", "head", "put", "patch", "delete", "options"]

    def get_serializer_class(self):
        if self.request.method in ("PUT", "PATCH"):
            return DocumentUpdateSerializer
        return super().get_serializer_class()

    def update(self, request, *args, **kwargs):
        document = self.get_object()
        serializer = self.get_serializer(
            data=request.data,
            partial=kwargs.pop("partial", False),
        )
        serializer.is_valid(raise_exception=True)

        try:
            document = update_document(
                document_id=document.pk,
                title=serializer.validated_data.get("title"),
                uploaded_file=serializer.validated_data.get("file"),
            )
        except Document.DoesNotExist as error:
            raise Http404 from error
        except (DatabaseError, OSError, MinioException, HTTPError) as error:
            logger.error("Document update failed (%s).", type(error).__name__)
            raise DocumentUpdateUnavailable() from error

        return Response(
            DocumentSerializer(document, context=self.get_serializer_context()).data,
        )

    def destroy(self, request, *args, **kwargs):
        try:
            document = self.get_object()
            delete_document(document_id=document.pk)
        except Document.DoesNotExist as error:
            raise Http404 from error
        except DatabaseError as error:
            logger.error("Document deletion failed (%s).", type(error).__name__)
            raise DocumentDeletionUnavailable() from error

        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(never_cache, name="dispatch")
class DocumentDownloadView(generics.GenericAPIView):
    queryset = Document.objects.all()
    serializer_class = DocumentDownloadSerializer
    permission_classes = [DocumentRolePermission]
    http_method_names = ["get", "options"]

    def get(self, request, *args, **kwargs):
        try:
            document = self.get_object()
            download_url = get_document_download_url(document=document)
        except (DatabaseError, OSError, MinioException, HTTPError) as error:
            logger.error("Document download signing failed (%s).", type(error).__name__)
            raise DocumentDownloadUnavailable() from error

        return Response(
            self.get_serializer(
                {
                    "download_url": download_url,
                    "expires_in": settings.DOCUMENT_DOWNLOAD_URL_TTL_SECONDS,
                },
            ).data,
        )
