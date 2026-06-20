from django.contrib import admin

from .models import FCMToken, Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'type', 'title', 'is_read', 'sent_at']
    list_filter = ['type', 'is_read']
    search_fields = ['user__email', 'title']


@admin.register(FCMToken)
class FCMTokenAdmin(admin.ModelAdmin):
    list_display = ['user', 'token', 'created_at']
    search_fields = ['user__email']
