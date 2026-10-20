from rest_framework import serializers

from .models import Document

MAX_IMAGE_SIZE = 10 * 1024 * 1024
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG"}


class DocumentUploadSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    file = serializers.ImageField()

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
    file = serializers.ImageField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide a title or file to update.")
        return attrs


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
