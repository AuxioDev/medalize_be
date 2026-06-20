import uuid

from django.conf import settings
from django.db import models


class Appointment(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_DECLINED = 'declined'
    STATUS_CANCELLED = 'cancelled'
    STATUS_COMPLETED = 'completed'
    STATUS_REQUIRES_RESCHEDULING = 'requires_rescheduling'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_CONFIRMED, 'Confirmed'),
        (STATUS_DECLINED, 'Declined'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_COMPLETED, 'Completed'),
        (STATUS_REQUIRES_RESCHEDULING, 'Requires Rescheduling'),
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
    reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['doctor', 'starts_at']),
            models.Index(fields=['patient', 'status']),
        ]
        ordering = ['-starts_at']

    def __str__(self):
        return f'{self.patient} → Dr.{self.doctor} @ {self.starts_at:%Y-%m-%d %H:%M}'
