import datetime

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView


def _parse_date_param(value, name):
    """Parse a YYYY-MM-DD query param; raise 400 ValidationError on bad format."""
    if not value:
        return None
    try:
        datetime.date.fromisoformat(value)
        return value
    except (ValueError, TypeError):
        raise ValidationError({name: 'Enter a valid date in YYYY-MM-DD format.'})

from apps.users.permissions import IsDoctor

from .models import BlockedPeriod, Workplace, WorkingHours
from .serializers import (
    BlockedPeriodSerializer,
    WorkingHoursPatchSerializer,
    WorkingHoursReplaceItemSerializer,
    WorkingHoursSerializer,
    WorkplaceSerializer,
)

_WEEKDAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
_DEFAULT_START = datetime.time(9, 0)
_DEFAULT_END = datetime.time(17, 0)


def _get_workplace(pk, doctor):
    try:
        return Workplace.objects.get(pk=pk, doctor=doctor)
    except Workplace.DoesNotExist:
        raise NotFound()


def _full_week_hours(workplace):
    """Return 7 dicts (Mon–Sun), synthesising defaults for days with no DB row."""
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
                'weekday_display': _WEEKDAY_NAMES[day],
                'start_time': _DEFAULT_START,
                'end_time': _DEFAULT_END,
                'is_active': False,
            })
    return result


class WorkplaceListCreateView(APIView):
    permission_classes = [IsDoctor]

    def get(self, request):
        workplaces = (
            Workplace.objects
            .filter(doctor=request.user)
            .prefetch_related('working_hours')
        )
        return Response(WorkplaceSerializer(workplaces, many=True).data)

    def post(self, request):
        serializer = WorkplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(doctor=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class WorkplaceDetailView(APIView):
    permission_classes = [IsDoctor]

    def patch(self, request, pk):
        workplace = _get_workplace(pk, request.user)
        serializer = WorkplaceSerializer(workplace, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        workplace = _get_workplace(pk, request.user)
        # TODO: check for future confirmed appointments when apps.appointments is implemented:
        # from apps.appointments.models import Appointment
        # from django.utils import timezone
        # if Appointment.objects.filter(
        #     workplace=workplace, starts_at__gt=timezone.now(), status='confirmed'
        # ).exists():
        #     return Response(
        #         {'code': 'conflict', 'message': 'Workplace has upcoming confirmed appointments.'},
        #         status=status.HTTP_409_CONFLICT,
        #     )
        workplace.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkplaceSetPrimaryView(APIView):
    permission_classes = [IsDoctor]

    def patch(self, request, pk):
        workplace = _get_workplace(pk, request.user)
        with transaction.atomic():
            Workplace.objects.filter(doctor=request.user).update(is_primary=False)
            workplace.is_primary = True
            workplace.save(update_fields=['is_primary'])
        return Response(WorkplaceSerializer(workplace).data)


class WorkingHoursView(APIView):
    permission_classes = [IsDoctor]

    def get(self, request, pk):
        workplace = _get_workplace(pk, request.user)
        return Response(_full_week_hours(workplace))

    def put(self, request, pk):
        workplace = _get_workplace(pk, request.user)

        if not isinstance(request.data, list):
            raise ValidationError({'detail': 'Expected a list of working-hours entries.'})

        items_serializer = WorkingHoursReplaceItemSerializer(data=request.data, many=True)
        items_serializer.is_valid(raise_exception=True)
        items = items_serializer.validated_data

        weekdays = [item['weekday'] for item in items]
        if len(weekdays) != len(set(weekdays)):
            raise ValidationError({'weekday': 'Duplicate weekdays are not allowed.'})

        provided_by_day = {item['weekday']: item for item in items}

        with transaction.atomic():
            workplace.working_hours.all().delete()
            WorkingHours.objects.bulk_create([
                WorkingHours(
                    workplace=workplace,
                    weekday=day,
                    start_time=provided_by_day[day]['start_time'] if day in provided_by_day else _DEFAULT_START,
                    end_time=provided_by_day[day]['end_time'] if day in provided_by_day else _DEFAULT_END,
                    is_active=provided_by_day[day]['is_active'] if day in provided_by_day else False,
                )
                for day in range(7)
            ])

        workplace.refresh_from_db()
        return Response(_full_week_hours(workplace))


class WorkingHoursDayView(APIView):
    permission_classes = [IsDoctor]

    def patch(self, request, pk, weekday):
        if weekday not in range(7):
            raise NotFound()

        workplace = _get_workplace(pk, request.user)

        serializer = WorkingHoursPatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        hours, _ = WorkingHours.objects.get_or_create(
            workplace=workplace,
            weekday=weekday,
            defaults={'start_time': _DEFAULT_START, 'end_time': _DEFAULT_END, 'is_active': False},
        )
        for field in ('start_time', 'end_time', 'is_active'):
            if field in data:
                setattr(hours, field, data[field])
        hours.save()

        return Response(WorkingHoursSerializer(hours).data)


class BlockedPeriodListCreateView(APIView):
    permission_classes = [IsDoctor]

    def get(self, request):
        qs = BlockedPeriod.objects.filter(doctor=request.user)
        from_date = _parse_date_param(request.query_params.get('from'), 'from')
        to_date = _parse_date_param(request.query_params.get('to'), 'to')
        if from_date:
            qs = qs.filter(ends_at__date__gte=from_date)
        if to_date:
            qs = qs.filter(starts_at__date__lte=to_date)
        return Response(BlockedPeriodSerializer(qs, many=True).data)

    def post(self, request):
        serializer = BlockedPeriodSerializer(
            data=request.data,
            context={'doctor': request.user},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(doctor=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BlockedPeriodDetailView(APIView):
    permission_classes = [IsDoctor]

    def _get_period(self, pk, doctor):
        try:
            return BlockedPeriod.objects.get(pk=pk, doctor=doctor)
        except BlockedPeriod.DoesNotExist:
            raise NotFound()

    def patch(self, request, pk):
        period = self._get_period(pk, request.user)
        serializer = BlockedPeriodSerializer(
            period,
            data=request.data,
            partial=True,
            context={'doctor': request.user},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        period = self._get_period(pk, request.user)
        period.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
