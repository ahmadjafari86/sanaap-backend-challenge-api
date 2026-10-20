from rest_framework import status
from rest_framework.exceptions import APIException


class DocumentCreationUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Document creation is temporarily unavailable."
    default_code = "document_creation_unavailable"
