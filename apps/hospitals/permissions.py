from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from apps.users.models import User


class IsHospital(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated
            and request.user.role == User.ROLE_HOSPITAL
        )


class IsHospitalApproved(IsHospital):
    """Extends IsHospital (never replaces it) — gates every endpoint that
    requires the hospital account to have cleared admin review, mirroring
    apps.users.permissions.IsDoctorVerified. Raises a structured error
    payload rather than a bare 403, matching
    apps.users.permissions.OnboardingStepRequired: the mobile client reads
    `code` to route to the pending-approval screen instead of showing a
    generic error."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        hospital = getattr(request.user, 'hospital', None)
        if hospital is None or hospital.claim_status != hospital.CLAIM_APPROVED:
            raise PermissionDenied({
                'code': 'hospital_not_approved',
                'claim_status': hospital.claim_status if hospital else 'none',
            })
        return True


class IsHospitalSubscribed(IsHospitalApproved):
    """Extends IsHospitalApproved, never replaces it — an unsubscribed but
    already-approved hospital must still be blocked from the dashboard
    (this is the whole point of "no trial, straight to paywall"), and an
    unapproved hospital must never even reach the subscription check — it
    should see the approval-pending screen, not a paywall it can't act on
    yet."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        from apps.subscriptions.entitlements import ENTITLED_STATUSES
        from apps.subscriptions.models import Subscription

        try:
            subscription = request.user.subscription
        except Subscription.DoesNotExist:
            subscription = None
        if subscription is None or subscription.status not in ENTITLED_STATUSES:
            raise PermissionDenied({'code': 'subscription_required'})
        return True
