from rest_framework import status
from rest_framework.exceptions import APIException


class DocumentNotReady(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Document storage is not ready."
    default_code = "document_not_ready"


class DocumentCreationUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Document creation is temporarily unavailable."
    default_code = "document_creation_unavailable"


class DocumentUpdateUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Document update is temporarily unavailable."
    default_code = "document_update_unavailable"


class DocumentDeletionUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Document deletion is temporarily unavailable."
    default_code = "document_deletion_unavailable"


class DocumentDownloadUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Document download is temporarily unavailable."
    default_code = "document_download_unavailable"
