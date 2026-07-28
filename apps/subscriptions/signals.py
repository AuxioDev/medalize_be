import logging
from datetime import timedelta

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.users.models import DoctorProfile, User

from .models import DoctorSubscription
from .plans import TRIAL_DAYS

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def create_doctor_subscription(sender, instance, created, **kwargs):
    """Mirrors apps.users.models.create_role_profile: every doctor gets a
    subscription row at registration, starting in `pending` (the trial has
    not started yet — it starts at verification, see below)."""
    if not created or instance.role != User.ROLE_DOCTOR:
        return
    DoctorSubscription.objects.create(user=instance)


@receiver(post_save, sender=DoctorProfile)
def start_trial_on_verification(sender, instance, created, **kwargs):
    """Starts the 7-day trial the moment a doctor is first verified, not at
    registration — before verification IsDoctorVerified already blocks
    everything a doctor could do, so starting the clock earlier would just
    burn trial days while waiting on admin review.

    Deliberately does NOT try to detect a False→True *transition* (the
    apps.users.models.notify_doctor_verified/`_original_is_verified`
    approach) — that snapshot is only populated when DoctorProfile.from_db()
    runs, which a freshly created-then-saved-in-the-same-request instance
    (e.g. right after DoctorProfile.objects.create() during registration)
    never goes through, silently no-op'ing the transition check. Instead
    this fires on every save where is_verified is True and relies entirely
    on the `status=STATUS_PENDING` filter for idempotency: the very first
    verification flips pending→trialing, and every save after that
    (including unrelated profile edits, or re-verification after the doctor
    is later unverified) finds status is no longer PENDING and no-ops.
    """
    if not instance.is_verified:
        return

    updated = DoctorSubscription.objects.filter(
        user_id=instance.user_id, status=DoctorSubscription.STATUS_PENDING,
    ).update(
        status=DoctorSubscription.STATUS_TRIALING,
        trial_ends_at=timezone.now() + timedelta(days=TRIAL_DAYS),
    )
    if created and not updated:
        logger.warning(
            'DoctorProfile %s created already verified but has no pending '
            'subscription row (create_doctor_subscription signal should have '
            'made one at User creation) — trial was not started.',
            instance.user_id,
        )
