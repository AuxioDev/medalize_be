import datetime

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.models import DoctorProfile
from apps.users.permissions import IsDoctor, IsDoctorVerified

from .models import BlockedPeriod, Workplace, WorkingHours
from .serializers import (
    BlockedPeriodSerializer,
    DoctorProfileReadSerializer,
    DoctorProfileWriteSerializer,
    WorkingHoursPatchSerializer,
    WorkingHoursReplaceItemSerializer,
    WorkingHoursSerializer,
    WorkplaceSerializer,
)

_WEEKDAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
_DEFAULT_START = datetime.time(9, 0)
_DEFAULT_END = datetime.time(17, 0)


def _parse_date_param(value, name):
    if not value:
        return None
    try:
        datetime.date.fromisoformat(value)
        return value
    except (ValueError, TypeError):
        raise ValidationError({name: 'Enter a valid date in YYYY-MM-DD format.'})


def _get_workplace(pk, doctor):
    try:
        return Workplace.objects.get(pk=pk, doctor=doctor)
    except Workplace.DoesNotExist:
        raise NotFound()


def _invalidate_doctor_slots(doctor_id):
    """Drop every cached availability entry for a doctor (keys ``slots:{doctor_id}:*``).

    Called whenever working hours or blocked periods change, so the next slot
    query recomputes instead of serving stale cached windows. The django-redis
    backend supports ``delete_pattern``; backends that don't (e.g. the LocMemCache
    used in tests) simply no-op.
    """
    delete_pattern = getattr(cache, 'delete_pattern', None)
    if delete_pattern is None:
        return
    try:
        delete_pattern(f'slots:{doctor_id}:*')
    except Exception:
        pass


def _full_week_hours(workplace):
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
    permission_classes = [IsDoctorVerified]

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
    permission_classes = [IsDoctorVerified]

    def patch(self, request, pk):
        workplace = _get_workplace(pk, request.user)
        serializer = WorkplaceSerializer(workplace, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        workplace = _get_workplace(pk, request.user)
        from apps.appointments.models import Appointment
        if Appointment.objects.filter(
            workplace=workplace,
            starts_at__gt=timezone.now(),
            status__in=[Appointment.STATUS_PENDING, Appointment.STATUS_CONFIRMED],
        ).exists():
            return Response(
                {'code': 'conflict', 'message': 'Workplace has upcoming confirmed appointments.'},
                status=status.HTTP_409_CONFLICT,
            )
        workplace.delete()
        _invalidate_doctor_slots(request.user.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkplaceSetPrimaryView(APIView):
    permission_classes = [IsDoctorVerified]

    def patch(self, request, pk):
        workplace = _get_workplace(pk, request.user)
        with transaction.atomic():
            Workplace.objects.filter(doctor=request.user).update(is_primary=False)
            workplace.is_primary = True
            workplace.save(update_fields=['is_primary'])
        return Response(WorkplaceSerializer(workplace).data)


class WorkingHoursView(APIView):
    permission_classes = [IsDoctorVerified]

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

        _invalidate_doctor_slots(request.user.id)
        workplace.refresh_from_db()
        return Response(_full_week_hours(workplace))


class WorkingHoursDayView(APIView):
    permission_classes = [IsDoctorVerified]

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

        _invalidate_doctor_slots(request.user.id)
        return Response(WorkingHoursSerializer(hours).data)


class BlockedPeriodListCreateView(APIView):
    permission_classes = [IsDoctorVerified]

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
        notify = serializer.validated_data.pop('notify_patients', False)
        period = serializer.save(doctor=request.user)

        _invalidate_doctor_slots(request.user.id)

        if notify:
            try:
                from apps.notifications.tasks import notify_blocked_period_patients
                notify_blocked_period_patients.delay(str(period.id))
            except Exception:
                pass

        return Response(BlockedPeriodSerializer(period).data, status=status.HTTP_201_CREATED)


class BlockedPeriodDetailView(APIView):
    permission_classes = [IsDoctorVerified]

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
        serializer.validated_data.pop('notify_patients', None)
        serializer.save()
        _invalidate_doctor_slots(request.user.id)
        return Response(serializer.data)

    def delete(self, request, pk):
        period = self._get_period(pk, request.user)
        period.delete()
        _invalidate_doctor_slots(request.user.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DoctorProfileView(APIView):
    """Read/write the authenticated doctor's profile. Available to unverified
    doctors so they can complete onboarding."""

    permission_classes = [IsDoctor]

    def get(self, request):
        profile, _ = DoctorProfile.objects.get_or_create(user=request.user)
        return Response(DoctorProfileReadSerializer(profile).data)

    def patch(self, request):
        profile, _ = DoctorProfile.objects.get_or_create(user=request.user)
        serializer = DoctorProfileWriteSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(DoctorProfileReadSerializer(profile).data)


class OnboardingCompleteView(APIView):
    """Finalize doctor onboarding once the required fields and diploma are set.
    Verification itself stays an admin action (``is_verified`` is untouched)."""

    permission_classes = [IsDoctor]

    def post(self, request):
        profile, _ = DoctorProfile.objects.get_or_create(user=request.user)

        missing = []
        if not profile.specialization:
            missing.append('specialization')
        if not profile.license_number:
            missing.append('license_number')
        if not profile.diploma_file:
            missing.append('diploma')
        if missing:
            return Response(
                {
                    'code': 'onboarding_incomplete',
                    'message': 'Complete all required fields before finishing onboarding.',
                    'missing': missing,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not profile.onboarding_complete:
            profile.onboarding_complete = True
            profile.onboarding_step = 99
            profile.save(update_fields=['onboarding_complete', 'onboarding_step'])

        return Response(
            {
                'onboarding_complete': profile.onboarding_complete,
                'is_verified': profile.is_verified,
            }
        )


class DiplomaUploadView(APIView):
    permission_classes = [IsDoctor]
    parser_classes = [MultiPartParser]

    def post(self, request):
        file = request.FILES.get('diploma')
        if not file:
            raise ValidationError({'diploma': 'No file provided.'})
        # Any file type is accepted (images, PDF, documents, …). A 10 MB size cap
        # is kept; uploads are stored on the configured backend (Cloudinary in
        # production, local media in development).
        if file.size > 10 * 1024 * 1024:
            raise ValidationError({'diploma': 'File size must not exceed 10 MB.'})

        profile, _ = DoctorProfile.objects.get_or_create(user=request.user)
        profile.diploma_file = file
        profile.save(update_fields=['diploma_file'])

        diploma_url = (
            request.build_absolute_uri(profile.diploma_file.url)
            if profile.diploma_file
            else None
        )
        return Response(
            {'message': 'Diploma uploaded successfully.', 'diploma_url': diploma_url}
        )
