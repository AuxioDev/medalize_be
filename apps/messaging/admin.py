from django.contrib import admin

from .models import Message, Thread


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = ['id', 'patient', 'doctor', 'created_at', 'updated_at']
    search_fields = ['patient__email', 'doctor__email']
    raw_id_fields = ['patient', 'doctor']
    ordering = ['-updated_at']


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'thread', 'sender', 'read_at', 'created_at']
    list_filter = ['read_at']
    # body is encrypted at rest so it cannot be searched — the sender's email
    # is the only practical moderation entry point.
    search_fields = ['sender__email']
    raw_id_fields = ['thread', 'sender']
    ordering = ['-created_at']
