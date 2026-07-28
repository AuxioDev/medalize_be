"""State machine for the hospital<->doctor affiliation
(HospitalDoctorLink). See that model's docstring for why the affiliation
lives in its own table rather than as a status field on
apps.doctors.models.Workplace.

Call graph:
  - apps.doctors.serializers.WorkplaceSerializer calls sync_workplace_
    hospital_link / sync_link_after_workplace_hospital_removed whenever a
    doctor sets/clears `Workplace.hospital` themselves.
  - apps.hospitals.views calls invite_doctor/approve_link/reject_link/
    remove_doctor for the hospital-dashboard side.
  - apps.hospitals.views (doctor-side accept/decline endpoints) call
    approve_link/reject_link too — confirming is confirming regardless of
    who clicked the button; only the permission check and `decided_by`
    differ between the two call sites.
"""
import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.subscriptions.entitlements import limits_for

from .models import Hospital, HospitalDoctorLink

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = (
    HospitalDoctorLink.STATUS_PENDING,
    HospitalDoctorLink.STATUS_INVITED,
    HospitalDoctorLink.STATUS_CONFIRMED,
)


def claim_or_create_hospital(user, *, hospital_id=None, name='', city='', address=''):
    """Called from apps.users.serializers.RegisterSerializer.create() when
    a new User registers with role='hospital'. Two paths, both ending with
    `user.hospital` set and `claim_status = CLAIM_PENDING` — admin approval
    always follows, mirroring DoctorProfile.is_verified's gate:

      - `hospital_id` given: the registrant found themselves in the picker
        (an entry a doctor added, or an admin-seeded one) and is claiming
        it. Fails loudly if it's already claimed or isn't a live registry
        entry (merged/rejected).
      - No `hospital_id`: their hospital was never added by anyone —
        creates a brand-new STATUS_PENDING_REVIEW entry, exactly like a
        doctor's "add your variant" (see HospitalCreateSerializer), owned
        by this account from the start.

    Runs inside the caller's transaction (RegisterSerializer wraps user
    creation + this call in one atomic block) so a failure here rolls back
    the User row too — a 'hospital' User must never exist without a
    Hospital attached.
    """
    if hospital_id:
        try:
            hospital = Hospital.objects.select_for_update().get(pk=hospital_id)
        except Hospital.DoesNotExist:
            raise ValidationError({'hospital_id': 'Hospital not found.'})
        if hospital.status not in Hospital.VISIBLE_STATUSES:
            raise ValidationError({'hospital_id': 'This hospital entry is not available to claim.'})
        if hospital.owner_id is not None:
            raise ValidationError({'hospital_id': 'This hospital has already been claimed.'})
    else:
        if not name or not city:
            raise ValidationError({
                'hospital_name': 'Select your hospital from the list, or provide a name and city to add it.',
            })
        hospital = Hospital(name=name, city=city, address=address)
        hospital.save()

    hospital.owner = user
    hospital.claim_status = Hospital.CLAIM_PENDING
    hospital.claim_requested_at = timezone.now()
    hospital.save(update_fields=['owner', 'claim_status', 'claim_requested_at', 'updated_at'])
    return hospital


def _notify(user_id, template_key, **kwargs):
    if user_id is None:
        return
    try:
        from apps.notifications.tasks import send_hospital_notification
        send_hospital_notification.delay(str(user_id), template_key, **kwargs)
    except Exception:
        logger.exception(
            'Failed to dispatch %s notification for user %s', template_key, user_id,
        )


def _display_name(user):
    return f'{user.first_name} {user.last_name}'.strip() or user.email


def _unlink_workplaces(hospital, doctor):
    """Null the `hospital` FK on every one of this doctor's workplaces at
    this hospital. Never touches the Workplace row itself, its WorkingHours,
    or any Appointment — only this one FK, so the workplace keeps working on
    its free-text `name` exactly as a private-practice workplace would."""
    from apps.doctors.models import Workplace

    return Workplace.objects.filter(hospital=hospital, doctor=doctor).update(hospital=None)


def _confirmed_doctor_count(hospital):
    return HospitalDoctorLink.objects.filter(
        hospital=hospital, status=HospitalDoctorLink.STATUS_CONFIRMED,
    ).count()


def _check_doctor_cap(hospital):
    """Only ever called from the transition INTO STATUS_CONFIRMED — never
    when a doctor merely requests a link (see sync_workplace_hospital_link).
    A doctor's own request must never reveal anything about a hospital's
    plan/billing state, the same reasoning as the monthly-appointment cap in
    apps.appointments.serializers."""
    owner = hospital.owner
    limit = limits_for(owner)['doctors'] if owner is not None else 0
    if limit is None:
        return
    if _confirmed_doctor_count(hospital) >= limit:
        raise PermissionDenied({
            'code': 'plan_limit_reached',
            'resource': 'doctors',
            'limit': limit,
        })


def sync_workplace_hospital_link(hospital, doctor):
    """Called after a doctor sets `Workplace.hospital` to a non-null value
    on their own workplace (create or update). Ensures a HospitalDoctorLink
    exists and is (re)opened as a doctor-initiated request — never
    auto-confirms and never checks the doctor cap, since confirming is a
    hospital-only decision (see approve_link)."""
    link, created = HospitalDoctorLink.objects.get_or_create(
        hospital=hospital, doctor=doctor,
        defaults={
            'status': HospitalDoctorLink.STATUS_PENDING,
            'requested_by': HospitalDoctorLink.REQUESTED_BY_DOCTOR,
        },
    )
    reopened = False
    if not created and link.status in (HospitalDoctorLink.STATUS_REJECTED, HospitalDoctorLink.STATUS_REMOVED):
        link.status = HospitalDoctorLink.STATUS_PENDING
        link.requested_by = HospitalDoctorLink.REQUESTED_BY_DOCTOR
        link.decided_by = None
        link.decided_at = None
        link.save(update_fields=['status', 'requested_by', 'decided_by', 'decided_at', 'updated_at'])
        reopened = True
    if created or reopened:
        _notify(hospital.owner_id, 'hospital_link_requested', doctor_name=_display_name(doctor))
    return link


def sync_link_after_workplace_hospital_removed(hospital, doctor):
    """Called after a doctor clears `Workplace.hospital` (PATCHes it back to
    null) directly on one of their own workplaces — as opposed to the
    hospital rejecting/removing them. Only downgrades the link if the
    doctor has no *other* workplace still pointing at this hospital (a
    doctor may have two departments at one hospital; dropping one shouldn't
    end the whole affiliation). The caller has already saved the Workplace
    change itself, so this never touches Workplace rows."""
    from apps.doctors.models import Workplace

    if Workplace.objects.filter(doctor=doctor, hospital=hospital).exists():
        return None
    try:
        link = HospitalDoctorLink.objects.get(hospital=hospital, doctor=doctor)
    except HospitalDoctorLink.DoesNotExist:
        return None
    if link.status not in _ACTIVE_STATUSES:
        return link
    link.status = (
        HospitalDoctorLink.STATUS_REMOVED if link.status == HospitalDoctorLink.STATUS_CONFIRMED
        else HospitalDoctorLink.STATUS_REJECTED
    )
    link.decided_by = doctor
    link.decided_at = timezone.now()
    link.save(update_fields=['status', 'decided_by', 'decided_at', 'updated_at'])
    return link


def invite_doctor(hospital, doctor, invited_by):
    """Hospital-initiated direction: no Workplace needs to exist yet — see
    HospitalDoctorLink's docstring, reason 1."""
    link, created = HospitalDoctorLink.objects.get_or_create(
        hospital=hospital, doctor=doctor,
        defaults={
            'status': HospitalDoctorLink.STATUS_INVITED,
            'requested_by': HospitalDoctorLink.REQUESTED_BY_HOSPITAL,
        },
    )
    if not created:
        if link.status == HospitalDoctorLink.STATUS_CONFIRMED:
            raise ValidationError({'detail': 'This doctor is already linked to your hospital.'})
        if link.status in (HospitalDoctorLink.STATUS_PENDING, HospitalDoctorLink.STATUS_INVITED):
            raise ValidationError({'detail': 'A request is already pending for this doctor.'})
        link.status = HospitalDoctorLink.STATUS_INVITED
        link.requested_by = HospitalDoctorLink.REQUESTED_BY_HOSPITAL
        link.decided_by = None
        link.decided_at = None
        link.save(update_fields=['status', 'requested_by', 'decided_by', 'decided_at', 'updated_at'])
    _notify(doctor.id, 'hospital_invite_received', hospital_name=hospital.name)
    return link


def approve_link(link, decided_by):
    """Confirms a pending request or invite. Used by both directions —
    the hospital approving a doctor-initiated request, and the doctor
    accepting a hospital-initiated invite — confirming is confirming
    regardless of who clicked; only the caller's permission check and
    `decided_by` differ."""
    if link.status not in (HospitalDoctorLink.STATUS_PENDING, HospitalDoctorLink.STATUS_INVITED):
        raise ValidationError({'detail': 'Only a pending or invited link can be confirmed.'})
    _check_doctor_cap(link.hospital)
    link.status = HospitalDoctorLink.STATUS_CONFIRMED
    link.decided_by = decided_by
    link.decided_at = timezone.now()
    link.save(update_fields=['status', 'decided_by', 'decided_at', 'updated_at'])
    # Only notify the doctor when the *hospital* side confirmed (their own
    # request got approved) — when the doctor themselves just accepted an
    # invite, they already know; telling them again is noise.
    if decided_by.id != link.doctor_id:
        _notify(link.doctor_id, 'hospital_link_confirmed', hospital_name=link.hospital.name)
    return link


def reject_link(link, decided_by):
    """Declines a request/invite that was never confirmed. Used by both the
    hospital rejecting a doctor's request and the doctor declining a
    hospital's invite."""
    if link.status not in (HospitalDoctorLink.STATUS_PENDING, HospitalDoctorLink.STATUS_INVITED):
        raise ValidationError({'detail': 'Only a pending or invited link can be rejected.'})
    with transaction.atomic():
        link.status = HospitalDoctorLink.STATUS_REJECTED
        link.decided_by = decided_by
        link.decided_at = timezone.now()
        link.save(update_fields=['status', 'decided_by', 'decided_at', 'updated_at'])
        _unlink_workplaces(link.hospital, link.doctor)
    return link


# Status precedence used by resolve_merge when a merge collision leaves two
# links for the same doctor (linked to both the source and target hospital
# already) — the stronger status wins, the weaker row is dropped.
_LINK_STRENGTH = {
    HospitalDoctorLink.STATUS_CONFIRMED: 3,
    HospitalDoctorLink.STATUS_PENDING: 2,
    HospitalDoctorLink.STATUS_INVITED: 2,
    HospitalDoctorLink.STATUS_REJECTED: 1,
    HospitalDoctorLink.STATUS_REMOVED: 1,
}


def resolve_merge(source):
    """Called by apps.hospitals.admin.HospitalAdmin.save_model right after
    an admin sets `source.merged_into` and flips `source.status` to
    STATUS_MERGED — re-points every Workplace and HospitalDoctorLink off
    the now-defunct duplicate entry onto the surviving one, so the merge
    doesn't silently orphan doctors who'd picked the duplicate."""
    target = source.merged_into
    if target is None or target.pk == source.pk:
        return

    with transaction.atomic():
        from apps.doctors.models import Workplace

        Workplace.objects.filter(hospital=source).update(hospital=target)

        existing_by_doctor = {
            link.doctor_id: link
            for link in HospitalDoctorLink.objects.filter(hospital=target)
        }
        for link in HospitalDoctorLink.objects.filter(hospital=source):
            existing = existing_by_doctor.get(link.doctor_id)
            if existing is None:
                link.hospital = target
                link.save(update_fields=['hospital', 'updated_at'])
            elif _LINK_STRENGTH[link.status] > _LINK_STRENGTH[existing.status]:
                existing.delete()
                link.hospital = target
                link.save(update_fields=['hospital', 'updated_at'])
            else:
                link.delete()


def remove_doctor(link, decided_by):
    """Ends an already-confirmed affiliation — from either side. The
    doctor's Workplace(s) at this hospital, their WorkingHours, and every
    Appointment booked through them are untouched; only the `hospital` FK on
    those workplaces is nulled (see HospitalDoctorLink's docstring, reason
    3 — this is the invariant the whole feature is built around)."""
    if link.status != HospitalDoctorLink.STATUS_CONFIRMED:
        raise ValidationError({'detail': 'Only a confirmed link can be removed.'})
    hospital_name = link.hospital.name
    with transaction.atomic():
        link.status = HospitalDoctorLink.STATUS_REMOVED
        link.decided_by = decided_by
        link.decided_at = timezone.now()
        link.save(update_fields=['status', 'decided_by', 'decided_at', 'updated_at'])
        _unlink_workplaces(link.hospital, link.doctor)
    # Only notify the doctor when the *hospital* ended it — a doctor who
    # removed themselves already knows.
    if decided_by.id != link.doctor_id:
        _notify(link.doctor_id, 'hospital_doctor_removed', hospital_name=hospital_name)
    return link
