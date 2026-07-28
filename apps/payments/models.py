import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Payment(models.Model):
    """One payment per appointment — same OneToOneField + denormalized-owner
    pattern already used twice in this codebase: apps.appointments.models.
    Review and apps.prescriptions.models.Prescription (doctor/patient copied
    onto the row at creation time so callers don't need to join through
    appointment; `try/except Payment.DoesNotExist` is the idiom for "does one
    already exist").

    `amount` is a SNAPSHOT of doctor.doctor_profile.consultation_fee taken at
    payment-creation time, not a live reference — the price must not change
    retroactively if the doctor edits their fee after the patient has already
    started (or finished) paying.
    """

    STATUS_PENDING = 'pending'
    STATUS_PAID = 'paid'
    STATUS_FAILED = 'failed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'), (STATUS_PAID, 'Paid'),
        (STATUS_FAILED, 'Failed'), (STATUS_CANCELLED, 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    appointment = models.OneToOneField(
        'appointments.Appointment', on_delete=models.CASCADE, related_name='payment',
    )
    # Denormalized from appointment at creation time (same as Review/Prescription).
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payments',
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='payments_received',
    )
    # Denormalized from appointment.dependent at creation time, same as
    # doctor/patient above — no new UI/input here, get_or_create_payment()
    # just copies it from the appointment on first creation only (it never
    # changes after that, like the rest of this row's identity). Added in a
    # follow-up migration once apps.family exists (see
    # apps.appointments.models.Appointment.dependent for the full rationale).
    dependent = models.ForeignKey(
        'family.Dependent', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='payments',
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='AZN')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    provider = models.CharField(max_length=20, default='payriff')
    # Indexed: the webhook and check_status re-verification both look a
    # Payment up by this field, never by our own id.
    provider_order_id = models.CharField(max_length=255, blank=True, db_index=True)
    provider_session_id = models.CharField(max_length=255, blank=True)
    payment_url = models.URLField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # Excludes blank: many rows legitimately carry '' (any row not
            # yet round-tripped through get_or_create_payment). Without
            # `condition`, a plain unique index would reject the second such
            # row outright.
            models.UniqueConstraint(
                fields=['provider_order_id'],
                condition=~Q(provider_order_id=''),
                name='payment_unique_provider_order_id',
            ),
        ]

    def __str__(self):
        return f'{self.patient} → Dr.{self.doctor}: {self.amount} {self.currency} ({self.status})'
