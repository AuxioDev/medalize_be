from rest_framework import serializers

from apps.family.serializers import DependentBriefSerializer

from .models import Payment


class PaymentSerializer(serializers.ModelSerializer):
    dependent = DependentBriefSerializer(read_only=True)

    class Meta:
        model = Payment
        fields = [
            'id', 'appointment', 'status', 'amount', 'currency', 'dependent',
            'payment_url', 'created_at', 'updated_at', 'paid_at',
        ]
        read_only_fields = fields
