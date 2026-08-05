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

    Coercive-control safeguard for adult dependents: a dependent has no
    login of its own, which is fine for a child but is a real privacy risk
    for a family member who is themselves a competent adult (e.g. a spouse
    or an adult sibling) — without it, an account holder could add and
    fully manage another adult's medical history (bookings, records,
    prescriptions) with zero notice or consent. `date_of_birth` stays
    `null=True` at the DB level (existing rows created before this
    requirement existed are intentionally left alone — see
    `DependentCreateSerializer`, which enforces it as *required* for any
    new/edited dependent going forward, rather than a disruptive backfill
    migration), but is the single source of truth for the 18-year
    threshold that gates the `contact_*`/`consent_*` fields below. See
    `apps.family.services.issue_consent_notice`/
    `apps.family.views.DependentConsentRejectView` for the notify-then-
    object-if-you-want flow this enables: the account holder is never
    blocked from adding an adult they actively care for (e.g. an elderly
    parent), but that adult is emailed that they were added and given a
    no-login link to disconnect the profile if they did not agree to it.
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
    # Contact channel for an adult (18+) dependent's own consent notice —
    # same field shapes as apps.users.models.User.email/phone and
    # apps.hospitals.models.Hospital.phone for consistency. Only
    # contact_email is actually required/used to deliver the notice (see
    # DependentCreateSerializer.validate) — there is no SMS/phone-delivery
    # infrastructure anywhere in this codebase, only email (apps.
    # notifications.tasks.send_transactional_email), so a phone number
    # alone cannot satisfy the "positively informed" requirement. Kept
    # anyway as an optional, freeform supplementary field for the account
    # holder's own records, matching Hospital.phone (also unvalidated
    # free text).
    contact_email = models.EmailField(max_length=255, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    # Consent-notice state for an adult dependent — mirrors
    # apps.users.models.PasswordResetOTP's shape (hashed token + expiry),
    # adapted for a one-click no-login URL token instead of a typed code.
    # Set/cleared by apps.family.services.issue_consent_notice /
    # apply_consent_objection; never written to directly elsewhere.
    consent_notice_sent_at = models.DateTimeField(null=True, blank=True)
    consent_token_hash = models.CharField(max_length=128, blank=True)
    consent_token_expires_at = models.DateTimeField(null=True, blank=True)
    # Set when the adult dependent uses their no-login link to object.
    # is_active is flipped to False in the same action (see
    # apps.family.services.apply_consent_objection) — reusing the existing
    # soft-delete mechanism rather than a second delete path — but this
    # timestamp is kept alongside it so an objection is distinguishable
    # from an ordinary account-holder-initiated delete after the fact.
    consent_objected_at = models.DateTimeField(null=True, blank=True)
    # Soft delete (same pattern as Medication.is_active) — deleting a profile
    # must not orphan historical appointments/medications/records that
    # already reference it. Also the mechanism a successful consent
    # objection uses (see consent_objected_at above).
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['first_name', 'last_name']

    def __str__(self):
        return f'{self.first_name} {self.last_name} (managed by {self.managed_by})'.strip()

    @property
    def age(self):
        """Whole years old as of today; `None` when `date_of_birth` isn't
        set (still legal for a pre-existing dependent — see the class
        docstring). Delegates to apps.family.services.dependent_age (local
        import to avoid a models<->services import cycle, same idiom
        apps.users.models uses for its own cross-app signal handlers)."""
        from .services import dependent_age
        return dependent_age(self.date_of_birth)

    @property
    def is_adult(self):
        """Whether this dependent's computed age is 18 or older. `False`
        (not unknown) when `date_of_birth` is unset — a dependent with no
        DOB on file never triggers the consent-notice flow, it's simply
        excluded until the account holder supplies one (see
        DependentCreateSerializer)."""
        from .services import is_adult
        return is_adult(self.date_of_birth)
