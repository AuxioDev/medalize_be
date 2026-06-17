from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from .models import User


class IsPatient(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == User.ROLE_PATIENT)


class IsDoctor(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role == User.ROLE_DOCTOR)


class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.is_staff or getattr(request.user, 'role', None) == 'admin')
        )


class IsDoctorVerified(BasePermission):
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated and request.user.role == User.ROLE_DOCTOR):
            return False
        try:
            return request.user.doctor_profile.is_verified
        except Exception:
            return False


def OnboardingStepRequired(required_step):
    """
    Factory that returns a permission class enforcing a minimum onboarding step.
    Usage: permission_classes = [OnboardingStepRequired(required_step=5)]
    """
    class _OnboardingStepRequired(BasePermission):
        def has_permission(self, request, view):
            if not (request.user and request.user.is_authenticated and request.user.role == User.ROLE_DOCTOR):
                return False
            try:
                profile = request.user.doctor_profile
            except Exception:
                return False

            if profile.onboarding_step < required_step:
                raise PermissionDenied({
                    'code': 'onboarding_incomplete',
                    'current_step': profile.onboarding_step,
                    'required_step': required_step,
                    'message': 'Complete onboarding to access this feature',
                    'redirect': f'/onboarding/step/{profile.onboarding_step}/',
                })
            return True

    return _OnboardingStepRequired
