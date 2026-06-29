import logging
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

# Hours before an appointment within which the patient can no longer cancel or
# self-reschedule it. Single source of truth for the serializer (can_cancel /
# can_reschedule flags the client reads) and the cancel/reschedule views.
CANCELLATION_WINDOW_HOURS = 2


class Appointment(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_DECLINED = 'declined'
    STATUS_CANCELLED = 'cancelled'
    STATUS_COMPLETED = 'completed'
    STATUS_REQUIRES_RESCHEDULING = 'requires_rescheduling'
    STATUS_NO_SHOW = 'no_show'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_CONFIRMED, 'Confirmed'),
        (STATUS_DECLINED, 'Declined'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_REQUIRES_RESCHEDULING, 'Requires Rescheduling'),
        (STATUS_NO_SHOW, 'No-show'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='doctor_appointments',
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='patient_appointments',
    )
    workplace = models.ForeignKey(
        'doctors.Workplace',
        on_delete=models.PROTECT,
        related_name='appointments',
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PENDING)

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({'ends_at': 'End time must be after start time.'})
    reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['doctor', 'starts_at']),
            models.Index(fields=['patient', 'status']),
            models.Index(fields=['status', 'ends_at'], name='appt_status_ends_idx'),
        ]
        ordering = ['-starts_at']

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._original_status = instance.status
        return instance

    def __str__(self):
        return f'{self.patient} → Dr.{self.doctor} @ {self.starts_at:%Y-%m-%d %H:%M}'


@receiver(post_save, sender='appointments.Appointment')
def notify_waitlist_on_cancellation(sender, instance, created, **kwargs):
    if created:
        return
    original = getattr(instance, '_original_status', None)
    freed = {Appointment.STATUS_CANCELLED, Appointment.STATUS_DECLINED}
    if original not in (None, *freed) and instance.status in freed:
        try:
            from apps.notifications.tasks import notify_waitlist_slot_available
            notify_waitlist_slot_available.delay(str(instance.doctor_id))
        except Exception:
            logger.exception('Failed to dispatch waitlist notification for appointment %s', instance.pk)


class Waitlist(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='waitlist_entries',
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='waitlist',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [['patient', 'doctor']]
        ordering = ['created_at']

    def __str__(self):
        return f'{self.patient} waiting for Dr.{self.doctor}'


class Review(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    appointment = models.OneToOneField(
        Appointment,
        on_delete=models.CASCADE,
        related_name='review',
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='doctor_reviews',
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='patient_reviews',
    )
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['doctor'], name='appointment_doctor__dd3270_idx'),
        ]

    def __str__(self):
        return f'{self.patient} → Dr.{self.doctor}: {self.rating}★'
