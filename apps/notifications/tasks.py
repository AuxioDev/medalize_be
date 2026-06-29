import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)


def _send_email(subject, message, recipient_email):
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient_email],
            fail_silently=False,
        )
    except Exception:
        logger.exception('Failed to send email to %s', recipient_email)


def _send_push(user, title, body, data=None):
    """Send FCM push to all registered tokens for user. No-op if Firebase not configured."""
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
def send_booking_confirmed(appointment_id):
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    doctor_name = f'{appt.doctor.first_name} {appt.doctor.last_name}'.strip() or appt.doctor.email
    date_str = appt.starts_at.strftime('%d %b %Y at %H:%M')
    msg = f'Your appointment with Dr. {doctor_name} on {date_str} has been confirmed.'

    Notification.objects.create(
        user=appt.patient,
        appointment=appt,
        type=Notification.TYPE_CONFIRMED,
        title='Appointment Confirmed',
        message=msg,
    )
    _send_email('Appointment Confirmed — Medalize', msg, appt.patient.email)
    _send_push(appt.patient, 'Appointment Confirmed', msg,
               data={'type': 'appointment', 'appointment_id': str(appt.id)})


@shared_task
def send_booking_declined(appointment_id):
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    doctor_name = f'Dr. {appt.doctor.first_name} {appt.doctor.last_name}'.strip() or appt.doctor.email
    date_str = appt.starts_at.strftime('%d %b %Y at %H:%M')
    msg = f'Your appointment request with {doctor_name} on {date_str} has been declined.'

    Notification.objects.create(
        user=appt.patient,
        appointment=appt,
        type=Notification.TYPE_CANCELLED,
        title='Appointment Declined',
        message=msg,
    )
    _send_email('Appointment Declined — Medalize', msg, appt.patient.email)
    _send_push(appt.patient, 'Appointment Declined', msg,
               data={'type': 'appointment', 'appointment_id': str(appt.id)})


@shared_task
def send_booking_cancelled(appointment_id):
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    doctor_name = f'{appt.doctor.first_name} {appt.doctor.last_name}'.strip() or appt.doctor.email
    patient_name = f'{appt.patient.first_name} {appt.patient.last_name}'.strip() or appt.patient.email
    date_str = appt.starts_at.strftime('%d %b %Y at %H:%M')

    patient_msg = f'Your appointment with Dr. {doctor_name} on {date_str} has been cancelled.'
    doctor_msg = f'The appointment with {patient_name} on {date_str} has been cancelled.'

    Notification.objects.bulk_create([
        Notification(
            user=appt.patient,
            appointment=appt,
            type=Notification.TYPE_CANCELLED,
            title='Appointment Cancelled',
            message=patient_msg,
        ),
        Notification(
            user=appt.doctor,
            appointment=appt,
            type=Notification.TYPE_CANCELLED,
            title='Appointment Cancelled',
            message=doctor_msg,
        ),
    ])
    _send_email('Appointment Cancelled — Medalize', patient_msg, appt.patient.email)
    _send_email('Appointment Cancelled — Medalize', doctor_msg, appt.doctor.email)
    push_data = {'type': 'appointment', 'appointment_id': str(appt.id)}
    _send_push(appt.patient, 'Appointment Cancelled', patient_msg, data=push_data)
    _send_push(appt.doctor, 'Appointment Cancelled', doctor_msg, data=push_data)


@shared_task
def send_rescheduling_required(appointment_id):
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    doctor_name = f'{appt.doctor.first_name} {appt.doctor.last_name}'.strip() or appt.doctor.email
    date_str = appt.starts_at.strftime('%d %b %Y at %H:%M')
    msg = (
        f'Your appointment with Dr. {doctor_name} on {date_str} '
        'needs to be rescheduled. Please book a new time slot.'
    )

    Notification.objects.create(
        user=appt.patient,
        appointment=appt,
        type=Notification.TYPE_RESCHEDULING,
        title='Appointment Rescheduling Required',
        message=msg,
    )
    _send_email('Rescheduling Required — Medalize', msg, appt.patient.email)
    _send_push(appt.patient, 'Rescheduling Required', msg,
               data={'type': 'appointment', 'appointment_id': str(appt.id)})


@shared_task
def send_appointment_rescheduled(appointment_id):
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    patient_name = f'{appt.patient.first_name} {appt.patient.last_name}'.strip() or appt.patient.email
    date_str = appt.starts_at.strftime('%d %b %Y at %H:%M')
    doctor_msg = f'{patient_name} rescheduled their appointment to {date_str}.'

    Notification.objects.create(
        user=appt.doctor,
        appointment=appt,
        type=Notification.TYPE_GENERAL,
        title='Appointment Rescheduled',
        message=doctor_msg,
    )
    _send_email('Appointment Rescheduled — Medalize', doctor_msg, appt.doctor.email)
    _send_push(appt.doctor, 'Appointment Rescheduled', doctor_msg,
               data={'type': 'appointment', 'appointment_id': str(appt.id)})


@shared_task
def send_doctor_verified(user_id):
    from apps.users.models import User
    from .models import Notification
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    msg = 'Your account has been verified. You can now receive appointments from patients.'
    Notification.objects.create(
        user=user,
        type=Notification.TYPE_GENERAL,
        title='Account Verified',
        message=msg,
    )
    _send_email('Your Medalize Account is Verified', msg, user.email)
    _send_push(user, 'Account Verified', msg)


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
    from apps.appointments.models import Waitlist
    from apps.users.models import User
    from .models import Notification
    try:
        doctor = User.objects.get(pk=doctor_id)
    except User.DoesNotExist:
        return

    doctor_name = f'Dr. {doctor.first_name} {doctor.last_name}'.strip() or f'Dr. {doctor.email}'
    msg = f'A slot has opened up with {doctor_name}. Book your appointment now.'
    title = 'New Slot Available'

    waiting = Waitlist.objects.filter(doctor=doctor).select_related('patient')
    Notification.objects.bulk_create([
        Notification(
            user=entry.patient,
            type=Notification.TYPE_GENERAL,
            title=title,
            message=msg,
        )
        for entry in waiting
    ])
    for entry in waiting:
        _send_email('Slot Available — Medalize', msg, entry.patient.email)
        _send_push(entry.patient, title, msg,
                   data={'type': 'doctor', 'doctor_id': str(doctor.id)})


@shared_task
def send_appointment_reminders():
    from apps.appointments.models import Appointment
    from .models import Notification
    now = timezone.now()
    window_end = now + timezone.timedelta(hours=1)

    upcoming = Appointment.objects.filter(
        status=Appointment.STATUS_CONFIRMED,
        starts_at__gt=now,
        starts_at__lte=window_end,
    ).select_related('doctor', 'patient').exclude(
        notifications__type=Notification.TYPE_REMINDER
    )

    for appt in upcoming:
        doctor_name = f'{appt.doctor.first_name} {appt.doctor.last_name}'.strip() or appt.doctor.email
        date_str = appt.starts_at.strftime('%d %b %Y at %H:%M')
        patient_msg = f'Reminder: appointment with Dr. {doctor_name} at {date_str}.'
        doctor_msg = f'Reminder: appointment with {appt.patient.first_name} {appt.patient.last_name} at {date_str}.'

        Notification.objects.bulk_create([
            Notification(
                user=appt.patient,
                appointment=appt,
                type=Notification.TYPE_REMINDER,
                title='Appointment Reminder',
                message=patient_msg,
            ),
            Notification(
                user=appt.doctor,
                appointment=appt,
                type=Notification.TYPE_REMINDER,
                title='Appointment Reminder',
                message=doctor_msg,
            ),
        ])
        _send_email('Appointment Reminder — Medalize', patient_msg, appt.patient.email)
        _send_email('Appointment Reminder — Medalize', doctor_msg, appt.doctor.email)
        _send_push(appt.patient, 'Appointment Reminder', patient_msg)
        _send_push(appt.doctor, 'Appointment Reminder', doctor_msg)


@shared_task
def send_appointment_completed(appointment_id):
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    doctor_name = f'Dr. {appt.doctor.first_name} {appt.doctor.last_name}'.strip() or appt.doctor.email
    msg = f'Your appointment with {doctor_name} is complete. Leave a review to help others!'

    Notification.objects.create(
        user=appt.patient,
        appointment=appt,
        type=Notification.TYPE_GENERAL,
        title='Appointment Complete',
        message=msg,
    )
    _send_email('Appointment Complete — Medalize', msg, appt.patient.email)
    _send_push(appt.patient, 'Appointment Complete', msg,
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

    patient_name = f'{appt.patient.first_name} {appt.patient.last_name}'.strip() or appt.patient.email
    date_str = appt.starts_at.strftime('%d %b %Y at %H:%M')
    msg = f'{patient_name} has requested an appointment on {date_str}. Please confirm or decline.'

    Notification.objects.create(
        user=appt.doctor,
        appointment=appt,
        type=Notification.TYPE_GENERAL,
        title='New Appointment Request',
        message=msg,
    )
    _send_email('New Appointment Request — Medalize', msg, appt.doctor.email)
    _send_push(appt.doctor, 'New Appointment Request', msg,
               data={'type': 'appointment', 'appointment_id': str(appt.id)})


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
