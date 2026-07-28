"""Working-hours helpers shared by apps.doctors' own workplace/hours
endpoints and apps.hospitals' dashboard (a hospital editing hours for one of
its confirmed doctors' workplaces uses the exact same replace-a-week
semantics). Moved out of apps.doctors.views so apps.hospitals.views can
import them without reaching into another app's view module — a views-to-
views import across apps is how circular imports start.
"""
import datetime

from django.core.cache import cache
from rest_framework.exceptions import ValidationError

from .models import WorkingHours
from .serializers import WorkingHoursReplaceItemSerializer

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
