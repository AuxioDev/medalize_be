from django.contrib import admin

from .models import Block, Message, Report, Thread


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


@admin.register(Block)
class BlockAdmin(admin.ModelAdmin):
    list_display = ['blocker', 'blocked', 'created_at']
    search_fields = ['blocker__email', 'blocked__email']
    raw_id_fields = ['blocker', 'blocked']
    ordering = ['-created_at']


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ['reporter', 'thread', 'message', 'reason', 'created_at']
    list_filter = ['reason']
    search_fields = ['reporter__email', 'thread__patient__email', 'thread__doctor__email']
    raw_id_fields = ['thread', 'message', 'reporter']
    ordering = ['-created_at']
