import logging
import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.i18n import CITY_CHOICES

logger = logging.getLogger(__name__)


def diploma_storage():
    """Storage for doctor diplomas. Uses Cloudinary's private/authenticated
    raw storage (handles any file type — PDF, docs, images, signed URLs
    only) when Cloudinary is configured, else the default local storage. A
    callable keeps the migration stable across environments regardless of
    which backend is active."""
    from django.conf import settings

    if getattr(settings, 'USE_CLOUDINARY', False):
        from apps.core.storage import PrivateRawCloudinaryStorage

        return PrivateRawCloudinaryStorage()
    from django.core.files.storage import default_storage

    return default_storage


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    ROLE_DOCTOR = 'doctor'
    ROLE_PATIENT = 'patient'
    ROLE_HOSPITAL = 'hospital'
    ROLE_CHOICES = [
        (ROLE_DOCTOR, 'Doctor'),
        (ROLE_PATIENT, 'Patient'),
        (ROLE_HOSPITAL, 'Hospital'),
    ]

    # Codes match the mobile AppLocale codes and the keys in the i18n JSON
    # registries (apps/notifications/i18n, apps/users/i18n).
    LANGUAGE_CHOICES = [
        ('en', 'English'),
        ('ru', 'Russian'),
        ('az', 'Azerbaijani'),
        ('tr', 'Turkish'),
        ('fr', 'French'),
        ('zh', 'Chinese'),
    ]

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._original_is_active = instance.is_active
        return instance

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, max_length=255)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    # Indexed: filtered on nearly every doctor-facing search/booking query.
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, db_index=True)
    # Preferred UI/notification language, kept in sync by the mobile app.
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, default='en')
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    # Audit trail for the explicit written consent Azerbaijan's Law on
    # Personal Data (No. 998-IIIQ) requires specifically for special-category
    # data (health data is one) — a checkbox the mobile app merely disables
    # submission on isn't provable after the fact; this timestamp is. Set
    # once at registration (RegisterSerializer), never cleared afterward.
    privacy_consent_accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


class DoctorProfile(models.Model):
    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._original_is_verified = instance.is_verified
        return instance

    SPECIALIZATION_CHOICES = [
        ('general_practice', 'General Practice'),
        ('cardiology', 'Cardiology'),
        ('dermatology', 'Dermatology'),
        ('neurology', 'Neurology'),
        ('orthopedics', 'Orthopedics'),
        ('pediatrics', 'Pediatrics'),
        ('psychiatry', 'Psychiatry'),
        ('gynecology', 'Gynecology'),
        ('urology', 'Urology'),
        ('ophthalmology', 'Ophthalmology'),
        ('ent', 'ENT (Ear, Nose & Throat)'),
        ('oncology', 'Oncology'),
        ('endocrinology', 'Endocrinology'),
        ('gastroenterology', 'Gastroenterology'),
        ('pulmonology', 'Pulmonology'),
        ('radiology', 'Radiology'),
        ('anesthesiology', 'Anesthesiology'),
        ('emergency_medicine', 'Emergency Medicine'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='doctor_profile')
    # Indexed: exact-match filtered on doctor search.
    specialization = models.CharField(
        max_length=50,
        choices=SPECIALIZATION_CHOICES,
        default='general_practice',
        blank=True,
        db_index=True,
    )
    license_number = models.CharField(max_length=100, blank=True)
    bio = models.TextField(blank=True)
    diploma_file = models.FileField(
        upload_to='diplomas/', null=True, blank=True, storage=diploma_storage
    )
    # Indexed: filtered on nearly every doctor-facing search/booking query.
    is_verified = models.BooleanField(default=False, db_index=True)
    onboarding_step = models.PositiveIntegerField(default=1)
    onboarding_complete = models.BooleanField(default=False)
    slot_duration_min = models.PositiveIntegerField(default=30)
    consultation_fee = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    # Hours before an appointment within which the patient can no longer cancel
    # or self-reschedule. Drives can_cancel/can_reschedule in the API.
    cancellation_window_hours = models.PositiveIntegerField(default=2)

    def __str__(self):
        return f'Dr. {self.user.email}'


class PatientProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='patient_profile')
    date_of_birth = models.DateField(null=True, blank=True)
    blood_type = models.CharField(max_length=5, blank=True)
    address = models.TextField(blank=True)
    # Canonical key into apps.core.i18n.locations.json, not free text.
    city = models.CharField(max_length=64, choices=CITY_CHOICES, blank=True)
    region = models.CharField(max_length=64, blank=True)
    allergies = models.TextField(blank=True)
    chronic_conditions = models.TextField(blank=True)
    medications = models.TextField(blank=True)

    def __str__(self):
        return f'Patient: {self.user.email}'


class SocialAccount(models.Model):
    PROVIDER_GOOGLE = 'google'
    PROVIDER_APPLE = 'apple'
    PROVIDER_CHOICES = [
        (PROVIDER_GOOGLE, 'Google'),
        (PROVIDER_APPLE, 'Apple'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='social_accounts')
    provider = models.CharField(max_length=10, choices=PROVIDER_CHOICES)
    provider_uid = models.CharField(max_length=255)
    email = models.EmailField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('provider', 'provider_uid')

    def __str__(self):
        return f'{self.provider}:{self.user.email}'


class UserDevice(models.Model):
    PLATFORM_IOS = 'ios'
    PLATFORM_ANDROID = 'android'
    PLATFORM_CHOICES = [
        (PLATFORM_IOS, 'iOS'),
        (PLATFORM_ANDROID, 'Android'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='devices')
    device_id = models.CharField(max_length=255)
    device_name = models.CharField(max_length=255, blank=True)
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES, blank=True)
    # jti of the most recent refresh token issued to this device — lets us
    # blacklist exactly this device's session via the token_blacklist app.
    jti = models.CharField(max_length=64, blank=True)
    # jti this device's token was rotated away from, if any. Lets refresh-
    # token-reuse detection (apps/users/views.py::_detect_refresh_reuse)
    # confirm a presented, already-blacklisted jti was specifically
    # rotated-away-from-legitimately (real reuse/theft) rather than
    # blacklisted for an unrelated reason (e.g. explicit device revocation),
    # which would otherwise look identical from blacklist state alone.
    previous_jti = models.CharField(max_length=64, blank=True, db_index=True)
    last_seen_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'device_id')
        ordering = ['-last_seen_at']

    def __str__(self):
        return f'{self.user.email} — {self.device_name or self.device_id}'


class PasswordResetOTP(models.Model):
    # Number of wrong-code guesses after which the OTP is retired, forcing the
    # user to request a fresh code. Caps per-account brute force independently
    # of the per-IP throttle.
    MAX_ATTEMPTS = 5

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_reset_otps')
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'used', 'expires_at']),
        ]


class EmailChangeRequest(models.Model):
    # Number of wrong-code guesses after which the request is retired, forcing
    # the user to request a fresh code. Caps per-account brute force
    # independently of the per-user throttle.
    MAX_ATTEMPTS = 5

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='email_change_requests')
    new_email = models.EmailField(max_length=255)
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'used', 'expires_at']),
        ]


@receiver(post_save, sender=User)
def create_role_profile(sender, instance, created, **kwargs):
    if not created:
        return
    if instance.role == User.ROLE_DOCTOR:
        DoctorProfile.objects.create(user=instance)
    elif instance.role == User.ROLE_PATIENT:
        PatientProfile.objects.create(user=instance)
    elif instance.role == User.ROLE_HOSPITAL:
        # Deliberately no profile created here, unlike the two branches
        # above. A hospital account's "profile" IS a apps.hospitals.models.
        # Hospital row (claimed or newly created), which needs registration-
        # time input (an existing hospital_id to claim, or a name+city to
        # create) that this zero-argument signal has no way to supply.
        # apps.users.serializers.RegisterSerializer.create() calls
        # apps.hospitals.services.claim_or_create_hospital() explicitly,
        # inside the same transaction as this User row, right after this
        # signal fires. Do not "fix" this branch by adding a bare
        # HospitalProfile.objects.create(user=instance) call — there is no
        # such model; the registry entry IS the profile (see apps.hospitals.
        # models.Hospital's docstring).
        pass


def cancel_future_appointments_for_doctor(doctor):
    """Auto-cancel this doctor's future pending/confirmed appointments and
    notify the affected patients. Called whenever a doctor becomes unbookable
    (deactivated or loses verification) so patients aren't left holding a
    booking nobody can act on. Reuses the existing booking-cancelled
    notification path rather than a bespoke message."""
    from django.core.cache import cache
    from django.utils import timezone
    from apps.appointments.models import Appointment

    appointments = list(
        Appointment.objects.filter(
            doctor=doctor,
            status__in=[Appointment.STATUS_PENDING, Appointment.STATUS_CONFIRMED],
            starts_at__gt=timezone.now(),
        )
    )
    if not appointments:
        return

    Appointment.objects.filter(pk__in=[a.pk for a in appointments]).update(
        status=Appointment.STATUS_CANCELLED, updated_at=timezone.now()
    )

    from apps.notifications.tasks import send_booking_cancelled
    for appt in appointments:
        cache.delete(f'slots:{appt.doctor_id}:{appt.workplace_id}:{appt.starts_at.date()}')
        try:
            send_booking_cancelled.delay(str(appt.id))
        except Exception:
            logger.exception(
                'Failed to enqueue cancellation notification for appointment %s', appt.id
            )


@receiver(post_save, sender=User)
def cancel_appointments_on_deactivation(sender, instance, created, **kwargs):
    if created or instance.role != User.ROLE_DOCTOR:
        return
    original = getattr(instance, '_original_is_active', None)
    if original is True and instance.is_active is False:
        cancel_future_appointments_for_doctor(instance)


@receiver(post_save, sender=DoctorProfile)
def notify_doctor_verified(sender, instance, created, **kwargs):
    if created:
        return
    original = getattr(instance, '_original_is_verified', None)
    if original is False and instance.is_verified is True:
        try:
            from apps.notifications.tasks import send_doctor_verified
            send_doctor_verified.delay(instance.user_id)
        except Exception:
            logger.exception('Failed to dispatch verification notification for doctor %s', instance.user_id)
    elif original is True and instance.is_verified is False:
        cancel_future_appointments_for_doctor(instance.user)
