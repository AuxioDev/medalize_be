import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from .plans import PLAN_BASIC, PLAN_HOSPITAL_BASIC, PLAN_HOSPITAL_PRO, PLAN_PRO


class Subscription(models.Model):
    """One row per billable account (doctor or hospital), created at
    registration (see signals.py) and never deleted — it is that account's
    whole billing history in one place, not a per-period record. ``plan``
    deliberately survives past_due/expired transitions: it is the LAST plan
    the account held (paid for, or was trialing under, for doctors), so a
    lapsed account's enforcement (see apps.subscriptions.entitlements)
    degrades from its own tier rather than resetting to the cheapest one.

    Shared by both doctors and hospitals (see apps.subscriptions.plans —
    TRIAL_ROLES, PLANS_FOR_ROLE, LIMITS_BY_ROLE) rather than split into two
    models: they share one Payriff order-id space (see
    apps.subscriptions.service.handle_webhook_ping and
    apps.payments.service's fallback lookup) and one hourly sweep
    (apps.subscriptions.tasks.sweep_subscriptions) — a second table would
    duplicate both. Renamed from DoctorSubscription when hospital accounts
    were added; ``related_name='subscription'`` kept unchanged so every
    existing doctor-only call site (apps.appointments, apps.messaging,
    apps.doctors, apps.assistant) is untouched.
    """

    STATUS_PENDING = 'pending'
    STATUS_TRIALING = 'trialing'
    STATUS_ACTIVE = 'active'
    STATUS_PAST_DUE = 'past_due'
    STATUS_EXPIRED = 'expired'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_TRIALING, 'Trialing'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_PAST_DUE, 'Past due'),
        (STATUS_EXPIRED, 'Expired'),
    ]

    PLAN_CHOICES = [
        ('', 'None'),
        (PLAN_BASIC, 'Başlanğıc'),
        (PLAN_PRO, 'Peşəkar'),
        (PLAN_HOSPITAL_BASIC, 'Klinika'),
        (PLAN_HOSPITAL_PRO, 'Klinika Plus'),
    ]

    REMINDER_NONE = ''
    REMINDER_T3 = 't-3'
    REMINDER_T1 = 't-1'
    REMINDER_T0 = 't-0'
    REMINDER_CHOICES = [
        (REMINDER_NONE, 'None'),
        (REMINDER_T3, 'T-3 days'),
        (REMINDER_T1, 'T-1 day'),
        (REMINDER_T0, 'T-0'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscription',
    )
    # 20, not 10: 'hospital_basic' is 14 characters.
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, blank=True, default='')
    # Indexed: every doctor-facing search/booking/ranking query filters or
    # orders on this, same reasoning as DoctorProfile.is_verified.
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True,
    )
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True, db_index=True)
    grace_ends_at = models.DateTimeField(null=True, blank=True)
    # Dedupes the hourly sweep_subscriptions reminder sends — without this a
    # retried/overlapping task run would re-send the same T-3/T-1/T-0 email.
    last_reminder_stage = models.CharField(
        max_length=5, choices=REMINDER_CHOICES, blank=True, default='',
    )
    # Always False today — Payriff has no confirmed card-on-file/recurring-
    # charge API (see apps.payments.providers.payriff.PayriffProvider's
    # docstring), so every renewal is a doctor-initiated checkout. The column
    # exists so a future auto-renew rollout is a data migration, not a schema
    # change.
    auto_renew = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user.email} — {self.status} ({self.plan or "no plan"})'


class SubscriptionPayment(models.Model):
    """One row per checkout attempt for a doctor or hospital subscription.

    Deliberately NOT apps.payments.Payment: that model's ``appointment``
    field is a non-nullable OneToOneField and its idempotency contract
    (service.get_or_create_payment) depends on exactly one row per
    appointment — reusing it here would mean loosening that constraint for
    an unrelated feature. Field shape otherwise mirrors Payment so
    apps.payments.service's provider-facing patterns (get_provider(),
    STATUS_* handling in handle_webhook_ping) carry over directly.
    """

    STATUS_PENDING = 'pending'
    STATUS_PAID = 'paid'
    STATUS_FAILED = 'failed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'), (STATUS_PAID, 'Paid'),
        (STATUS_FAILED, 'Failed'), (STATUS_CANCELLED, 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name='subscription_payments',
    )
    plan = models.CharField(max_length=20, choices=Subscription.PLAN_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='AZN')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    provider = models.CharField(max_length=20, default='payriff')
    # Indexed: the webhook and check_status re-verification both look a
    # SubscriptionPayment up by this field, never by our own id. Unique
    # (excluding blank) so a colliding order id can never silently match the
    # wrong row — see the identical constraint on apps.payments.Payment.
    provider_order_id = models.CharField(max_length=255, blank=True, db_index=True)
    provider_session_id = models.CharField(max_length=255, blank=True)
    payment_url = models.URLField(max_length=500, blank=True)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['provider_order_id'],
                condition=~Q(provider_order_id=''),
                name='subscriptionpayment_unique_provider_order_id',
            ),
        ]

    def __str__(self):
        return (
            f'{self.subscription.user.email} — {self.plan}: '
            f'{self.amount} {self.currency} ({self.status})'
        )
