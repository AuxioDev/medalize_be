from django.contrib import admin

from .models import Appointment, Review


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ['id', 'doctor', 'patient', 'workplace', 'starts_at', 'status']
    list_filter = ['status']
    search_fields = ['doctor__email', 'patient__email']
    ordering = ['-starts_at']


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['doctor', 'patient', 'rating', 'created_at']
    list_filter = ['rating']
    search_fields = ['doctor__email', 'patient__email']
    ordering = ['-created_at']
