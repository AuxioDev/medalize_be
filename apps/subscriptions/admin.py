from django.contrib import admin

from .models import DoctorSubscription, SubscriptionPayment


@admin.register(DoctorSubscription)
class DoctorSubscriptionAdmin(admin.ModelAdmin):
    list_display = ['user', 'plan', 'status', 'trial_ends_at', 'current_period_end', 'grace_ends_at']
    list_filter = ['status', 'plan']
    search_fields = ['user__email']
    raw_id_fields = ['user']


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ['id', 'subscription', 'plan', 'amount', 'currency', 'status', 'created_at']
    list_filter = ['status', 'plan', 'provider', 'currency']
    search_fields = ['subscription__user__email', 'provider_order_id']
    raw_id_fields = ['subscription']
