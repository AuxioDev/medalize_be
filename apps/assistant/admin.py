from django.contrib import admin

from .models import Conversation, Message


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ['id', 'patient', 'created_at', 'updated_at']
    search_fields = ['patient__email']
    ordering = ['-updated_at']


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'conversation', 'role', 'flagged', 'created_at']
    list_filter = ['flagged', 'role']
    # content is encrypted at rest so it cannot be searched; flag_reason and
    # the owner's email are the moderation entry points.
    search_fields = ['conversation__patient__email', 'flag_reason']
    ordering = ['-created_at']
