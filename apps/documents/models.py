from uuid import uuid4

from django.conf import settings
from django.db import models


class Document(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending storage"
        READY = "READY", "Ready"
        FAILED = "FAILED", "Storage failed"

    title = models.CharField(max_length=255)
    original_filename = models.CharField(max_length=255)
    object_key = models.CharField(max_length=1024, unique=True)
    content_type = models.CharField(max_length=100)
    file_size = models.PositiveBigIntegerField()
    metadata_version = models.UUIDField(default=uuid4, editable=False)
    status = models.CharField(max_length=7, choices=Status.choices, default=Status.READY)
    staging_key = models.CharField(max_length=64, blank=True, editable=False)
    storage_attempts = models.PositiveSmallIntegerField(default=0, editable=False)

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="documents",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class DocumentAuditEvent(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Create document"
        UPDATE = "update", "Update document"
        DELETE = "delete", "Delete document"
        DOWNLOAD_URL = "download_url", "Issue download URL"
        STORE = "store", "Background document storage"

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"

    # Snapshots deliberately have no foreign keys: deletion must retain history.
    actor_id = models.PositiveBigIntegerField(null=True, editable=False, db_index=True)
    document_id = models.PositiveBigIntegerField(null=True, editable=False, db_index=True)
    action = models.CharField(max_length=16, choices=Action.choices, editable=False)
    outcome = models.CharField(max_length=7, choices=Outcome.choices, editable=False)
    status_code = models.PositiveSmallIntegerField(editable=False)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-occurred_at", "-id")
