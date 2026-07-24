from django.contrib import admin

from .models import Prescription, PrescriptionItem


class PrescriptionItemInline(admin.TabularInline):
    model = PrescriptionItem
    extra = 0


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ['id', 'doctor', 'patient', 'appointment', 'issued_at']
    search_fields = ['doctor__email', 'patient__email']
    raw_id_fields = ['appointment', 'doctor', 'patient']
    inlines = [PrescriptionItemInline]
