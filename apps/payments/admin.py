from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    # 'status' (list_filter below) is the admin-visible flag for manual
    # follow-up on a failed refund — filter to STATUS_REFUND_FAILED to find
    # payments that need a manual refund through the Payriff dashboard (see
    # apps.payments.service.refund_payment).
    list_display = [
        'id', 'doctor', 'patient', 'appointment', 'amount', 'currency', 'status',
        'created_at', 'refunded_at',
    ]
    list_filter = ['status', 'provider', 'currency']
    search_fields = ['doctor__email', 'patient__email', 'provider_order_id']
    raw_id_fields = ['appointment', 'doctor', 'patient']
