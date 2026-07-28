import logging

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.notifications.i18n import recipient_language

from .models import Payment
from .providers.payriff import PayriffProvider

logger = logging.getLogger(__name__)


def payments_enabled():
    """Same optional-external-service pattern as
    apps.assistant.views._assistant_enabled and USE_CLOUDINARY: both Payriff
    env vars empty ⇒ the feature is off, payment endpoints answer 503, and
    the rest of the booking flow is completely unaffected — payment is never
    a required step of booking (see AppointmentPaymentView / boundaries in
    the phase spec)."""
    return bool(
        getattr(settings, 'PAYRIFF_MERCHANT_ID', '')
        and getattr(settings, 'PAYRIFF_SECRET_KEY', '')
    )


def get_provider():
    # The one seam a future second provider (or a Payriff API version swap)
    # would plug into — views/service code never constructs PayriffProvider
    # directly anywhere else.
    return PayriffProvider()


def _doctor_display_name(doctor):
    return f'{doctor.first_name} {doctor.last_name}'.strip() or doctor.email


def _return_url(result, lang):
    # settings.BACKEND_BASE_URL + reverse() rather than request.
    # build_absolute_uri() — get_or_create_payment() takes only an
    # appointment (see the phase spec's service-layer signature), and these
    # URLs are handed to Payriff, not rendered back to the calling client, so
    # there is no HttpRequest in scope here.
    base = settings.BACKEND_BASE_URL.rstrip('/')
    path = reverse('payriff-return')
    return f'{base}{path}?result={result}&lang={lang}'


def get_or_create_payment(appointment):
    """Idempotent per-appointment payment creation:
    - an existing PENDING payment is returned as-is (its payment_url is
      reused — no duplicate order is opened with the provider);
    - an existing PAID payment is also returned as-is (idempotent — the
      caller decides what to show the patient for an already-paid
      appointment);
    - a previous FAILED/CANCELLED payment is reset in place and a fresh
      provider order is opened for it (the appointment↔payment relationship
      is OneToOne, so there is never more than one Payment row per
      appointment to retry against);
    - otherwise a brand new Payment is created.

    Wrapped in select_for_update() on the appointment row: without it, two
    near-simultaneous calls (double-tap "Pay", or a retried request) can both
    read "no payment yet", both call the provider, and then race on the
    OneToOneField insert — one succeeds, the other hits an IntegrityError
    (surfaced as a raw 500) after already having opened a second, orphaned
    order with Payriff. Locking the appointment row serializes the two calls
    so the second one sees the first's just-created PENDING payment and
    returns it instead of creating a duplicate. The lock is held across the
    provider.create_order() network call deliberately — the race window is
    exactly that call, and this endpoint is single-appointment-scoped
    (one patient paying for their own booking), not high-concurrency.
    """
    with transaction.atomic():
        appointment = Appointment.objects.select_for_update().get(pk=appointment.pk)
        try:
            payment = appointment.payment
        except Payment.DoesNotExist:
            payment = None

        if payment is not None and payment.status in (Payment.STATUS_PENDING, Payment.STATUS_PAID):
            return payment

        doctor = appointment.doctor
        fee = getattr(doctor.doctor_profile, 'consultation_fee', None) or 0
        lang = recipient_language(appointment.patient)

        provider = get_provider()
        order = provider.create_order(
            amount=fee,
            currency='AZN',
            description=f'Medalize — {_doctor_display_name(doctor)}',
            approve_url=_return_url('approve', lang),
            cancel_url=_return_url('cancel', lang),
            decline_url=_return_url('decline', lang),
        )

        if payment is not None:
            payment.amount = fee
            payment.status = Payment.STATUS_PENDING
            payment.provider = 'payriff'
            payment.provider_order_id = order.order_id
            payment.provider_session_id = order.session_id
            payment.payment_url = order.payment_url
            payment.paid_at = None
            payment.save()
            return payment

        return Payment.objects.create(
            appointment=appointment,
            patient=appointment.patient,
            dependent=appointment.dependent,
            doctor=doctor,
            amount=fee,
            currency='AZN',
            provider='payriff',
            provider_order_id=order.order_id,
            provider_session_id=order.session_id,
            payment_url=order.payment_url,
        )


def handle_webhook_ping(provider_order_id):
    """Defensive webhook handler — the whole reason it takes only a
    ``provider_order_id`` and nothing else from the webhook body.

    The webhook is treated as NOTHING MORE than a trigger to re-check: the
    backend calls ``provider.check_status(provider_order_id)`` itself (the
    same authenticated way as order creation) and updates ``Payment`` based
    on THAT response only — never on whatever status the webhook payload
    itself claims. This is good practice generally, and it specifically
    neutralizes the unconfirmed webhook signature scheme (see
    PayriffProvider's docstring): a forged or malformed webhook can supply a
    real ``provider_order_id`` and nothing else useful — it cannot fabricate
    a "paid" status without Payriff's own API agreeing when asked directly.
    """
    try:
        payment = Payment.objects.select_related('doctor', 'patient').get(
            provider_order_id=provider_order_id,
        )
    except Payment.MultipleObjectsReturned:
        # Should be unreachable — provider_order_id carries a partial unique
        # constraint (see Payment.Meta) — but this is the one exception type
        # the original code left uncaught: it used to fall through to the
        # view's blanket `except Exception`, log, and answer 200, silently
        # never marking the payment paid. Fail loud in the logs instead.
        logger.error('Multiple Payment rows share provider_order_id=%s', provider_order_id)
        return None
    except Payment.DoesNotExist:
        # Appointment payments and doctor-subscription payments share one
        # Payriff merchant and therefore one order-id space — a miss here
        # means the order belongs to a subscription checkout instead.
        from apps.subscriptions.service import handle_webhook_ping as handle_subscription_webhook_ping
        return handle_subscription_webhook_ping(provider_order_id)

    provider = get_provider()
    try:
        new_status = provider.check_status(provider_order_id)
    except Exception:
        logger.exception('Payriff check_status failed for order %s', provider_order_id)
        return payment

    valid_statuses = {choice[0] for choice in Payment.STATUS_CHOICES}
    if new_status not in valid_statuses:
        logger.warning(
            'Payriff check_status returned unrecognized status %r for order %s',
            new_status, provider_order_id,
        )
        return payment

    was_paid = payment.status == Payment.STATUS_PAID
    payment.status = new_status
    if new_status == Payment.STATUS_PAID and not payment.paid_at:
        payment.paid_at = timezone.now()
    payment.save(update_fields=['status', 'paid_at', 'updated_at'])

    if new_status == Payment.STATUS_PAID and not was_paid:
        try:
            from apps.notifications.tasks import send_payment_received
            send_payment_received.delay(str(payment.id))
        except Exception:
            logger.exception('Failed to enqueue payment notification for payment %s', payment.id)

    return payment
