from django.contrib import admin

from .models import Appointment, Favorite, Review


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'doctor', 'patient', 'workplace', 'starts_at', 'status', 'doctor_cancelled_late',
    ]
    list_filter = ['status', 'doctor_cancelled_late']
    search_fields = ['doctor__email', 'patient__email']
    ordering = ['-starts_at']


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ['patient', 'doctor', 'created_at']
    search_fields = ['doctor__email', 'patient__email']
    ordering = ['-created_at']


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['doctor', 'patient', 'rating', 'needs_manual_review', 'created_at']
    list_filter = ['rating', 'needs_manual_review']
    search_fields = ['doctor__email', 'patient__email']
    ordering = ['-created_at']
