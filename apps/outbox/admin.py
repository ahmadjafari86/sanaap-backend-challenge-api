import json

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import OutboxMessage, OutboxReceipt


class ReadOnlyOutboxAdmin(admin.ModelAdmin):
    actions = None

    # Delivery state and deduplication records are maintained by the outbox
    # services; the admin is an inspection interface, including for superusers.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OutboxMessage)
class OutboxMessageAdmin(ReadOnlyOutboxAdmin):
    list_display = ("id", "kind", "document_id", "status", "attempts", "last_error_code", "created_at")
    list_filter = ("status", "kind", "last_error_code", "created_at")
    search_fields = ("id__exact", "document_id__exact", "dedupe_key", "kind", "last_error_code")
    ordering = ("-created_at", "-id")
    list_per_page = 50
    fields = readonly_fields = (
        "id", "kind", "schema_version", "document_id", "dedupe_key", "status", "attempts",
        "available_at", "published_at", "last_error_code", "lease_token", "locked_until",
        "payload_details", "processing_receipt", "created_at", "updated_at",
    )

    @admin.display(description="Payload")
    def payload_details(self, obj):
        return format_html("<pre>{}</pre>", json.dumps(obj.payload, indent=2, ensure_ascii=False, sort_keys=True))

    @admin.display(description="Processing receipt")
    def processing_receipt(self, obj):
        if not OutboxReceipt.objects.filter(pk=obj.pk).exists():
            return "-"
        url = reverse("admin:outbox_outboxreceipt_change", args=[obj.pk])
        return format_html('<a href="{}">{}</a>', url, obj.pk)


@admin.register(OutboxReceipt)
class OutboxReceiptAdmin(ReadOnlyOutboxAdmin):
    list_display = ("event_id", "processed_at")
    list_filter = ("processed_at",)
    search_fields = ("event_id__exact",)
    ordering = ("-processed_at", "-event_id")
    list_per_page = 50
    fields = readonly_fields = ("event_id", "processed_at", "outbox_message")

    @admin.display(description="Outbox message")
    def outbox_message(self, obj):
        if not OutboxMessage.objects.filter(pk=obj.event_id).exists():
            return "-"
        url = reverse("admin:outbox_outboxmessage_change", args=[obj.event_id])
        return format_html('<a href="{}">{}</a>', url, obj.event_id)
