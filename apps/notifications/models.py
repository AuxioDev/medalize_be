import uuid

from django.conf import settings
from django.db import models


class FCMToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='fcm_tokens',
    )
    token = models.CharField(max_length=500, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.user.email} — {self.token[:20]}...'


class Notification(models.Model):
    TYPE_REMINDER = 'appointment_reminder'
    TYPE_CONFIRMED = 'booking_confirmed'
    TYPE_CANCELLED = 'booking_cancelled'
    TYPE_RESCHEDULING = 'rescheduling_required'
    TYPE_GENERAL = 'general'
    TYPE_CHOICES = [
        (TYPE_REMINDER, 'Appointment Reminder'),
        (TYPE_CONFIRMED, 'Booking Confirmed'),
        (TYPE_CANCELLED, 'Booking Cancelled'),
        (TYPE_RESCHEDULING, 'Rescheduling Required'),
        (TYPE_GENERAL, 'General'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    appointment = models.ForeignKey(
        'appointments.Appointment',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
    )
    type = models.CharField(max_length=50, choices=TYPE_CHOICES, default=TYPE_GENERAL)
    title = models.CharField(max_length=255)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return f'{self.user.email} — {self.title}'
