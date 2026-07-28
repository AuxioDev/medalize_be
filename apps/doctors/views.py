import datetime
import logging
from io import BytesIO

from django.db.models.deletion import ProtectedError
from PIL import Image

logger = logging.getLogger(__name__)
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.uploads import randomize_upload_filename
from apps.subscriptions.entitlements import limits_for
from apps.users.models import DoctorProfile
from apps.users.permissions import IsDoctor, IsDoctorVerified

from .models import BlockedPeriod, Workplace, WorkingHours
from .serializers import (
    BlockedPeriodSerializer,
    DoctorProfileReadSerializer,
    DoctorProfileWriteSerializer,
    WorkingHoursPatchSerializer,
    WorkingHoursSerializer,
    WorkplaceSerializer,
)
from .services import (
    DEFAULT_END as _DEFAULT_END,
    DEFAULT_START as _DEFAULT_START,
    full_week_hours as _full_week_hours,
    invalidate_doctor_slots as _invalidate_doctor_slots,
    replace_working_hours as _replace_working_hours,
    validated_hours_items as _validated_hours_items,
)


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


class WorkplaceListCreateView(APIView):
    permission_classes = [IsDoctorVerified]

    def get(self, request):
        workplaces = (
            Workplace.objects
            .filter(doctor=request.user)
            .prefetch_related('working_hours')
        )
        return Response(
            WorkplaceSerializer(workplaces, many=True, context={'request': request}).data
        )

    def post(self, request):
        workplace_limit = limits_for(request.user)['workplaces']
        if Workplace.objects.filter(doctor=request.user).count() >= workplace_limit:
            raise PermissionDenied({
                'code': 'plan_limit_reached',
                'resource': 'workplaces',
                'limit': workplace_limit,
            })

        serializer = WorkplaceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        hours_data = request.data.get('working_hours')
        items = _validated_hours_items(hours_data) if hours_data is not None else None

        with transaction.atomic():
            workplace = serializer.save(doctor=request.user)
            if items is not None:
                _replace_working_hours(workplace, items)

        if items is not None:
            _invalidate_doctor_slots(request.user.id)

        return Response(
            WorkplaceSerializer(workplace, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class WorkplaceDetailView(APIView):
    permission_classes = [IsDoctorVerified]

    def patch(self, request, pk):
        workplace = _get_workplace(pk, request.user)
        serializer = WorkplaceSerializer(workplace, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        hours_data = request.data.get('working_hours')
        items = _validated_hours_items(hours_data) if hours_data is not None else None

        with transaction.atomic():
            serializer.save()
            if items is not None:
                _replace_working_hours(workplace, items)

        if items is not None:
            _invalidate_doctor_slots(request.user.id)

        return Response(WorkplaceSerializer(workplace, context={'request': request}).data)

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
        try:
            workplace.delete()
        except ProtectedError:
            return Response(
                {'code': 'conflict', 'message': 'Workplace has historical appointments and cannot be deleted.'},
                status=status.HTTP_409_CONFLICT,
            )
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
        return Response(WorkplaceSerializer(workplace, context={'request': request}).data)


class WorkingHoursView(APIView):
    permission_classes = [IsDoctorVerified]

    def get(self, request, pk):
        workplace = _get_workplace(pk, request.user)
        return Response(_full_week_hours(workplace))

    def put(self, request, pk):
        workplace = _get_workplace(pk, request.user)
        items = _validated_hours_items(request.data)

        with transaction.atomic():
            _replace_working_hours(workplace, items)

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
                logger.exception('Failed to enqueue blocked period notification for period %s', period.id)

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
        return Response(DoctorProfileReadSerializer(profile, context={'request': request}).data)

    def patch(self, request):
        profile, _ = DoctorProfile.objects.get_or_create(user=request.user)
        serializer = DoctorProfileWriteSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(DoctorProfileReadSerializer(profile, context={'request': request}).data)


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
    throttle_scope = 'file_upload'

    def post(self, request):
        file = request.FILES.get('diploma')
        if not file:
            raise ValidationError({'diploma': 'No file provided.'})
        if file.size > 10 * 1024 * 1024:
            raise ValidationError({'diploma': 'File size must not exceed 10 MB.'})

        # Validate the real file bytes — content_type/extension are client-controlled.
        # Only PDF and JPEG/PNG are accepted so malicious HTML/SVG/scripts can't be
        # stored and later served to admins reviewing diplomas.
        header = file.read(8)
        file.seek(0)
        if header.startswith(b'%PDF-'):
            extension = 'pdf'
        else:
            try:
                img = Image.open(BytesIO(file.read()))
                if img.format not in ('JPEG', 'PNG'):
                    raise ValidationError({'diploma': 'Only PDF, JPEG or PNG files are allowed.'})
                img.load()
                extension = 'jpg' if img.format == 'JPEG' else 'png'
            except ValidationError:
                raise
            except Exception:
                raise ValidationError({'diploma': 'Only PDF, JPEG or PNG files are allowed.'})
            finally:
                file.seek(0)

        # Discard the client-supplied filename/extension now that the real
        # type is known — prevents storing validated-PDF bytes under a
        # client-chosen .html/.svg extension that would be served with a
        # mismatched, exploitable Content-Type.
        randomize_upload_filename(file, extension)

        profile, _ = DoctorProfile.objects.get_or_create(user=request.user)
        old_diploma = profile.diploma_file
        profile.diploma_file = file
        profile.save(update_fields=['diploma_file'])
        if old_diploma and old_diploma.name and old_diploma.name != profile.diploma_file.name:
            try:
                old_diploma.delete(save=False)
            except Exception:
                logger.warning('Failed to delete replaced diploma %s', old_diploma.name)

        diploma_url = (
            request.build_absolute_uri(profile.diploma_file.url)
            if profile.diploma_file
            else None
        )
        return Response(
            {'message': 'Diploma uploaded successfully.', 'diploma_url': diploma_url}
        )
