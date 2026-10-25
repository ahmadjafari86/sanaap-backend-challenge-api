from uuid import uuid4

from django.db import models
from django.utils import timezone


class OutboxMessage(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        PUBLISHED = "PUBLISHED", "Published"
        DEAD = "DEAD", "Needs explicit replay"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    dedupe_key = models.CharField(max_length=255, unique=True)
    kind = models.CharField(max_length=64)
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
        from .validation import validate_message
        validate_message(kind=self.kind, document_id=self.document_id,
                         payload=self.payload, schema_version=self.schema_version)


class OutboxReceipt(models.Model):
    # Independent from the document and message lifecycle; no cascade removes deduplication.
    event_id = models.UUIDField(primary_key=True, editable=False)
    processed_at = models.DateTimeField(default=timezone.now)
