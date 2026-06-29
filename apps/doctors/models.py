import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Workplace(models.Model):
    TYPE_CHOICES = [
        ('clinic', 'Clinic'),
        ('hospital', 'Hospital'),
        ('private', 'Private Practice'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='workplaces',
    )
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=500)
    city = models.CharField(max_length=100)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ['-is_primary', 'name']

    def __str__(self):
        return f'{self.name} ({self.city})'


class WorkingHours(models.Model):
    WEEKDAY_CHOICES = [
        (0, 'Monday'),
        (1, 'Tuesday'),
        (2, 'Wednesday'),
        (3, 'Thursday'),
        (4, 'Friday'),
        (5, 'Saturday'),
        (6, 'Sunday'),
    ]

    workplace = models.ForeignKey(
        Workplace,
        on_delete=models.CASCADE,
        related_name='working_hours',
    )
    weekday = models.PositiveSmallIntegerField(
        choices=WEEKDAY_CHOICES,
        validators=[MinValueValidator(0), MaxValueValidator(6)],
    )
    start_time = models.TimeField(default='09:00')
    end_time = models.TimeField(default='17:00')
    is_active = models.BooleanField(default=False)

    class Meta:
        unique_together = ('workplace', 'weekday')
        ordering = ['weekday']

    def __str__(self):
        return f'{self.workplace.name} – {self.get_weekday_display()}'


class BlockedPeriod(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='blocked_periods',
    )
    workplace = models.ForeignKey(
        Workplace,
        on_delete=models.CASCADE,
        related_name='blocked_periods',
        null=True,
        blank=True,
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.TextField(blank=True)

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({'ends_at': 'End time must be after start time.'})

    class Meta:
        ordering = ['starts_at']

    def __str__(self):
        return f'{self.doctor.email}: {self.starts_at:%Y-%m-%d} – {self.ends_at:%Y-%m-%d}'
