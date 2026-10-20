from django.conf import settings
from django.db import models


class Document(models.Model):
    title = models.CharField(max_length=255)
    original_filename = models.CharField(max_length=255)
    object_key = models.CharField(max_length=1024, unique=True)
    content_type = models.CharField(max_length=100)
    file_size = models.PositiveBigIntegerField()

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="documents",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title