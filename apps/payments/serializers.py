from rest_framework import serializers

from .models import Payment


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            'id', 'appointment', 'status', 'amount', 'currency',
            'payment_url', 'created_at', 'updated_at', 'paid_at',
        ]
        read_only_fields = fields
