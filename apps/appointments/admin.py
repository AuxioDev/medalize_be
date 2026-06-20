from django.contrib import admin

from .models import Appointment


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ['id', 'doctor', 'patient', 'workplace', 'starts_at', 'status']
    list_filter = ['status']
    search_fields = ['doctor__email', 'patient__email']
    ordering = ['-starts_at']
