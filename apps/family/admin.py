from django.contrib import admin

from .models import Dependent


@admin.register(Dependent)
class DependentAdmin(admin.ModelAdmin):
    list_display = [
        'first_name', 'last_name', 'relationship', 'managed_by',
        'consent_notice_sent_at', 'consent_objected_at', 'is_active', 'created_at',
    ]
    list_filter = ['relationship', 'is_active']
    search_fields = ['first_name', 'last_name', 'managed_by__email', 'contact_email']
    raw_id_fields = ['managed_by']
    # Token hash/expiry are set exclusively by apps.family.services — never
    # hand-edited from admin.
    readonly_fields = [
        'consent_token_hash', 'consent_token_expires_at',
        'consent_notice_sent_at', 'consent_objected_at',
    ]
