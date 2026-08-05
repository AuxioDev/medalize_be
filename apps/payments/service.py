import logging

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.notifications.i18n import recipient_language

from .models import Payment
from .providers.mock import MockCardProvider
from .providers.payriff import PayriffProvider

logger = logging.getLogger(__name__)


def payments_enabled():
    """Same optional-external-service pattern as
    apps.assistant.views._assistant_enabled and USE_CLOUDINARY: both Payriff
    env vars empty ⇒ the feature is off, payment endpoints answer 503, and
    the rest of the booking flow is completely unaffected — payment is never
    a required step of booking (see AppointmentPaymentView / boundaries in
    the phase spec).

    The mock provider (see get_provider) needs no credentials at all, so it
    is always enabled regardless of PAYRIFF_* — only the real 'payriff'
    provider gates on them being configured."""
    if getattr(settings, 'PAYMENT_PROVIDER', 'payriff') == 'mock':
        return True
    return bool(
        getattr(settings, 'PAYRIFF_MERCHANT_ID', '')
        and getattr(settings, 'PAYRIFF_SECRET_KEY', '')
    )


def get_provider():
    # The one seam a future second provider (or a Payriff API version swap)
    # would plug into — views/service code never constructs a provider class
    # directly anywhere else. settings.PAYMENT_PROVIDER='mock' (the current
    # default — real Payriff credentials/verification aren't wired up yet,
    # see PayriffProvider's docstring) routes every payment surface
    # (appointments here, doctor/hospital subscriptions in
    # apps.subscriptions, which import this function rather than
    # constructing a provider themselves) through MockCardProvider instead.
    # Flip PAYMENT_PROVIDER back to 'payriff' (or unset it) once that
    # integration is verified against real credentials — nothing else here
    # needs to change.
    if getattr(settings, 'PAYMENT_PROVIDER', 'payriff') == 'mock':
        return MockCardProvider()
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
            payment.provider = provider.name
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
            provider=provider.name,
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


def refund_payment(payment, reason=''):
    """Idempotent, best-effort full refund of a single ``Payment`` via the
    active provider (mock or Payriff — see ``get_provider``). Called at
    every money-handling trigger point in the phase-1 cancellation-fee
    policy: patient cancellation (outside the window), doctor decline/
    cancellation (always), the stale-pending auto-decline sweep, and the
    doctor-deactivation mass-cancel. There are no partial refunds anywhere
    in this codebase — always the payment's full ``amount``.

    Mirrors ``get_or_create_payment``'s select_for_update() pattern: locks
    the ``Payment`` row for the duration of the provider call so two
    near-simultaneous triggers (e.g. a patient cancelling right as the
    stale-pending sweep also fires) can't double-refund.

    - ``STATUS_PAID`` → calls ``provider.refund_order()``. Success sets
      ``STATUS_REFUNDED`` (+ ``refunded_at``). Failure is caught here —
      never raised — and sets ``STATUS_REFUND_FAILED`` instead, so the
      caller's appointment-status change is never rolled back by a refund
      API failure; the exception is logged with ``reason`` for context, and
      ``STATUS_REFUND_FAILED`` is the intentional admin-visible flag for
      manual follow-up (PaymentAdmin filters/lists on ``status``; a human
      can then re-attempt the refund through the Payriff dashboard
      directly). This is deliberately a status value rather than a second
      boolean field — one field, already surfaced in admin, already
      filterable, with no risk of the two ever disagreeing.
    - Any other status (``PENDING`` — nothing was ever captured;
      ``FAILED``/``CANCELLED`` — same; already ``REFUNDED``/
      ``REFUND_FAILED`` — already handled) is a pure no-op: returns the
      Payment unchanged, no provider call, never an error. This is what
      makes it safe for every trigger point to call unconditionally instead
      of each one re-deriving "was this actually paid for".
    - Never raises.
    """
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        if payment.status != Payment.STATUS_PAID:
            return payment

        provider = get_provider()
        try:
            provider.refund_order(payment.provider_order_id, payment.amount)
        except Exception:
            logger.exception(
                'Refund failed for payment %s (order %s, reason=%s)',
                payment.id, payment.provider_order_id, reason,
            )
            payment.status = Payment.STATUS_REFUND_FAILED
            payment.save(update_fields=['status', 'updated_at'])
            return payment

        payment.status = Payment.STATUS_REFUNDED
        payment.refunded_at = timezone.now()
        payment.save(update_fields=['status', 'refunded_at', 'updated_at'])
        return payment


def refund_appointment_payment(appointment, reason=''):
    """Convenience wrapper for the cancellation/decline/expiry trigger
    points in apps.appointments.views, apps.notifications.tasks, and
    apps.users.models: looks up the appointment's Payment (if any) and
    refunds it via ``refund_payment``.

    A complete no-op — returns ``None``, no provider call — when the
    appointment was never paid for at all (payments disabled, or the
    patient never got as far as paying), which is the common case. Callers
    don't need their own ``try/except Payment.DoesNotExist``, and — like
    ``refund_payment`` — this never raises, so it's always safe to call
    unconditionally at a trigger point even when the caller doesn't know
    in advance whether a Payment row exists.
    """
    try:
        payment = appointment.payment
    except Payment.DoesNotExist:
        return None
    return refund_payment(payment, reason=reason)
