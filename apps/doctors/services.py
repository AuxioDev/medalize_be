"""Working-hours helpers shared by apps.doctors' own workplace/hours
endpoints and apps.hospitals' dashboard (a hospital editing hours for one of
its confirmed doctors' workplaces uses the exact same replace-a-week
semantics). Moved out of apps.doctors.views so apps.hospitals.views can
import them without reaching into another app's view module — a views-to-
views import across apps is how circular imports start.
"""
import datetime
import logging

from django.core.cache import cache
from rest_framework.exceptions import ValidationError

from .models import WorkingHours
from .serializers import WorkingHoursReplaceItemSerializer

logger = logging.getLogger(__name__)

WEEKDAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
DEFAULT_START = datetime.time(9, 0)
DEFAULT_END = datetime.time(17, 0)


def invalidate_doctor_slots(doctor_id):
    """Drop every cached availability entry for a doctor: the per-day
    ``slots:{doctor_id}:*`` keys and the 14-day-window ``next_slot:{doctor_id}``.

    Called whenever working hours or blocked periods change, so the next slot
    query recomputes instead of serving stale cached windows. The django-redis
    backend supports ``delete_pattern``; backends that don't (e.g. the LocMemCache
    used in tests) simply no-op for the pattern delete.
    """
    cache.delete(f'next_slot:{doctor_id}')
    delete_pattern = getattr(cache, 'delete_pattern', None)
    if delete_pattern is None:
        return
    try:
        delete_pattern(f'slots:{doctor_id}:*')
    except Exception:
        pass


def full_week_hours(workplace):
    existing = {h.weekday: h for h in workplace.working_hours.all()}
    result = []
    for day in range(7):
        if day in existing:
            h = existing[day]
            result.append({
                'id': h.id,
                'weekday': h.weekday,
                'weekday_display': h.get_weekday_display(),
                'start_time': h.start_time,
                'end_time': h.end_time,
                'is_active': h.is_active,
            })
        else:
            result.append({
                'id': None,
                'weekday': day,
                'weekday_display': WEEKDAY_NAMES[day],
                'start_time': DEFAULT_START,
                'end_time': DEFAULT_END,
                'is_active': False,
            })
    return result


def validated_hours_items(data):
    """Validate a working-hours replace payload (a list of per-weekday entries).

    Shared by the dedicated hours endpoints (apps.doctors and apps.hospitals)
    and the workplace create/update endpoints, which accept an optional
    ``working_hours`` list so a doctor can set their schedule in the same
    request that creates/edits the workplace.
    """
    if not isinstance(data, list):
        raise ValidationError({'detail': 'Expected a list of working-hours entries.'})

    items_serializer = WorkingHoursReplaceItemSerializer(data=data, many=True)
    items_serializer.is_valid(raise_exception=True)
    items = items_serializer.validated_data

    weekdays = [item['weekday'] for item in items]
    if len(weekdays) != len(set(weekdays)):
        raise ValidationError({'weekday': 'Duplicate weekdays are not allowed.'})

    return items


def notify_verification_reset(profile):
    """Tell a doctor their profile dropped out of verified status because
    they edited a credential field (specialization, license number, or
    diploma) that the original admin review covered. Called after the
    caller has already flipped ``profile.is_verified`` to False (with
    ``_verification_reset_skip_cascade`` set first) and saved. Unlike an
    admin-initiated unverify, this does *not* cancel the doctor's existing
    future appointments — see apps.users.models.notify_doctor_verified for
    why a self-service edit is deliberately treated more gently; it only
    drops the doctor out of verified search/booking until re-review.
    This function only handles the doctor-facing explanation, reusing
    send_doctor_verified's dispatch shape (Notification row + email + push)
    rather than a bespoke path, since there is no separate "submitted for
    verification" notification in this codebase to reuse — verification
    review is otherwise a manual admin-panel action, not a queued request."""
    try:
        from apps.notifications.tasks import send_doctor_verification_reset
        send_doctor_verification_reset.delay(profile.user_id)
    except Exception:
        logger.exception(
            'Failed to dispatch verification-reset notification for doctor %s', profile.user_id
        )


def replace_working_hours(workplace, items):
    provided_by_day = {item['weekday']: item for item in items}
    workplace.working_hours.all().delete()
    WorkingHours.objects.bulk_create([
        WorkingHours(
            workplace=workplace,
            weekday=day,
            start_time=provided_by_day[day]['start_time'] if day in provided_by_day else DEFAULT_START,
            end_time=provided_by_day[day]['end_time'] if day in provided_by_day else DEFAULT_END,
            is_active=provided_by_day[day]['is_active'] if day in provided_by_day else False,
        )
        for day in range(7)
    ])
