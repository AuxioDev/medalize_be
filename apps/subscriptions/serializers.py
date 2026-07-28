from rest_framework import serializers

from .plans import PLANS_FOR_ROLE, ROLE_DOCTOR


class SubscriptionCheckoutSerializer(serializers.Serializer):
    """``plan`` is restricted to the caller's own role's plans via
    ``context['role']`` (defaults to doctor, the original/only caller) —
    this is the first of two layers guarding against a hospital account
    buying a doctor-priced plan or vice versa; the second is the
    role-membership check in apps.subscriptions.service.
    create_subscription_checkout, which every view here still goes through
    even though this field should already reject the wrong code."""

    plan = serializers.ChoiceField(choices=[])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        role = self.context.get('role', ROLE_DOCTOR)
        self.fields['plan'].choices = list(PLANS_FOR_ROLE[role])
