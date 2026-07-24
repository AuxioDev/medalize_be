import uuid
from datetime import date

from django.conf import settings
from django.db import models


class Medication(models.Model):
    SOURCE_MANUAL = 'manual'
    SOURCE_PRESCRIPTION = 'prescription'
    SOURCE_CHOICES = [(SOURCE_MANUAL, 'Manual'), (SOURCE_PRESCRIPTION, 'Prescription')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='medications',
    )
    name = models.CharField(max_length=200)
    dosage = models.CharField(max_length=100, blank=True)
    # pill/capsule/liquid/injection/other — validated against a client-side
    # choice list and localized on mobile, not validated/localized here.
    form = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    source = models.CharField(max_length=12, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    is_active = models.BooleanField(default=True)
    # Set only when source == SOURCE_PRESCRIPTION, via PrescriptionApplyView.
    # Added in a follow-up migration once apps.prescriptions exists, to avoid
    # a circular app dependency at apps.medications' initial migration.
    prescription_item = models.ForeignKey(
        'prescriptions.PrescriptionItem', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='medications',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.name} ({self.patient})'


class MedicationSchedule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    medication = models.ForeignKey(Medication, on_delete=models.CASCADE, related_name='schedules')
    times = models.JSONField(default=list)          # ["08:00", "20:00"] — 24h, patient-local time
    days_of_week = models.JSONField(default=list)    # [0..6] Mon-Sun; empty list = every day
    start_date = models.DateField(default=date.today)
    end_date = models.DateField(null=True, blank=True)
    # IANA name, e.g. "Asia/Baku"; blank = use the device's current timezone at display time.
    timezone = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.medication.name} schedule'


class DoseLog(models.Model):
    STATUS_TAKEN = 'taken'
    STATUS_SKIPPED = 'skipped'
    STATUS_CHOICES = [(STATUS_TAKEN, 'Taken'), (STATUS_SKIPPED, 'Skipped')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    schedule = models.ForeignKey(MedicationSchedule, on_delete=models.CASCADE, related_name='dose_logs')
    scheduled_at = models.DateTimeField()  # the specific dose moment the client is marking
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['schedule', 'scheduled_at'], name='unique_dose_per_schedule_time'),
        ]

    def __str__(self):
        return f'{self.schedule_id} @ {self.scheduled_at}: {self.status}'
