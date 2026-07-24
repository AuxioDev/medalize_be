from django.contrib import admin

from .models import MedicalRecord


@admin.register(MedicalRecord)
class MedicalRecordAdmin(admin.ModelAdmin):
    list_display = ['title', 'patient', 'record_type', 'record_date', 'created_at']
    list_filter = ['record_type']
    search_fields = ['title', 'patient__email']
    raw_id_fields = ['patient']
