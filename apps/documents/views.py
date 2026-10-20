import logging

from django.db import DatabaseError
from minio.error import MinioException
from rest_framework import generics, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from urllib3.exceptions import HTTPError

from .exceptions import DocumentCreationUnavailable
from .permissions import DocumentRolePermission
from .serializers import DocumentSerializer, DocumentUploadSerializer
from .services import create_document

logger = logging.getLogger(__name__)


class DocumentCreateView(generics.GenericAPIView):
    serializer_class = DocumentUploadSerializer
    permission_classes = [DocumentRolePermission]
    parser_classes = [MultiPartParser, FormParser]
    http_method_names = ["post", "options"]

    def post(self, request, *args, **kwargs):
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
