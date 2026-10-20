from rest_framework import serializers

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