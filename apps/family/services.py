from rest_framework import serializers

from .models import Dependent


def resolve_dependent(user, dependent_id):
    """Shared ownership/validity check for an optional `dependent_id` coming
    from a client request. Reused by every app that accepts `dependent_id`
    on write (appointments, medications, records) so the rule stays in one
    place: the dependent must exist, be managed by the requesting user, and
    still be active (not soft-deleted).

    Returns the `Dependent` instance, or ``None`` when `dependent_id` is
    falsy (feature not used for this record — perfectly normal, most
    records are still for the account owner). Raises
    ``serializers.ValidationError`` — intended to be called from a
    ``validate_dependent_id`` field-level hook so DRF automatically keys the
    resulting 400 response under ``dependent_id``.
    """
    if not dependent_id:
        return None
    try:
        return Dependent.objects.get(pk=dependent_id, managed_by=user, is_active=True)
    except Dependent.DoesNotExist:
        raise serializers.ValidationError('Dependent not found.')
