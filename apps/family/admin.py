from django.contrib import admin

from .models import Dependent


@admin.register(Dependent)
class DependentAdmin(admin.ModelAdmin):
    list_display = ['first_name', 'last_name', 'relationship', 'managed_by', 'is_active', 'created_at']
    list_filter = ['relationship', 'is_active']
    search_fields = ['first_name', 'last_name', 'managed_by__email']
    raw_id_fields = ['managed_by']
