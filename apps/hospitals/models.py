import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import Q

from apps.core.i18n import CITY_CHOICES, city_region

from .matching import normalized_key


class Hospital(models.Model):
    """A hospital/clinic — simultaneously a registry entry (selectable from
    the doctor's workplace-hospital picker) and, once claimed, a billable
    account that can log in and run a dashboard.

    Two independent status axes rather than one combined enum — see the
    plan this was built from (docs discussion, not in-repo): a *registry*
    entry can be well-formed and admin-confirmed with no account behind it
    at all, and a *claim* can be pending review on either a brand-new or a
    long-since-confirmed registry entry. Collapsing them into one enum would
    mean enumerating their cross product every time a new state is added.
    """

    # --- Axis 1: the registry entry's own lifecycle -----------------------
    STATUS_PENDING_REVIEW = 'pending_review'  # a doctor typed it; visible in the picker, unvetted
    STATUS_CONFIRMED = 'confirmed'            # admin (or a successful claim) vetted the entity
    STATUS_MERGED = 'merged'                  # duplicate of another entry — see merged_into
    STATUS_REJECTED = 'rejected'              # junk / not a real hospital
    STATUS_CHOICES = [
        (STATUS_PENDING_REVIEW, 'Pending review'),
        (STATUS_CONFIRMED, 'Confirmed'),
        (STATUS_MERGED, 'Merged'),
        (STATUS_REJECTED, 'Rejected'),
    ]
    # Registry entries a doctor should ever be offered in the picker.
    VISIBLE_STATUSES = (STATUS_PENDING_REVIEW, STATUS_CONFIRMED)

    # --- Axis 2: the account claim on this entry ---------------------------
    CLAIM_NONE = 'none'
    CLAIM_PENDING = 'pending'
    CLAIM_APPROVED = 'approved'
    CLAIM_REJECTED = 'rejected'
    CLAIM_CHOICES = [
        (CLAIM_NONE, 'No claim'),
        (CLAIM_PENDING, 'Pending review'),
        (CLAIM_APPROVED, 'Approved'),
        (CLAIM_REJECTED, 'Rejected'),
    ]

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._original_claim_status = instance.claim_status
        return instance

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    # Order-independent, noise-stripped form of `name`, kept in sync by
    # save() below — see apps.hospitals.matching.normalized_key. Doubles as
    # the exact-match half of duplicate detection and as the DB-level
    # dedupe key (see Meta.constraints).
    normalized_name = models.CharField(max_length=220, db_index=True, blank=True)
    # Canonical key into apps.core.i18n.locations.json, not free text — same
    # registry apps.doctors.models.Workplace.city uses.
    city = models.CharField(max_length=64, choices=CITY_CHOICES, db_index=True)
    # Denormalized from `city`, kept in sync by save() — mirrors
    # apps.doctors.models.Workplace.region.
    region = models.CharField(max_length=64, db_index=True, default='', blank=True)
    address = models.CharField(max_length=500, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    website = models.URLField(max_length=300, blank=True)
    logo = models.ImageField(upload_to='hospital_logos/', null=True, blank=True)

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING_REVIEW, db_index=True,
    )
    # Set when `status` becomes STATUS_MERGED — the entry this one's
    # workplaces/links were re-pointed to by the admin merge action. SET_NULL
    # (not CASCADE): deleting the target hospital must not delete this
    # historical breadcrumb.
    merged_into = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='merged_from',
    )

    # SET_NULL, not CASCADE: deleting a hospital's User account must never
    # delete the registry entry other doctors' Workplace rows point at (see
    # apps.doctors.models.Workplace.hospital). See also the post_delete
    # signal in apps.hospitals.signals that resets claim_status to
    # CLAIM_NONE when this happens, so an approved claim never dangles
    # ownerless.
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='hospital',
    )
    claim_status = models.CharField(
        max_length=20, choices=CLAIM_CHOICES, default=CLAIM_NONE, db_index=True,
    )
    claim_requested_at = models.DateTimeField(null=True, blank=True)
    claim_reviewed_at = models.DateTimeField(null=True, blank=True)
    # Attribution for a doctor-submitted "add your variant" entry — SET_NULL
    # so deleting that doctor's account doesn't delete the registry entry
    # other doctors may already be using.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='hospitals_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [
            # Excludes merged entries so a hospital that got merged away can
            # free up its (name, city) pair for a genuinely different entry
            # later, without a stale unique row blocking it.
            models.UniqueConstraint(
                fields=['normalized_name', 'city'],
                # Nested Meta class bodies can't see sibling names on the
                # outer class (Python class-body scoping, not a closure) —
                # 'merged' is Hospital.STATUS_MERGED's literal value.
                condition=~Q(status='merged'),
                name='hospital_unique_normalized_name_per_city',
            ),
        ]
        indexes = [
            GinIndex(fields=['name'], name='hospital_name_trgm_idx', opclasses=['gin_trgm_ops']),
        ]

    def save(self, *args, **kwargs):
        self.normalized_name = normalized_key(self.name)
        if self.city:
            self.region = city_region(self.city) or ''
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.name} ({self.city})'


class HospitalDoctorLink(models.Model):
    """The affiliation between a hospital account and a doctor — kept as its
    own row rather than a status field on apps.doctors.models.Workplace for
    three reasons:

    1. An invite runs in the opposite direction from a doctor picking a
       hospital on their workplace form: "hospital invites doctor X" needs a
       row to exist *before* any Workplace does. A status field on Workplace
       cannot represent that at all.
    2. The relationship is hospital<->doctor, not hospital<->workplace — the
       doctor headcount cap (see apps.subscriptions.plans.
       HOSPITAL_PLAN_LIMITS['doctors']), "our doctors", and per-doctor stats
       are all counted per doctor, and one doctor may legitimately have two
       workplaces at the same hospital (two departments/branches).
    3. Rejecting or removing a link only ever touches this table plus one FK
       null-out on the affected Workplace rows — the Workplace itself, its
       WorkingHours, and its PROTECT-ed Appointments are structurally
       untouchable by a hospital's decision. See
       apps.hospitals.services.reject_link/remove_doctor.

    One row per (hospital, doctor) pair with a *mutable* status — a second
    request after a rejection is an UPDATE back to STATUS_PENDING, not a new
    row — so the unique constraint below doubles as anti-spam: a doctor
    cannot flood a hospital with repeated pending requests.
    """

    STATUS_PENDING = 'pending'      # doctor requested; hospital hasn't answered
    STATUS_INVITED = 'invited'      # hospital invited; doctor hasn't answered
    STATUS_CONFIRMED = 'confirmed'
    STATUS_REJECTED = 'rejected'
    STATUS_REMOVED = 'removed'      # was confirmed, then unlinked by either side
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_INVITED, 'Invited'),
        (STATUS_CONFIRMED, 'Confirmed'),
        (STATUS_REJECTED, 'Rejected'),
        (STATUS_REMOVED, 'Removed'),
    ]

    REQUESTED_BY_DOCTOR = 'doctor'
    REQUESTED_BY_HOSPITAL = 'hospital'
    REQUESTED_BY_CHOICES = [
        (REQUESTED_BY_DOCTOR, 'Doctor'),
        (REQUESTED_BY_HOSPITAL, 'Hospital'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name='doctor_links')
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='hospital_links',
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    requested_by = models.CharField(max_length=10, choices=REQUESTED_BY_CHOICES)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['hospital', 'doctor'], name='hospital_doctor_link_unique'),
        ]
        indexes = [
            models.Index(fields=['hospital', 'status']),
        ]

    def __str__(self):
        return f'{self.doctor_id} @ {self.hospital_id} ({self.status})'
