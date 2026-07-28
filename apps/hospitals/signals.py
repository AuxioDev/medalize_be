import logging

from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from apps.users.models import User

from .models import Hospital

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Hospital)
def notify_hospital_claim_reviewed(sender, instance, created, **kwargs):
    """Mirrors apps.users.models.notify_doctor_verified: detects a
    claim_status transition via the from_db snapshot (Hospital.from_db)
    and fires the matching hospital_approved/hospital_rejected
    notification to the account owner.

    No-ops on a fresh row (nothing to compare a transition against — a
    just-created Hospital never has _original_claim_status set, same
    reasoning as DoctorProfile.from_db) and when there's no owner yet
    (claim_status is only ever non-NONE once claim_or_create_hospital has
    already set one in the very same save)."""
    if created or instance.owner_id is None:
        return
    original = getattr(instance, '_original_claim_status', None)
    if original == instance.claim_status:
        return
    try:
        from apps.notifications.tasks import send_hospital_notification
        if instance.claim_status == Hospital.CLAIM_APPROVED:
            send_hospital_notification.delay(str(instance.owner_id), 'hospital_approved')
        elif instance.claim_status == Hospital.CLAIM_REJECTED:
            send_hospital_notification.delay(str(instance.owner_id), 'hospital_rejected')
    except Exception:
        logger.exception(
            'Failed to dispatch claim-review notification for hospital %s', instance.pk,
        )


@receiver(pre_delete, sender=User)
def reset_claim_on_owner_deletion(sender, instance, **kwargs):
    """Hospital.owner is SET_NULL (see that field's docstring) — deleting a
    hospital's User account must never delete the registry entry other
    doctors' Workplace rows point at. But Django's deletion collector
    performs the SET_NULL as a raw UPDATE, bypassing Hospital.save() (and
    therefore normalized_name/region re-derivation and the post_save signal
    above) entirely — so claim_status has to be reset explicitly here,
    *before* the delete (pre_delete, not post_delete: `owner=instance` must
    still match), or an approved claim would dangle in CLAIM_APPROVED with
    no owner behind it."""
    Hospital.objects.filter(owner=instance).update(claim_status=Hospital.CLAIM_NONE)
