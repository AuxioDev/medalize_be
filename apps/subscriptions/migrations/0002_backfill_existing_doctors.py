from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def backfill_doctor_subscriptions(apps, schema_editor):
    """The create_doctor_subscription/start_trial_on_verification signals
    (apps/subscriptions/signals.py) only fire for future User/DoctorProfile
    saves — doctors that already existed before this app was added need a
    subscription row created here instead. Already-verified doctors are
    granted the full 7-day trial starting now, exactly as if they were being
    verified today, rather than being retroactively penalized for having
    signed up before subscriptions existed."""
    User = apps.get_model('users', 'User')
    DoctorSubscription = apps.get_model('subscriptions', 'DoctorSubscription')
    now = timezone.now()

    existing_ids = set(DoctorSubscription.objects.values_list('user_id', flat=True))
    doctors = User.objects.filter(role='doctor').select_related('doctor_profile')

    to_create = []
    for doctor in doctors:
        if doctor.id in existing_ids:
            continue
        try:
            is_verified = doctor.doctor_profile.is_verified
        except Exception:
            is_verified = False
        if is_verified:
            to_create.append(DoctorSubscription(
                user_id=doctor.id,
                status='trialing',
                trial_ends_at=now + timedelta(days=7),
            ))
        else:
            to_create.append(DoctorSubscription(user_id=doctor.id, status='pending'))

    DoctorSubscription.objects.bulk_create(to_create)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0001_initial'),
        ('users', '0018_patientprofile_city_patientprofile_region'),
    ]

    operations = [
        migrations.RunPython(backfill_doctor_subscriptions, noop_reverse),
    ]
