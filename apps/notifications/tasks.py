from celery import shared_task
from django.utils import timezone


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

    Notification.objects.create(
        user=appt.patient,
        appointment=appt,
        type=Notification.TYPE_CONFIRMED,
        title='Appointment Confirmed',
        message=f'Your appointment with Dr. {doctor_name} on {date_str} has been confirmed.',
    )


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

    Notification.objects.bulk_create([
        Notification(
            user=appt.patient,
            appointment=appt,
            type=Notification.TYPE_CANCELLED,
            title='Appointment Cancelled',
            message=f'Your appointment with Dr. {doctor_name} on {date_str} has been cancelled.',
        ),
        Notification(
            user=appt.doctor,
            appointment=appt,
            type=Notification.TYPE_CANCELLED,
            title='Appointment Cancelled',
            message=f'The appointment with {patient_name} on {date_str} has been cancelled.',
        ),
    ])


@shared_task
def send_rescheduling_required(appointment_id):
    from apps.appointments.models import Appointment
    from .models import Notification
    try:
        appt = Appointment.objects.select_related('doctor', 'patient').get(pk=appointment_id)
    except Appointment.DoesNotExist:
        return

    appt.status = Appointment.STATUS_REQUIRES_RESCHEDULING
    appt.save(update_fields=['status', 'updated_at'])

    doctor_name = f'{appt.doctor.first_name} {appt.doctor.last_name}'.strip() or appt.doctor.email
    date_str = appt.starts_at.strftime('%d %b %Y at %H:%M')

    Notification.objects.create(
        user=appt.patient,
        appointment=appt,
        type=Notification.TYPE_RESCHEDULING,
        title='Appointment Rescheduling Required',
        message=(
            f'Your appointment with Dr. {doctor_name} on {date_str} '
            'needs to be rescheduled. Please book a new time slot.'
        ),
    )


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

    for appt in affected:
        send_rescheduling_required.delay(str(appt.id))


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
        msg = f'Reminder: appointment with Dr. {doctor_name} at {date_str}.'

        Notification.objects.bulk_create([
            Notification(
                user=appt.patient,
                appointment=appt,
                type=Notification.TYPE_REMINDER,
                title='Appointment Reminder',
                message=msg,
            ),
            Notification(
                user=appt.doctor,
                appointment=appt,
                type=Notification.TYPE_REMINDER,
                title='Appointment Reminder',
                message=f'Reminder: appointment with {appt.patient.first_name} {appt.patient.last_name} at {date_str}.',
            ),
        ])
