from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils import timezone


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


class OutboxMessage(models.Model):
    class Kind(models.TextChoices):
        UPLOAD_REQUESTED = "document.upload_requested", "Upload document"
        OBJECT_DELETE = "storage.object_delete_requested", "Delete object"
        STAGING_DELETE = "storage.staging_delete_requested", "Delete staged file"
        METADATA_INVALIDATE = "document.metadata_invalidate_requested", "Invalidate metadata"
        NOTIFICATION = "document.notification_requested", "Publish notification"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        PUBLISHED = "PUBLISHED", "Published"
        DEAD = "DEAD", "Needs explicit replay"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    dedupe_key = models.CharField(max_length=255, unique=True)
    kind = models.CharField(max_length=64, choices=Kind.choices)
    schema_version = models.PositiveSmallIntegerField(default=1)
    # Snapshot: document deletion must retain its cleanup obligations.
    document_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    lease_token = models.UUIDField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "available_at", "created_at"], name="outbox_due_idx"),
            models.Index(fields=["status", "locked_until"], name="outbox_lease_idx"),
            models.Index(fields=["kind", "status", "published_at"], name="outbox_upload_idx"),
        ]

    def clean(self):
        super().clean()
        from .outbox import validate_message
        validate_message(kind=self.kind, document_id=self.document_id,
                         payload=self.payload, schema_version=self.schema_version)


class OutboxReceipt(models.Model):
    # Independent from the document and message lifecycle; no cascade removes deduplication.
    event_id = models.UUIDField(primary_key=True, editable=False)
    processed_at = models.DateTimeField(default=timezone.now)
