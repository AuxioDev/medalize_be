from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['id', 'doctor', 'patient', 'appointment', 'amount', 'currency', 'status', 'created_at']
    list_filter = ['status', 'provider', 'currency']
    search_fields = ['doctor__email', 'patient__email', 'provider_order_id']
    raw_id_fields = ['appointment', 'doctor', 'patient']
