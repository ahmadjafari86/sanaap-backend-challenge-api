from rest_framework import serializers

from .models import Document

MAX_IMAGE_SIZE = 10 * 1024 * 1024
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG"}


class DocumentListFilterSerializer(serializers.Serializer):
    search = serializers.CharField(max_length=255, required=False, allow_blank=True)
    creator = serializers.IntegerField(min_value=1, max_value=2**63 - 1, required=False)


class DocumentUploadSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    file = serializers.ImageField(help_text="Real JPEG or PNG image, at most 10 MiB (10,485,760 bytes).")

    def validate_file(self, uploaded_file):
        if uploaded_file.size > MAX_IMAGE_SIZE:
            raise serializers.ValidationError(
                "Image size must not exceed 10 MiB.",
            )

        if uploaded_file.image.format not in ALLOWED_IMAGE_FORMATS:
            raise serializers.ValidationError(
                "Only JPEG and PNG images are allowed.",
            )

        uploaded_file.seek(0)
        return uploaded_file


class DocumentUpdateSerializer(DocumentUploadSerializer):
    file = serializers.ImageField(
        required=False,
        help_text="Optional replacement JPEG or PNG image, at most 10 MiB (10,485,760 bytes).",
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide a title or file to update.")
        return attrs


class DocumentDownloadSerializer(serializers.Serializer):
    download_url = serializers.URLField(read_only=True, help_text="Private temporary bearer URL; do not cache.")
    expires_in = serializers.IntegerField(read_only=True, min_value=60, max_value=60)


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = (
            "id",
            "title",
            "original_filename",
            "content_type",
            "file_size",
            "creator",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
