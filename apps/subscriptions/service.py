import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.notifications.i18n import recipient_language
from apps.payments.service import _return_url as _payriff_return_url
from apps.payments.service import get_provider, payments_enabled

from .models import Subscription, SubscriptionPayment
from .plans import PERIOD_DAYS, PLANS_FOR_ROLE, PLAN_NAMES, PLAN_PRICES

logger = logging.getLogger(__name__)

# Re-exported so apps.payments.service can delegate here without both
# modules hardcoding "payments_enabled" as the shared feature flag name.
__all__ = ['create_subscription_checkout', 'handle_webhook_ping', 'payments_enabled']


def create_subscription_checkout(user, plan):
    """Open a one-shot Payriff hosted-checkout order for a subscription
    payment. Locks the account's Subscription row across the provider
    call for the same reason apps.payments.service.get_or_create_payment
    locks the appointment row: to serialize a double-tap "Subscribe" into
    one provider order instead of two.

    Unlike appointment payments there is no existing-pending-payment reuse
    here — each checkout attempt is its own SubscriptionPayment row, and
    only whichever one Payriff actually confirms paid ever changes the
    subscription's state (see handle_webhook_ping).

    Guards against a plan belonging to the wrong role — e.g. a hospital
    account POSTing a doctor plan code — in addition to the serializer-level
    ChoiceField restriction in each role's checkout view: both layers exist
    on purpose (apps.hospitals.views / apps.subscriptions.views), since a
    role-scoped serializer choice is easy to accidentally widen later
    without anyone noticing this service function is the last line of
    defense against selling doctor-priced entitlements to a hospital."""
    if plan not in PLAN_PRICES:
        raise ValueError(f'Unknown plan: {plan!r}')
    if plan not in PLANS_FOR_ROLE.get(user.role, ()):
        raise ValueError(f'Plan {plan!r} is not available for role {user.role!r}')

    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(user=user)

        amount = PLAN_PRICES[plan]
        lang = recipient_language(user)
        provider = get_provider()
        order = provider.create_order(
            amount=amount,
            currency='AZN',
            description=f'Medalize — {PLAN_NAMES[plan]} subscription',
            approve_url=_payriff_return_url('approve', lang),
            cancel_url=_payriff_return_url('cancel', lang),
            decline_url=_payriff_return_url('decline', lang),
        )

        now = timezone.now()
        return SubscriptionPayment.objects.create(
            subscription=subscription,
            plan=plan,
            amount=amount,
            currency='AZN',
            provider=provider.name,
            provider_order_id=order.order_id,
            provider_session_id=order.session_id,
            payment_url=order.payment_url,
            period_start=now,
            period_end=now + timedelta(days=PERIOD_DAYS),
        )


def handle_webhook_ping(provider_order_id):
    """Called by apps.payments.service.handle_webhook_ping when a Payriff
    webhook's order id doesn't match any appointment Payment — the two
    order-id spaces share one Payriff merchant, so a single webhook endpoint
    routes to whichever table actually owns the order. Same defensive
    pattern as the appointment-payment path: the webhook body is never
    trusted, only used as a nudge to ask Payriff directly via
    check_status()."""
    try:
        payment = SubscriptionPayment.objects.select_related('subscription').get(
            provider_order_id=provider_order_id,
        )
    except SubscriptionPayment.MultipleObjectsReturned:
        logger.error(
            'Multiple SubscriptionPayment rows share provider_order_id=%s', provider_order_id,
        )
        return None
    except SubscriptionPayment.DoesNotExist:
        logger.warning(
            'Payriff webhook ping for unknown provider_order_id=%s (checked both '
            'Payment and SubscriptionPayment)', provider_order_id,
        )
        return None

    provider = get_provider()
    try:
        new_status = provider.check_status(provider_order_id)
    except Exception:
        logger.exception('Payriff check_status failed for subscription order %s', provider_order_id)
        return payment

    valid_statuses = {choice[0] for choice in SubscriptionPayment.STATUS_CHOICES}
    if new_status not in valid_statuses:
        logger.warning(
            'Payriff check_status returned unrecognized status %r for subscription order %s',
            new_status, provider_order_id,
        )
        return payment

    was_paid = payment.status == SubscriptionPayment.STATUS_PAID
    payment.status = new_status
    if new_status == SubscriptionPayment.STATUS_PAID and not payment.paid_at:
        payment.paid_at = timezone.now()
    payment.save(update_fields=['status', 'paid_at', 'updated_at'])

    if new_status == SubscriptionPayment.STATUS_PAID and not was_paid:
        _activate_subscription(payment)

    return payment


def _activate_subscription(payment):
    """Extends the subscription period from whichever is later: the current
    period end (a renewal paid before expiry) or now (a lapsed/first-time
    subscriber) — so paying early never shortens what the account already
    has, and paying late never backdates the new period into the past."""
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(pk=payment.subscription_id)
        now = timezone.now()
        base = (
            subscription.current_period_end
            if subscription.current_period_end and subscription.current_period_end > now
            else now
        )
        subscription.plan = payment.plan
        subscription.status = Subscription.STATUS_ACTIVE
        subscription.current_period_end = base + timedelta(days=PERIOD_DAYS)
        subscription.grace_ends_at = None
        subscription.last_reminder_stage = Subscription.REMINDER_NONE
        subscription.save()

    try:
        from apps.notifications.tasks import send_subscription_notification
        send_subscription_notification.delay(str(subscription.user_id), 'subscription_activated')
    except Exception:
        logger.exception(
            'Failed to enqueue subscription-activated notification for account %s',
            subscription.user_id,
        )
