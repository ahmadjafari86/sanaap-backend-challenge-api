from drf_spectacular.utils import OpenApiResponse, inline_serializer
from rest_framework import serializers

ERROR_DETAIL = inline_serializer(
    name="APIError",
    fields={
        "detail": serializers.CharField(),
        "code": serializers.CharField(required=False),
        "messages": serializers.ListField(child=serializers.DictField(), required=False),
    },
)

BAD_REQUEST = OpenApiResponse(
    response={
        "type": "object",
        "additionalProperties": {
            "oneOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
            ],
        },
    },
    description="Invalid input. Field errors are lists of messages; parsing errors use detail.",
)
UNAUTHORIZED = OpenApiResponse(
    response=ERROR_DETAIL,
    description="Missing, invalid, expired, or inactive-user credentials/token.",
)
FORBIDDEN = OpenApiResponse(
    response=ERROR_DETAIL,
    description="The authenticated application's role does not allow this operation.",
)
NOT_FOUND = OpenApiResponse(response=ERROR_DETAIL, description="The requested record or page does not exist.")
UNAVAILABLE = OpenApiResponse(
    response=ERROR_DETAIL,
    description="The operation is temporarily unavailable because of a database or storage error.",
)
AUTH_ERRORS = {401: UNAUTHORIZED, 403: FORBIDDEN}
