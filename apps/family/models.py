import uuid

from django.conf import settings
from django.db import models


class Dependent(models.Model):
    """A managed profile for a member of the account owner's family (child,
    elderly parent, ...) who does not have their own login. Every clinical
    app (appointments/medications/records/prescriptions/payments) keeps its
    existing `patient` FK exactly as-is — that field remains the real
    authorized `User` for ownership/payment/notification purposes — and adds
    a separate nullable `dependent` FK alongside it. `patient` is always the
    account owner (contact/payer); `dependent` (if set) is who the record is
    actually *for*. See the appointments/medications/records/prescriptions/
    payments apps for where `dependent` is threaded through.

    `blood_type`/`allergies`/`chronic_conditions`/`medications` deliberately
    mirror `apps.users.models.PatientProfile` — same fields, same meaning,
    just for an unauthenticated family member instead of the user themself.
    """

    RELATIONSHIP_CHILD = 'child'
    RELATIONSHIP_SPOUSE = 'spouse'
    RELATIONSHIP_PARENT = 'parent'
    RELATIONSHIP_SIBLING = 'sibling'
    RELATIONSHIP_OTHER = 'other'
    RELATIONSHIP_CHOICES = [
        (RELATIONSHIP_CHILD, 'Child'),
        (RELATIONSHIP_SPOUSE, 'Spouse'),
        (RELATIONSHIP_PARENT, 'Parent'),
        (RELATIONSHIP_SIBLING, 'Sibling'),
        (RELATIONSHIP_OTHER, 'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    managed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='dependents',
    )
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150, blank=True)
    relationship = models.CharField(max_length=10, choices=RELATIONSHIP_CHOICES)
    date_of_birth = models.DateField(null=True, blank=True)
    blood_type = models.CharField(max_length=5, blank=True)
    allergies = models.TextField(blank=True)
    chronic_conditions = models.TextField(blank=True)
    medications = models.TextField(blank=True)
    # Soft delete (same pattern as Medication.is_active) — deleting a profile
    # must not orphan historical appointments/medications/records that
    # already reference it.
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['first_name', 'last_name']

    def __str__(self):
        return f'{self.first_name} {self.last_name} (managed by {self.managed_by})'.strip()
