import uuid
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


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
    ROLE_CHOICES = [
        (ROLE_DOCTOR, 'Doctor'),
        (ROLE_PATIENT, 'Patient'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, max_length=255)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


class DoctorProfile(models.Model):
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
    specialization = models.CharField(
        max_length=50,
        choices=SPECIALIZATION_CHOICES,
        default='general_practice',
        blank=True,
    )
    license_number = models.CharField(max_length=100, blank=True)
    bio = models.TextField(blank=True)
    diploma_file = models.FileField(upload_to='diplomas/', null=True, blank=True)
    is_verified = models.BooleanField(default=False)
    onboarding_step = models.PositiveIntegerField(default=1)
    onboarding_complete = models.BooleanField(default=False)
    slot_duration_min = models.PositiveIntegerField(default=30)

    def __str__(self):
        return f'Dr. {self.user.email}'


class PatientProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='patient_profile')
    date_of_birth = models.DateField(null=True, blank=True)
    blood_type = models.CharField(max_length=5, blank=True)
    address = models.TextField(blank=True)

    def __str__(self):
        return f'Patient: {self.user.email}'


class PasswordResetOTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_reset_otps')
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']


@receiver(post_save, sender=User)
def create_role_profile(sender, instance, created, **kwargs):
    if not created:
        return
    if instance.role == User.ROLE_DOCTOR:
        DoctorProfile.objects.create(user=instance)
    elif instance.role == User.ROLE_PATIENT:
        PatientProfile.objects.create(user=instance)
