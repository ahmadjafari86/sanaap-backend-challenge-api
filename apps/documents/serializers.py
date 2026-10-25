import struct

from PIL import Image
from rest_framework import serializers

from .models import Document

MAX_IMAGE_SIZE = 10 * 1024 * 1024
ALLOWED_IMAGE_FORMATS = {"JPEG", "MPO", "PNG"}


class DocumentListFilterSerializer(serializers.Serializer):
    search = serializers.CharField(max_length=255, required=False, allow_blank=True)
    creator = serializers.IntegerField(min_value=1, max_value=2**63 - 1, required=False)


class DocumentUploadSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    file = serializers.ImageField(help_text="Real JPEG (including MPO) or PNG image, at most 10 MiB (10,485,760 bytes).")

    def validate_file(self, uploaded_file):
        if uploaded_file.size > MAX_IMAGE_SIZE:
            raise serializers.ValidationError(
                "Image size must not exceed 10 MiB.",
            )

        if uploaded_file.image.format not in ALLOWED_IMAGE_FORMATS:
            raise serializers.ValidationError(
                "Only JPEG (including MPO) and PNG images are allowed.",
            )

        try:
            uploaded_file.seek(0)
            with Image.open(uploaded_file) as image:
                # MPO contains multiple JPEG pictures. Validate each one while
                # retaining the original bytes and Pillow's detected MIME type.
                frames = image.n_frames if image.format == "MPO" else 1
                for frame in range(frames):
                    image.seek(frame)
                    # MPO seek() does not repeat Image.open()'s pixel guard.
                    if Image.MAX_IMAGE_PIXELS is not None and image.width * image.height > 2 * Image.MAX_IMAGE_PIXELS:
                        raise Image.DecompressionBombError("Image exceeds the Pillow pixel limit.")
                    image.load()
        except (
            EOFError, IndexError, OSError, SyntaxError, TypeError, ValueError,
            struct.error, Image.DecompressionBombError,
        ):
            raise serializers.ValidationError("Upload a complete, valid image.") from None
        finally:
            uploaded_file.seek(0)
        return uploaded_file


class DocumentUpdateSerializer(DocumentUploadSerializer):
    file = serializers.ImageField(
        required=False,
        help_text="Optional replacement JPEG (including MPO) or PNG image, at most 10 MiB (10,485,760 bytes).",
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
            "status",
            "creator",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
