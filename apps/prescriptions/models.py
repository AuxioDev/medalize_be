import uuid

from django.conf import settings
from django.db import models


class Prescription(models.Model):
    """One prescription per completed appointment — same pattern as
    apps.appointments.models.Review (OneToOneField, created only once the
    appointment is completed, guarded by a try/except DoesNotExist in the
    creating serializer)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    appointment = models.OneToOneField(
        'appointments.Appointment', on_delete=models.CASCADE, related_name='prescription',
    )
    # Denormalized from appointment at creation time (same as Review) so the
    # patient's prescription list doesn't need a join through appointment.
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='prescriptions_issued',
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='prescriptions_received',
    )
    # Denormalized from appointment.dependent at creation time, same as
    # doctor/patient above — no new UI/input here, PrescriptionCreateSerializer.
    # create() just copies it from the appointment. Added in a follow-up
    # migration once apps.family exists (see
    # apps.appointments.models.Appointment.dependent for the full rationale).
    dependent = models.ForeignKey(
        'family.Dependent', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='prescriptions',
    )
    notes = models.TextField(blank=True)
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-issued_at']

    def __str__(self):
        return f'Rx for {self.patient} from Dr.{self.doctor} ({self.issued_at:%Y-%m-%d})'


class PrescriptionItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE, related_name='items')
    drug_name = models.CharField(max_length=200)
    dosage = models.CharField(max_length=100, blank=True)
    frequency = models.CharField(max_length=100, blank=True)   # free text, e.g. "twice a day"
    duration = models.CharField(max_length=100, blank=True)    # e.g. "7 days"; blank = ongoing
    instructions = models.TextField(blank=True)

    def __str__(self):
        return self.drug_name
