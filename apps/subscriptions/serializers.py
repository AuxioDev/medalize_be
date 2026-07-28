from rest_framework import serializers

from .plans import PLAN_PRICES


class SubscriptionCheckoutSerializer(serializers.Serializer):
    plan = serializers.ChoiceField(choices=list(PLAN_PRICES.keys()))
