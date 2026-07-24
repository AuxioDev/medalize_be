from django.contrib import admin

from .models import DoseLog, Medication, MedicationSchedule


@admin.register(Medication)
class MedicationAdmin(admin.ModelAdmin):
    list_display = ['name', 'patient', 'source', 'is_active', 'created_at']
    list_filter = ['source', 'is_active']
    search_fields = ['name', 'patient__email']
    raw_id_fields = ['patient']


@admin.register(MedicationSchedule)
class MedicationScheduleAdmin(admin.ModelAdmin):
    list_display = ['medication', 'start_date', 'end_date', 'is_active']
    list_filter = ['is_active']
    raw_id_fields = ['medication']


@admin.register(DoseLog)
class DoseLogAdmin(admin.ModelAdmin):
    list_display = ['schedule', 'scheduled_at', 'status', 'created_at']
    list_filter = ['status']
    raw_id_fields = ['schedule']
