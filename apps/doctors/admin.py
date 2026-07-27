from django.contrib import admin

from .models import BlockedPeriod, Workplace, WorkingHours


@admin.register(Workplace)
class WorkplaceAdmin(admin.ModelAdmin):
    list_display = ['name', 'city', 'region', 'type', 'is_primary', 'doctor']
    list_filter = ['region', 'type', 'is_primary', 'city']
    search_fields = ['name', 'doctor__email', 'city', 'address']
    raw_id_fields = ['doctor']


@admin.register(WorkingHours)
class WorkingHoursAdmin(admin.ModelAdmin):
    list_display = ['workplace', 'weekday', 'start_time', 'end_time', 'is_active']
    list_filter = ['weekday', 'is_active']
    search_fields = ['workplace__name', 'workplace__doctor__email']


@admin.register(BlockedPeriod)
class BlockedPeriodAdmin(admin.ModelAdmin):
    list_display = ['doctor', 'workplace', 'starts_at', 'ends_at']
    list_filter = ['doctor']
    search_fields = ['doctor__email']
    raw_id_fields = ['doctor', 'workplace']
