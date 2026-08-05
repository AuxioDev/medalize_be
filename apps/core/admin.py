from django.contrib import admin

from .models import RecordAccessLog


@admin.register(RecordAccessLog)
class RecordAccessLogAdmin(admin.ModelAdmin):
    """Read-only — this log exists for future support/compliance lookups,
    not for anyone to edit or backfill by hand."""

    list_display = ['accessed_by', 'action', 'content_type', 'object_id', 'created_at']
    list_filter = ['action', 'content_type']
    search_fields = ['accessed_by__email']
    raw_id_fields = ['accessed_by']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
