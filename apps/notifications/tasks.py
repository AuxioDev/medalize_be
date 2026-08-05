import datetime
import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .i18n import recipient_language, render_template

logger = logging.getLogger(__name__)

# Numeric date format readable in all supported languages. Localized month
# names (%b) would need locale.setlocale(), which is not thread-safe in a
# multi-worker Django/Celery process.
DATE_FORMAT = '%d.%m.%Y %H:%M'


def _display_name(user):
    """Bare full name (no honorific prefix — 'Dr.' etc. lives in the
    translated template text), falling back to the email address."""
    return f'{user.first_name} {user.last_name}'.strip() or user.email


def _prefs_for(user):
    from .models import NotificationPreference
    prefs, _ = NotificationPreference.objects.get_or_create(user=user)
    return prefs


def _send_email(subject, message, user):
    if not _prefs_for(user).email_enabled:
        return
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception('Failed to send email to %s', user.email)


def _send_push(user, title, body, data=None):
    """Send FCM push to all registered tokens for user. No-op if Firebase not configured
    or the user has disabled push notifications."""
    if not _prefs_for(user).push_enabled:
        return
    if not getattr(settings, 'FIREBASE_CREDENTIALS_JSON', ''):
        return
    try:
        from firebase_admin import messaging
        from .models import FCMToken
        tokens = list(FCMToken.objects.filter(user=user).values_list('token', flat=True))
        if not tokens:
            return
        notification = messaging.Notification(title=title, body=body)
        msg = messaging.MulticastMessage(
            notification=notification,
            tokens=tokens,
            data={k: str(v) for k, v in (data or {}).items()},
        )
        resp = messaging.send_each_for_multicast(msg)
        invalid = {
            tokens[i]
            for i, r in enumerate(resp.responses)
            if not r.success and r.exception and 'registration-token-not-registered' in str(r.exception)
        }
        if invalid:
            FCMToken.objects.filter(token__in=invalid).delete()
    except Exception:
        logger.exception('Failed to send push notification to user %s', user.pk)


@shared_task
def deliver_email_and_push(user_id, subject, title, message, data=None):
    """Deliver a single email + push to one user. Used by fan-out tasks so each
    recipient's (potentially slow) network I/O runs as its own job instead of
    serializing hundreds of sends inside one worker task."""
    from apps.users.models import User
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return
    _send_email(subject, message, user)
    _send_push(user, title, message, data=data or {})


@shared_task
def send_transactional_email(subject, message, recipient_email):
    """Security/account emails — password-reset codes, new-device login
    alerts, email-change codes and confirmations — that must always be
    delivered regardless of the recipient's discretionary notification
    preferences (unlike deliver_email_and_push, this never checks
    NotificationPreference.email_enabled). Takes the address directly rather
    than a user_id since some of these intentionally go to an address other
    than the account's current email (the new address being verified, or the
    old one being alerted of a change)."""
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient_email],
            fail_silently=False,
        )
    except Exception:
        logger.exception('Failed to send transactional email to %s', recipient_email)


@shared_task
def send_booking_confirmed(appointment_id):
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    tpl = render_template(
        'booking_confirmed', recipient_language(appt.patient),
        doctor_name=_display_name(appt.doctor),
        date_str=appt.starts_at.strftime(DATE_FORMAT),
    )

    Notification.objects.create(
        user=appt.patient,
        appointment=appt,
        type=Notification.TYPE_CONFIRMED,
        title=tpl['title'],
        message=tpl['body'],
    )
    _send_email(tpl['subject'], tpl['body'], appt.patient)
    _send_push(appt.patient, tpl['title'], tpl['body'],
               data={'type': 'appointment', 'appointment_id': str(appt.id)})


@shared_task
def send_booking_declined(appointment_id):
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    tpl = render_template(
        'booking_declined', recipient_language(appt.patient),
        doctor_name=_display_name(appt.doctor),
        date_str=appt.starts_at.strftime(DATE_FORMAT),
    )

    Notification.objects.create(
        user=appt.patient,
        appointment=appt,
        type=Notification.TYPE_CANCELLED,
        title=tpl['title'],
        message=tpl['body'],
    )
    _send_email(tpl['subject'], tpl['body'], appt.patient)
    _send_push(appt.patient, tpl['title'], tpl['body'],
               data={'type': 'appointment', 'appointment_id': str(appt.id)})


@shared_task
def send_booking_cancelled(appointment_id):
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    date_str = appt.starts_at.strftime(DATE_FORMAT)
    patient_tpl = render_template(
        'booking_cancelled_patient', recipient_language(appt.patient),
        doctor_name=_display_name(appt.doctor), date_str=date_str,
    )
    doctor_tpl = render_template(
        'booking_cancelled_doctor', recipient_language(appt.doctor),
        patient_name=_display_name(appt.patient), date_str=date_str,
    )

    Notification.objects.bulk_create([
        Notification(
            user=appt.patient,
            appointment=appt,
            type=Notification.TYPE_CANCELLED,
            title=patient_tpl['title'],
            message=patient_tpl['body'],
        ),
        Notification(
            user=appt.doctor,
            appointment=appt,
            type=Notification.TYPE_CANCELLED,
            title=doctor_tpl['title'],
            message=doctor_tpl['body'],
        ),
    ])
    _send_email(patient_tpl['subject'], patient_tpl['body'], appt.patient)
    _send_email(doctor_tpl['subject'], doctor_tpl['body'], appt.doctor)
    push_data = {'type': 'appointment', 'appointment_id': str(appt.id)}
    _send_push(appt.patient, patient_tpl['title'], patient_tpl['body'], data=push_data)
    _send_push(appt.doctor, doctor_tpl['title'], doctor_tpl['body'], data=push_data)


@shared_task
def send_rescheduling_required(appointment_id):
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    tpl = render_template(
        'rescheduling_required', recipient_language(appt.patient),
        doctor_name=_display_name(appt.doctor),
        date_str=appt.starts_at.strftime(DATE_FORMAT),
    )

    Notification.objects.create(
        user=appt.patient,
        appointment=appt,
        type=Notification.TYPE_RESCHEDULING,
        title=tpl['title'],
        message=tpl['body'],
    )
    _send_email(tpl['subject'], tpl['body'], appt.patient)
    _send_push(appt.patient, tpl['title'], tpl['body'],
               data={'type': 'appointment', 'appointment_id': str(appt.id)})


@shared_task
def send_appointment_rescheduled(appointment_id):
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    date_str = appt.starts_at.strftime(DATE_FORMAT)
    doctor_tpl = render_template(
        'appointment_rescheduled_doctor', recipient_language(appt.doctor),
        patient_name=_display_name(appt.patient), date_str=date_str,
    )
    patient_tpl = render_template(
        'appointment_rescheduled_patient', recipient_language(appt.patient),
        doctor_name=_display_name(appt.doctor), date_str=date_str,
    )

    push_data = {'type': 'appointment', 'appointment_id': str(appt.id)}
    Notification.objects.bulk_create([
        Notification(
            user=appt.doctor,
            appointment=appt,
            type=Notification.TYPE_GENERAL,
            title=doctor_tpl['title'],
            message=doctor_tpl['body'],
        ),
        Notification(
            user=appt.patient,
            appointment=appt,
            type=Notification.TYPE_GENERAL,
            title=patient_tpl['title'],
            message=patient_tpl['body'],
        ),
    ])
    _send_email(doctor_tpl['subject'], doctor_tpl['body'], appt.doctor)
    _send_email(patient_tpl['subject'], patient_tpl['body'], appt.patient)
    _send_push(appt.doctor, doctor_tpl['title'], doctor_tpl['body'], data=push_data)
    _send_push(appt.patient, patient_tpl['title'], patient_tpl['body'], data=push_data)


@shared_task
def send_booking_no_show(appointment_id):
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    tpl = render_template(
        'booking_no_show', recipient_language(appt.patient),
        doctor_name=_display_name(appt.doctor),
        date_str=appt.starts_at.strftime(DATE_FORMAT),
    )

    Notification.objects.create(
        user=appt.patient,
        appointment=appt,
        type=Notification.TYPE_GENERAL,
        title=tpl['title'],
        message=tpl['body'],
    )
    _send_email(tpl['subject'], tpl['body'], appt.patient)
    _send_push(appt.patient, tpl['title'], tpl['body'],
               data={'type': 'appointment', 'appointment_id': str(appt.id)})


@shared_task
def send_doctor_verified(user_id):
    from apps.users.models import User
    from .models import Notification
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    tpl = render_template('doctor_verified', recipient_language(user))
    Notification.objects.create(
        user=user,
        type=Notification.TYPE_GENERAL,
        title=tpl['title'],
        message=tpl['body'],
    )
    _send_email(tpl['subject'], tpl['body'], user)
    _send_push(user, tpl['title'], tpl['body'])


@shared_task
def send_doctor_verification_reset(user_id):
    """Counterpart to send_doctor_verified above — dispatched when an
    already-verified doctor edits specialization, license_number, or
    diploma_file and apps.doctors.services.notify_verification_reset flips
    is_verified back to False for re-review (see
    apps.doctors.serializers.DoctorProfileWriteSerializer.update and
    apps.doctors.views.DiplomaUploadView.post)."""
    from apps.users.models import User
    from .models import Notification
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    tpl = render_template('doctor_verification_reset', recipient_language(user))
    Notification.objects.create(
        user=user,
        type=Notification.TYPE_GENERAL,
        title=tpl['title'],
        message=tpl['body'],
    )
    _send_email(tpl['subject'], tpl['body'], user)
    _send_push(user, tpl['title'], tpl['body'])


@shared_task
def notify_blocked_period_patients(blocked_period_id):
    from apps.appointments.models import Appointment
    from apps.doctors.models import BlockedPeriod
    try:
        bp = BlockedPeriod.objects.get(pk=blocked_period_id)
    except BlockedPeriod.DoesNotExist:
        return

    affected = Appointment.objects.filter(
        doctor=bp.doctor,
        starts_at__lt=bp.ends_at,
        ends_at__gt=bp.starts_at,
        status__in=[Appointment.STATUS_PENDING, Appointment.STATUS_CONFIRMED],
    )
    if bp.workplace:
        affected = affected.filter(workplace=bp.workplace)

    now = timezone.now()
    ids = list(affected.values_list('id', flat=True))
    # Set status in bulk here — send_rescheduling_required only sends the notification.
    Appointment.objects.filter(id__in=ids).update(
        status=Appointment.STATUS_REQUIRES_RESCHEDULING,
        updated_at=now,
    )
    for appt_id in ids:
        send_rescheduling_required.delay(str(appt_id))


@shared_task
def notify_waitlist_slot_available(doctor_id):
    from apps.appointments.models import Appointment, Waitlist
    from apps.users.models import User
    from .models import Notification
    try:
        doctor = User.objects.get(pk=doctor_id)
    except User.DoesNotExist:
        return

    doctor_name = _display_name(doctor)

    # Exclude patients who already have an active appointment with this doctor.
    existing_patient_ids = set(
        Appointment.objects.filter(
            doctor=doctor,
            status__in=[Appointment.STATUS_PENDING, Appointment.STATUS_CONFIRMED],
        ).values_list('patient_id', flat=True)
    )
    waiting = (
        Waitlist.objects
        .filter(doctor=doctor)
        .exclude(patient_id__in=existing_patient_ids)
        .select_related('patient')
    )
    waiting = list(waiting)
    # Rendered per recipient — each waitlisted patient gets their own language.
    rendered = [
        render_template(
            'waitlist_slot_available', recipient_language(entry.patient),
            doctor_name=doctor_name,
        )
        for entry in waiting
    ]
    Notification.objects.bulk_create([
        Notification(
            user=entry.patient,
            type=Notification.TYPE_GENERAL,
            title=tpl['title'],
            message=tpl['body'],
        )
        for entry, tpl in zip(waiting, rendered)
    ])
    # Fan out email/push as one job per recipient so a large waitlist doesn't
    # block the worker on serial network I/O.
    push_data = {'type': 'doctor', 'doctor_id': str(doctor.id)}
    for entry, tpl in zip(waiting, rendered):
        deliver_email_and_push.delay(
            str(entry.patient_id), tpl['subject'], tpl['title'], tpl['body'], push_data
        )


@shared_task
def send_appointment_reminders():
    from apps.appointments.models import Appointment
    from .models import Notification
    now = timezone.now()
    window_end = now + datetime.timedelta(hours=1)

    upcoming = Appointment.objects.filter(
        status=Appointment.STATUS_CONFIRMED,
        starts_at__gt=now,
        starts_at__lte=window_end,
    ).select_related('doctor', 'patient').exclude(
        notifications__type=Notification.TYPE_REMINDER
    )

    for appt in upcoming:
        date_str = appt.starts_at.strftime(DATE_FORMAT)
        patient_tpl = render_template(
            'appointment_reminder_patient', recipient_language(appt.patient),
            doctor_name=_display_name(appt.doctor), date_str=date_str,
        )
        doctor_tpl = render_template(
            'appointment_reminder_doctor', recipient_language(appt.doctor),
            patient_name=_display_name(appt.patient), date_str=date_str,
        )

        Notification.objects.bulk_create([
            Notification(
                user=appt.patient,
                appointment=appt,
                type=Notification.TYPE_REMINDER,
                title=patient_tpl['title'],
                message=patient_tpl['body'],
            ),
            Notification(
                user=appt.doctor,
                appointment=appt,
                type=Notification.TYPE_REMINDER,
                title=doctor_tpl['title'],
                message=doctor_tpl['body'],
            ),
        ])
        deliver_email_and_push.delay(
            str(appt.patient_id), patient_tpl['subject'],
            patient_tpl['title'], patient_tpl['body'],
        )
        deliver_email_and_push.delay(
            str(appt.doctor_id), doctor_tpl['subject'],
            doctor_tpl['title'], doctor_tpl['body'],
        )


@shared_task
def send_appointment_completed(appointment_id):
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    tpl = render_template(
        'appointment_completed', recipient_language(appt.patient),
        doctor_name=_display_name(appt.doctor),
    )

    Notification.objects.create(
        user=appt.patient,
        appointment=appt,
        type=Notification.TYPE_GENERAL,
        title=tpl['title'],
        message=tpl['body'],
    )
    _send_email(tpl['subject'], tpl['body'], appt.patient)
    _send_push(appt.patient, tpl['title'], tpl['body'],
               data={'type': 'appointment', 'appointment_id': str(appt.id)})


@shared_task
def send_new_booking_pending(appointment_id):
    """Notify the doctor when a patient books a new appointment (pending confirmation)."""
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    tpl = render_template(
        'new_booking_pending', recipient_language(appt.doctor),
        patient_name=_display_name(appt.patient),
        date_str=appt.starts_at.strftime(DATE_FORMAT),
    )

    Notification.objects.create(
        user=appt.doctor,
        appointment=appt,
        type=Notification.TYPE_GENERAL,
        title=tpl['title'],
        message=tpl['body'],
    )
    _send_email(tpl['subject'], tpl['body'], appt.doctor)
    _send_push(appt.doctor, tpl['title'], tpl['body'],
               data={'type': 'appointment', 'appointment_id': str(appt.id)})


@shared_task
def send_prescription_issued(prescription_id):
    from apps.prescriptions.models import Prescription
    from .models import Notification
    try:
        prescription = Prescription.objects.select_related('doctor', 'patient').get(pk=prescription_id)
    except Prescription.DoesNotExist:
        return

    tpl = render_template(
        'prescription_issued', recipient_language(prescription.patient),
        doctor_name=_display_name(prescription.doctor),
    )

    Notification.objects.create(
        user=prescription.patient,
        type=Notification.TYPE_GENERAL,
        title=tpl['title'],
        message=tpl['body'],
    )
    _send_email(tpl['subject'], tpl['body'], prescription.patient)
    _send_push(prescription.patient, tpl['title'], tpl['body'],
               data={'type': 'prescription', 'prescription_id': str(prescription.id)})


@shared_task
def send_payment_received(payment_id):
    from apps.payments.models import Payment
    from .models import Notification
    try:
        payment = Payment.objects.select_related('doctor', 'patient').get(pk=payment_id)
    except Payment.DoesNotExist:
        return

    tpl = render_template(
        'payment_received', recipient_language(payment.patient),
        doctor_name=_display_name(payment.doctor),
        amount=payment.amount, currency=payment.currency,
    )

    Notification.objects.create(
        user=payment.patient,
        type=Notification.TYPE_GENERAL,
        title=tpl['title'],
        message=tpl['body'],
    )
    _send_email(tpl['subject'], tpl['body'], payment.patient)
    _send_push(payment.patient, tpl['title'], tpl['body'],
               data={'type': 'payment', 'payment_id': str(payment.id)})


@shared_task
def send_new_message(message_id):
    from apps.messaging.models import Message
    from .models import Notification
    try:
        message = Message.objects.select_related(
            'sender', 'thread__patient', 'thread__doctor'
        ).get(pk=message_id)
    except Message.DoesNotExist:
        return

    thread = message.thread
    recipient = thread.doctor if message.sender_id == thread.patient_id else thread.patient

    tpl = render_template(
        'new_message', recipient_language(recipient),
        sender_name=_display_name(message.sender),
    )

    Notification.objects.create(
        user=recipient,
        type=Notification.TYPE_GENERAL,
        title=tpl['title'],
        message=tpl['body'],
    )
    _send_email(tpl['subject'], tpl['body'], recipient)
    _send_push(recipient, tpl['title'], tpl['body'],
               data={'type': 'message', 'thread_id': str(thread.id)})


@shared_task
def auto_complete_past_appointments():
    """Marks confirmed appointments whose end time has passed as completed."""
    from apps.appointments.models import Appointment
    now = timezone.now()
    ids = list(
        Appointment.objects
        .filter(status=Appointment.STATUS_CONFIRMED, ends_at__lte=now)
        .values_list('id', flat=True)
    )
    if not ids:
        return
    Appointment.objects.filter(id__in=ids).update(
        status=Appointment.STATUS_COMPLETED,
        updated_at=now,
    )
    for appt_id in ids:
        send_appointment_completed.delay(str(appt_id))


@shared_task
def send_subscription_notification(user_id, template_key):
    """Shared by every doctor-subscription lifecycle event
    (apps.subscriptions.tasks.sweep_subscriptions and
    apps.subscriptions.service._activate_subscription) — template_key is one
    of trial_ending, trial_ended, subscription_expiring, subscription_expired,
    subscription_activated, all defined in templates.json with no
    interpolated fields, so a single generic task covers all five instead of
    one @shared_task per event."""
    from apps.users.models import User
    from .models import Notification
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    tpl = render_template(template_key, recipient_language(user))
    Notification.objects.create(
        user=user,
        type=Notification.TYPE_SUBSCRIPTION,
        title=tpl['title'],
        message=tpl['body'],
    )
    _send_email(tpl['subject'], tpl['body'], user)
    _send_push(user, tpl['title'], tpl['body'], data={'type': 'subscription'})


@shared_task
def send_hospital_notification(user_id, template_key, **kwargs):
    """Shared by every hospital-account lifecycle event (claim approved/
    rejected, a doctor requesting/being invited/confirmed/removed) —
    mirrors send_subscription_notification's one-generic-task-for-a-family-
    of-templates shape, but accepts interpolation kwargs since some of
    these templates (unlike the parameterless subscription ones) carry
    {doctor_name}/{hospital_name} placeholders — see templates.json's
    hospital_* keys."""
    from apps.users.models import User
    from .models import Notification
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    tpl = render_template(template_key, recipient_language(user), **kwargs)
    Notification.objects.create(
        user=user,
        type=Notification.TYPE_HOSPITAL,
        title=tpl['title'],
        message=tpl['body'],
    )
    _send_email(tpl['subject'], tpl['body'], user)
    _send_push(user, tpl['title'], tpl['body'], data={'type': 'hospital'})


@shared_task
def expire_stale_pending_appointments():
    """Decline appointments still pending past their start time (the doctor never
    confirmed or declined). Prevents them lingering forever and frees the slot."""
    from apps.appointments.models import Appointment
    now = timezone.now()
    ids = list(
        Appointment.objects
        .filter(status=Appointment.STATUS_PENDING, starts_at__lte=now)
        .values_list('id', flat=True)
    )
    if not ids:
        return
    Appointment.objects.filter(id__in=ids).update(
        status=Appointment.STATUS_DECLINED,
        updated_at=now,
    )
    for appt_id in ids:
        send_booking_declined.delay(str(appt_id))
