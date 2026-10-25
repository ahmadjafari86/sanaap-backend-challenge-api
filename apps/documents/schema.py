from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view, inline_serializer
from rest_framework import serializers

from config.schema import AUTH_ERRORS, BAD_REQUEST, ERROR_DETAIL, NOT_FOUND, UNAVAILABLE

from .serializers import (
    DocumentDownloadSerializer,
    DocumentListFilterSerializer,
    DocumentSerializer,
    DocumentUpdateSerializer,
    DocumentUploadSerializer,
)

title_update = inline_serializer(
    name="DocumentTitleUpdate",
    fields={"title": serializers.CharField(max_length=255)},
)
update_requests = {
    "application/json": title_update,
    "application/x-www-form-urlencoded": title_update,
    "multipart/form-data": DocumentUpdateSerializer,
}
update_responses = {
    200: DocumentSerializer,
    400: BAD_REQUEST,
    404: NOT_FOUND,
    409: OpenApiResponse(response=ERROR_DETAIL, description="Document storage is PENDING or FAILED; only READY documents can be updated."),
    503: UNAVAILABLE,
    **AUTH_ERRORS,
}

list_schema = extend_schema_view(
    get=extend_schema(
        tags=["Documents"],
        summary="List shared document metadata",
        description=(
            "Admins, editors, and viewers can read every document, regardless of creator. "
            "Search matches a trimmed, case-insensitive literal substring of title only. "
            "Search and creator filters combine with AND before pagination. "
            "Order is descending creation time, then descending id. "
            "Invalid query values return 400; a valid page beyond the results returns 404."
        ),
        parameters=[DocumentListFilterSerializer],
        responses={200: DocumentSerializer(many=True), 400: BAD_REQUEST, 404: NOT_FOUND, **AUTH_ERRORS},
    ),
    post=extend_schema(
        tags=["Documents"],
        summary="Accept a JPEG/MPO or PNG document for background storage",
        description=(
            "Admins and editors upload multipart title/file data. The file must contain a real "
            "JPEG (including multi-picture MPO) or PNG image no larger than 10 MiB (10,485,760 bytes). "
            "All MPO pictures must decode; the original bytes are retained with content type image/mpo. "
            "The authenticated user becomes the creator; storage keys and file metadata are server-owned. "
            "Returns 202 with PENDING after private staging and database acceptance. Poll detail for "
            "READY or FAILED. The Celery worker stores bytes in MinIO; dispatch or exhausted storage "
            "failures can produce FAILED after acceptance. Existing records are READY."
        ),
        request={"multipart/form-data": DocumentUploadSerializer},
        responses={202: DocumentSerializer, 400: BAD_REQUEST, 503: UNAVAILABLE, **AUTH_ERRORS},
    ),
)

detail_schema = extend_schema_view(
    get=extend_schema(
        tags=["Documents"],
        summary="Read shared document metadata",
        description=(
            "All three roles can read any document. Metadata is cached for 60 seconds after "
            "access checks and a database existence/version probe. PostgreSQL remains required; "
            "Redis errors fall back to a full database read. No storage key or download URL is returned."
        ),
        responses={200: DocumentSerializer, 404: NOT_FOUND, **AUTH_ERRORS},
    ),
    put=extend_schema(
        tags=["Documents"],
        summary="Update a title and optionally replace its image",
        description=(
            "Admins and editors may update any READY document; other states return 409. A title is required; the image is optional. "
            "Use JSON/form data for title-only updates and multipart for a replacement JPEG/MPO or PNG up to 10 MiB. "
            "Original creator/creation time are preserved. Replacement uploads use a new key; "
            "previous-file cleanup runs after commit. Cleanup failure cannot undo a committed update."
        ),
        request=update_requests,
        responses=update_responses,
    ),
    patch=extend_schema(
        tags=["Documents"],
        summary="Partially update document metadata or image",
        description=(
            "Admins and editors may update any READY document; other states return 409. Provide at least a title or file; empty updates "
            "return 400. JSON/form requests update only the title; multipart may also replace the image "
            "with a validated JPEG/MPO or PNG up to 10 MiB. Server-owned metadata cannot be overridden. "
            "Replacement and cache cleanup run after database commit."
        ),
        request=update_requests,
        responses=update_responses,
    ),
    delete=extend_schema(
        tags=["Documents"],
        summary="Delete a document as an application admin",
        description=(
            "Only admins may delete any document. Deletes metadata in a transaction, then invalidates "
            "its cache entry and attempts current-object cleanup after commit. If storage cleanup fails, "
            "the committed deletion still returns 204 and leaves a logged orphan. "
            "Missing or already-deleted documents return 404."
        ),
        request=None,
        responses={204: OpenApiResponse(description="Deleted; no response body."), 404: NOT_FOUND, 503: UNAVAILABLE, **AUTH_ERRORS},
    ),
)

download_schema = extend_schema(
    tags=["Documents"],
    summary="Issue a private download URL valid for 60 seconds",
    description=(
        "All three authenticated roles may request a URL for any document. Permissions and a database "
        "lookup precede signing. PENDING/FAILED documents return 409 without signing. URLs use the external MinIO endpoint and act as bearer credentials "
        "until expiry or object removal. Logout/role changes do not revoke issued URLs. "
        "URLs and HTTP responses are never cached. Only GET and OPTIONS are supported; HEAD returns 405. "
        "Local signing does not verify object existence, storage availability, or a completed transfer."
    ),
    responses={200: DocumentDownloadSerializer, 404: NOT_FOUND, 409: OpenApiResponse(response=ERROR_DETAIL, description="Document storage is not READY."), 503: UNAVAILABLE, **AUTH_ERRORS},
)
