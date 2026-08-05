import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

User = get_user_model()


def delete_account(user, reason='user_requested'):
    """Permanently and irreversibly deletes a user's account.

    Called by AccountDeleteView only, after it has already re-verified the
    caller's current password — this function itself performs no
    authorization check and assumes that has already happened.

    The product decision behind every step here (see the phase report for
    the full per-model table) is: full erasure of directly-identifying PII
    and medical content, with financial records retained-but-anonymized
    where a real accounting/tax reason exists to keep them at all. In order:

    1. Role-specific cascades — cancel future bookings (refunding any that
       were paid), and for a doctor, also stop their subscription and wipe
       their public professional profile. These run BEFORE the User row
       itself is touched because the doctor cascade below relies on the
       existing DoctorProfile.is_verified / User.is_active post_save
       signals (apps.users.models.notify_doctor_verified /
       cancel_appointments_on_deactivation) to actually fire
       cancel_future_appointments_for_doctor — reusing that exact
       mechanism rather than calling it a second time directly.
    2. Anonymize the two models explicitly called out for it (Review,
       Payment): the FK back to this user is nulled out (see the
       accompanying migrations making those fields nullable) rather than
       relying solely on step 4 scrubbing the User row — an explicit,
       provable "this reviewer/payer is no longer linkable" rather than an
       implicit one.
    3. Delete session/device/auth ephemera and FCM tokens outright — none
       of it has any retention value once the account is gone.
    4. Scrub the User row's own directly-identifying fields in place
       (never delete the row itself — Appointment/Payment/Review/
       Prescription/Message all still reference it, and deleting it would
       cascade-delete rows this function has deliberately chosen to
       retain). Flips is_active (reusing every existing "account is
       inactive" enforcement path — login, JWT auth, doctor search) and
       sets the new is_deleted flag so this is distinguishable from an
       ordinary reversible AccountDeactivateView deactivation.
    5. Revoke every outstanding session (belt-and-suspenders — is_active
       already blocks new logins/JWT auth, but this also kills any
       already-issued refresh token immediately rather than waiting for it
       to hit an is_active check on next use).

    Wrapped in one transaction: either the whole cascade lands, or (on an
    unexpected exception) none of it does — there is no realistic "partial
    deletion" the caller could sensibly recover from at the API layer.
    """
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)

        if user.role == User.ROLE_PATIENT:
            _erase_patient_data(user)
        elif user.role == User.ROLE_DOCTOR:
            _erase_doctor_data(user)
        elif user.role == User.ROLE_HOSPITAL:
            _cancel_subscription(user)

        _anonymize_reviews(user)
        _anonymize_payments(user)
        _delete_auth_ephemera(user)
        _delete_own_notifications(user)
        _scrub_user(user, reason=reason)

    from .views import _revoke_all_sessions
    _revoke_all_sessions(user)
    return user


def _erase_patient_data(user):
    """Everything that is exclusively this patient's own health data (no
    other party has a legitimate independent copy/interest in it) is
    deleted outright — MedicalRecord, Medication (+ its schedules/dose
    logs, via CASCADE), and their AI assistant conversation history.
    Prescription is included too: unlike Message/Thread (see
    _anonymize_... below for why those are NOT deleted), a Prescription row
    has no "other party's own content" analog — it is clinical
    documentation about this one patient, denormalized doctor/patient onto
    one row, not a two-sided exchange the issuing doctor separately owns a
    copy of.

    Appointment rows themselves are RETAINED (see the phase report — they
    remain useful, anonymized, business/clinical continuity records for the
    treating doctor), but their patient-authored/about-the-patient free
    text (`reason`, `notes`) is blanked.

    Family dependents managed by this account have no meaning without the
    account that manages them — soft-deleted via the same is_active=False
    mechanism apps.family.views.DependentDetailView.delete already uses
    (never hard-deleted: historical appointments/medications/records
    already pointing at a dependent must not be orphaned). Any outstanding
    adult-dependent consent-notice token is also invalidated here — the
    account it would have let someone object *out of* no longer exists, so
    the link must not remain usable.
    """
    from apps.appointments.models import Appointment
    from apps.family.models import Dependent
    from apps.medications.models import Medication
    from apps.prescriptions.models import Prescription
    from apps.records.models import MedicalRecord

    from .models import PatientProfile, cancel_future_appointments_for_patient

    cancel_future_appointments_for_patient(user)

    for record in MedicalRecord.objects.filter(patient=user):
        if record.file:
            try:
                record.file.delete(save=False)
            except Exception:
                logger.warning('Failed to delete record file for %s', record.pk)
        record.delete()

    Medication.objects.filter(patient=user).delete()

    try:
        from apps.assistant.models import Conversation
        Conversation.objects.filter(patient=user).delete()
    except Exception:
        logger.exception('Failed to delete assistant conversations for user %s', user.pk)

    Prescription.objects.filter(patient=user).delete()

    Appointment.objects.filter(patient=user).update(
        reason='', notes='', updated_at=timezone.now(),
    )

    Dependent.objects.filter(managed_by=user, is_active=True).update(
        is_active=False,
        consent_token_hash='',
        consent_token_expires_at=None,
        updated_at=timezone.now(),
    )

    PatientProfile.objects.filter(user=user).update(
        date_of_birth=None, blood_type='', address='', city='', region='',
        allergies='', chronic_conditions='', medications='',
    )


def _erase_doctor_data(user):
    """Cancels the doctor's subscription (see _cancel_subscription) and
    wipes their public professional profile's identifying/free-text fields
    (bio, license number, diploma file). `is_verified` is set False so the
    now-blank profile can never resurface in doctor search/booking.

    cancel_future_appointments_for_doctor is deliberately NOT called
    directly here — flipping `is_verified` (this function) and, later in
    delete_account, `is_active` (via _scrub_user) each independently
    trigger it via the existing post_save signals in apps.users.models
    (notify_doctor_verified / cancel_appointments_on_deactivation). Calling
    it a third time here would be pure duplication of an already-idempotent
    (no-op once nothing is left to cancel) cascade.

    Workplace/WorkingHours/BlockedPeriod and HospitalDoctorLink rows are
    deliberately left untouched: none of it is directly-identifying PII of
    the doctor (it's clinic name/address/schedule data), and Workplace
    specifically cannot be deleted at all here even if desired — historical
    Appointments reference it with on_delete=PROTECT.
    """
    from .models import DoctorProfile

    _cancel_subscription(user)

    try:
        profile = user.doctor_profile
    except DoctorProfile.DoesNotExist:
        return

    if profile.diploma_file:
        try:
            profile.diploma_file.delete(save=False)
        except Exception:
            logger.warning('Failed to delete diploma file for doctor %s', user.pk)

    profile.bio = ''
    profile.license_number = ''
    profile.diploma_file = None
    profile.is_verified = False
    profile.save(update_fields=['bio', 'license_number', 'diploma_file', 'is_verified'])


def _cancel_subscription(user):
    """Stops a doctor's or hospital's subscription from being treated as
    active/billable going forward. There is no auto-renew/recurring-charge
    API integrated anywhere in this codebase today (see
    apps.subscriptions.models.Subscription.auto_renew's docstring — every
    renewal is a manual, doctor-initiated Payriff checkout), so there is no
    live charge to literally "cancel" with a provider; what matters is that
    apps.subscriptions.tasks.sweep_subscriptions stops sending renewal-
    reminder emails to a deleted account and apps.subscriptions.
    entitlements stops treating it as entitled. STATUS_EXPIRED is exactly
    the status ENTITLED_STATUSES already excludes, and the sweep only acts
    on TRIALING/ACTIVE/PAST_DUE rows — an EXPIRED one is simply never
    touched by it again.
    """
    from apps.subscriptions.models import Subscription

    try:
        subscription = user.subscription
    except Subscription.DoesNotExist:
        return
    if subscription.status == Subscription.STATUS_EXPIRED:
        return
    subscription.status = Subscription.STATUS_EXPIRED
    subscription.trial_ends_at = None
    subscription.current_period_end = None
    subscription.grace_ends_at = None
    subscription.save(update_fields=[
        'status', 'trial_ends_at', 'current_period_end', 'grace_ends_at', 'updated_at',
    ])


def _anonymize_reviews(user):
    """A deleted patient's review is retained — deleting it outright would
    silently change the reviewed doctor's aggregate rating, which is
    exactly the outcome the phase spec calls out to avoid — but the FK
    back to an identifiable reviewer is explicitly nulled (see the
    accompanying migration making Review.patient nullable), not just left
    to rely on the User row being scrubbed elsewhere in this same
    transaction. `rating`/`comment`/`doctor`/timestamps are untouched."""
    from apps.appointments.models import Review
    Review.objects.filter(patient=user).update(patient=None)


def _anonymize_payments(user):
    """Payment rows are retained for accounting/tax purposes (this
    codebase has no explicit Azerbaijan-bookkeeping-retention-period
    reference to point to — see the phase report; a conservative
    assumption of "keep indefinitely, like the Subscription billing
    history it sits alongside" is used instead of guessing a number), but
    the FK back to an identifiable patient/doctor is explicitly nulled —
    same reasoning and same explicit-not-implicit choice as
    _anonymize_reviews above. `amount`/`currency`/`status`/
    `provider_order_id`/timestamps (the actual transactional facts) are
    untouched. Runs unconditionally for both FKs regardless of `user`'s
    role — a patient never appears as a Payment.doctor and vice versa, so
    the "wrong" filter is always a no-op."""
    from apps.payments.models import Payment
    Payment.objects.filter(patient=user).update(patient=None)
    Payment.objects.filter(doctor=user).update(doctor=None)


def _delete_auth_ephemera(user):
    """Nothing here has any retention value once the account is gone:
    linked social-login identities, remembered devices (also part of the
    session-revocation cascade — a device row carries the jti
    _revoke_all_sessions blacklists, but the row itself should not
    survive), and any outstanding password-reset/email-change codes. FCM
    tokens are deregistered the same way apps.notifications already
    expects a stale/invalid token to be cleaned up (see
    apps.notifications.tasks — a token is dropped wherever the provider
    reports it invalid); deleting the row outright here has the identical
    effect (no more pushes to that device) without waiting for that to
    happen."""
    from apps.notifications.models import FCMToken

    from .models import EmailChangeRequest, PasswordResetOTP, SocialAccount, UserDevice

    SocialAccount.objects.filter(user=user).delete()
    UserDevice.objects.filter(user=user).delete()
    PasswordResetOTP.objects.filter(user=user).delete()
    EmailChangeRequest.objects.filter(user=user).delete()
    FCMToken.objects.filter(user=user).delete()


def _delete_own_notifications(user):
    """Pure personal-inbox cleanup — a Notification's only reader is the
    account it belongs to, and there is no one left to read these once the
    account is gone."""
    from apps.notifications.models import Notification, NotificationPreference
    Notification.objects.filter(user=user).delete()
    NotificationPreference.objects.filter(user=user).delete()


def _scrub_user(user, reason=''):
    """Erases every directly-identifying field on the User row itself.
    The row is never deleted (see delete_account's docstring for why) —
    only mutated in place, exactly like every helper above.

    `email` is replaced with a value derived from the row's own immutable
    uuid pk rather than left blank, because it remains User.USERNAME_FIELD
    and unique=True — a blank/colliding value would either break the
    unique constraint the moment a second account was deleted, or (if left
    as the real email) leave the one piece of PII this function exists to
    remove. set_unusable_password() is defense in depth: is_active=False
    already blocks login and JWT auth, but this also forecloses ever
    reactivating this specific row into a usable login by any path that
    might flip is_active back (there is none today, but there is for
    ordinary AccountDeactivateView deactivation, and is_deleted below is
    exactly the flag future code must check to tell the two apart).
    """
    if user.avatar:
        try:
            user.avatar.delete(save=False)
        except Exception:
            logger.warning('Failed to delete avatar for deleted user %s', user.pk)

    user.email = f'deleted-{user.pk}@deleted.medalize.local'
    user.first_name = ''
    user.last_name = ''
    user.phone = ''
    user.avatar = None
    user.is_active = False
    user.is_deleted = True
    user.deleted_at = timezone.now()
    user.set_unusable_password()
    user.save()
