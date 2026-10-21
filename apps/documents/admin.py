from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "creator", "status", "content_type", "file_size", "created_at")
    list_filter = ("status", "content_type", "created_at")
    search_fields = ("title", "original_filename", "creator__username")
    list_select_related = ("creator",)
    ordering = ("-created_at", "-id")
    fields = readonly_fields = (
        "id", "title", "original_filename", "creator", "status", "content_type",
        "file_size", "created_at", "updated_at",
    )
    actions = None

    # Document writes must use the services that maintain storage, cache and audit state.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
