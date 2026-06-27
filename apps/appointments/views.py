import datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.db.models import Avg, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.doctors.models import BlockedPeriod, Workplace, WorkingHours
from apps.users.permissions import IsDoctor, IsPatient
from .models import Appointment, Review
from .serializers import (
    AppointmentSerializer,
    AppointmentStatusSerializer,
    BookingSerializer,
    DoctorDetailSerializer,
    DoctorNotesSerializer,
    DoctorPublicSerializer,
    RescheduleSerializer,
    ReviewCreateSerializer,
    ReviewSerializer,
)

User = get_user_model()


class DoctorListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (
            User.objects
            .filter(role=User.ROLE_DOCTOR, doctor_profile__is_verified=True)
            .select_related('doctor_profile')
            .prefetch_related('workplaces')
            .order_by('first_name', 'last_name', 'id')
        )
        name = request.query_params.get('name', '').strip()
        specialization = request.query_params.get('specialization', '').strip()
        city = request.query_params.get('city', '').strip()
        min_rating = request.query_params.get('min_rating', '').strip()

        if name:
            qs = qs.filter(Q(first_name__icontains=name) | Q(last_name__icontains=name))
        if specialization:
            qs = qs.filter(doctor_profile__specialization=specialization)
        if city:
            qs = qs.filter(workplaces__city__icontains=city).distinct()
        if min_rating:
            try:
                min_rating_val = float(min_rating)
                qs = qs.annotate(avg_rating=Avg('doctor_reviews__rating')).filter(
                    avg_rating__gte=min_rating_val
                )
            except ValueError:
                pass

        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(DoctorPublicSerializer(page, many=True).data)


class DoctorDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            doctor = (
                User.objects
                .filter(role='doctor', doctor_profile__is_verified=True)
                .select_related('doctor_profile')
                .prefetch_related('workplaces__working_hours')
                .get(pk=pk)
            )
        except User.DoesNotExist:
            raise NotFound()
        return Response(DoctorDetailSerializer(doctor).data)


class SlotListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        date_str = request.query_params.get('date', '').strip()
        workplace_id = request.query_params.get('workplace_id', '').strip()

        if not date_str:
            raise ValidationError({'date': 'date is required.'})
        if not workplace_id:
            raise ValidationError({'workplace_id': 'workplace_id is required.'})

        try:
            requested_date = datetime.date.fromisoformat(date_str)
        except (ValueError, TypeError):
            raise ValidationError({'date': 'Enter a valid date in YYYY-MM-DD format.'})

        try:
            doctor = User.objects.select_related('doctor_profile').get(
                pk=pk, role='doctor', doctor_profile__is_verified=True
            )
        except User.DoesNotExist:
            raise NotFound()

        try:
            workplace = Workplace.objects.get(pk=workplace_id, doctor=doctor)
        except Workplace.DoesNotExist:
            raise NotFound()

        cache_key = f'slots:{pk}:{workplace_id}:{date_str}'
        cached = cache.get(cache_key)
        if cached is not None:
            return Response({'slots': cached})

        weekday = requested_date.weekday()
        try:
            wh = WorkingHours.objects.get(workplace=workplace, weekday=weekday, is_active=True)
        except WorkingHours.DoesNotExist:
            return Response({'slots': []})

        try:
            slot_duration = doctor.doctor_profile.slot_duration_min
        except Exception:
            slot_duration = 30

        day_start = timezone.make_aware(
            datetime.datetime.combine(requested_date, wh.start_time)
        )
        day_end = timezone.make_aware(
            datetime.datetime.combine(requested_date, wh.end_time)
        )

        windows = []
        current = day_start
        delta = datetime.timedelta(minutes=slot_duration)
        while current + delta <= day_end:
            windows.append((current, current + delta))
            current = current + delta

        blocked = list(BlockedPeriod.objects.filter(
            doctor=doctor,
            starts_at__date__lte=requested_date,
            ends_at__date__gte=requested_date,
        ).filter(Q(workplace=workplace) | Q(workplace__isnull=True)))

        existing = list(Appointment.objects.filter(
            doctor=doctor,
            starts_at__date=requested_date,
        ).exclude(status__in=[Appointment.STATUS_CANCELLED, Appointment.STATUS_DECLINED]))

        free = []
        for w_start, w_end in windows:
            occupied = any(
                bp.starts_at < w_end and bp.ends_at > w_start
                for bp in blocked
            )
            if not occupied:
                occupied = any(
                    appt.starts_at < w_end and appt.ends_at > w_start
                    for appt in existing
                )
            if not occupied:
                free.append({
                    'starts_at': w_start.isoformat(),
                    'ends_at': w_end.isoformat(),
                })

        cache.set(cache_key, free, timeout=300)
        return Response({'slots': free})


class PatientAppointmentListCreateView(APIView):
    permission_classes = [IsPatient]

    def get(self, request):
        qs = (
            Appointment.objects
            .filter(patient=request.user)
            .select_related('doctor', 'doctor__doctor_profile', 'patient', 'workplace')
        )
        status_filter = request.query_params.get('status', '').strip()
        if status_filter:
            qs = qs.filter(status=status_filter)

        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(AppointmentSerializer(page, many=True).data)

    def post(self, request):
        serializer = BookingSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        doctor = serializer.validated_data['_doctor']
        starts_at = serializer.validated_data['starts_at']
        ends_at = serializer.validated_data['_ends_at']

        with transaction.atomic():
            overlap = (
                Appointment.objects
                .select_for_update()
                .filter(doctor=doctor, starts_at__lt=ends_at, ends_at__gt=starts_at)
                .exclude(status__in=[Appointment.STATUS_CANCELLED, Appointment.STATUS_DECLINED])
            )
            if overlap.exists():
                raise ValidationError({'starts_at': 'This slot is no longer available.'})

            appointment = serializer.save()

        cache.delete(
            f'slots:{appointment.doctor_id}:{appointment.workplace_id}'
            f':{appointment.starts_at.date()}'
        )
        return Response(
            AppointmentSerializer(
                Appointment.objects.select_related(
                    'doctor', 'doctor__doctor_profile', 'patient', 'workplace'
                ).get(pk=appointment.pk)
            ).data,
            status=status.HTTP_201_CREATED,
        )


class PatientAppointmentDetailView(APIView):
    permission_classes = [IsPatient]

    def _get(self, pk, patient):
        try:
            return (
                Appointment.objects
                .select_related('doctor', 'doctor__doctor_profile', 'patient', 'workplace')
                .get(pk=pk, patient=patient)
            )
        except Appointment.DoesNotExist:
            raise NotFound()

    def get(self, request, pk):
        return Response(AppointmentSerializer(self._get(pk, request.user)).data)

    def delete(self, request, pk):
        appointment = self._get(pk, request.user)

        if appointment.status not in [Appointment.STATUS_PENDING, Appointment.STATUS_CONFIRMED]:
            return Response(
                {'code': 'conflict', 'message': 'Only pending or confirmed appointments can be cancelled.'},
                status=status.HTTP_409_CONFLICT,
            )
        if appointment.starts_at <= timezone.now() + datetime.timedelta(hours=2):
            return Response(
                {'code': 'conflict', 'message': 'Cannot cancel within 2 hours of appointment.'},
                status=status.HTTP_409_CONFLICT,
            )

        appointment.status = Appointment.STATUS_CANCELLED
        appointment.save(update_fields=['status', 'updated_at'])

        try:
            from apps.notifications.tasks import send_booking_cancelled
            send_booking_cancelled.delay(str(appointment.id))
        except Exception:
            pass

        cache.delete(
            f'slots:{appointment.doctor_id}:{appointment.workplace_id}'
            f':{appointment.starts_at.date()}'
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class DoctorNextSlotView(APIView):
    """Returns the next available date (within 14 days) for a given doctor."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            doctor = User.objects.select_related('doctor_profile').get(
                pk=pk, role='doctor', doctor_profile__is_verified=True
            )
        except User.DoesNotExist:
            raise NotFound()

        try:
            slot_duration = doctor.doctor_profile.slot_duration_min
        except Exception:
            slot_duration = 30

        today = timezone.now().date()
        for days_ahead in range(14):
            check_date = today + datetime.timedelta(days=days_ahead)
            weekday = check_date.weekday()
            wh_qs = WorkingHours.objects.filter(
                workplace__doctor=doctor, weekday=weekday, is_active=True
            )
            if not wh_qs.exists():
                continue

            for wh in wh_qs:
                day_start = timezone.make_aware(
                    datetime.datetime.combine(check_date, wh.start_time)
                )
                day_end = timezone.make_aware(
                    datetime.datetime.combine(check_date, wh.end_time)
                )
                delta = datetime.timedelta(minutes=slot_duration)
                current = max(day_start, timezone.now())

                blocked = list(BlockedPeriod.objects.filter(
                    doctor=doctor,
                    starts_at__date__lte=check_date,
                    ends_at__date__gte=check_date,
                ).filter(Q(workplace=wh.workplace) | Q(workplace__isnull=True)))

                existing = list(Appointment.objects.filter(
                    doctor=doctor,
                    starts_at__date=check_date,
                ).exclude(status__in=[Appointment.STATUS_CANCELLED, Appointment.STATUS_DECLINED]))

                while current + delta <= day_end:
                    w_end = current + delta
                    occupied = any(
                        bp.starts_at < w_end and bp.ends_at > current for bp in blocked
                    ) or any(
                        a.starts_at < w_end and a.ends_at > current for a in existing
                    )
                    if not occupied:
                        return Response({'next_available_date': check_date.isoformat()})
                    current += delta

        return Response({'next_available_date': None})


class PatientAppointmentRescheduleView(APIView):
    permission_classes = [IsPatient]

    def patch(self, request, pk):
        try:
            appointment = (
                Appointment.objects
                .select_related('doctor', 'doctor__doctor_profile', 'patient', 'workplace')
                .get(pk=pk, patient=request.user)
            )
        except Appointment.DoesNotExist:
            raise NotFound()

        if appointment.status not in [Appointment.STATUS_PENDING, Appointment.STATUS_CONFIRMED]:
            return Response(
                {'code': 'conflict', 'message': 'Only pending or confirmed appointments can be rescheduled.'},
                status=status.HTTP_409_CONFLICT,
            )
        if appointment.starts_at <= timezone.now() + datetime.timedelta(hours=2):
            return Response(
                {'code': 'conflict', 'message': 'Cannot reschedule within 2 hours of appointment.'},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = RescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_starts_at = serializer.validated_data['starts_at']

        try:
            slot_duration = appointment.doctor.doctor_profile.slot_duration_min
        except Exception:
            slot_duration = 30
        new_ends_at = new_starts_at + datetime.timedelta(minutes=slot_duration)

        with transaction.atomic():
            overlap = (
                Appointment.objects
                .select_for_update()
                .filter(doctor=appointment.doctor, starts_at__lt=new_ends_at, ends_at__gt=new_starts_at)
                .exclude(pk=appointment.pk)
                .exclude(status__in=[Appointment.STATUS_CANCELLED, Appointment.STATUS_DECLINED])
            )
            if overlap.exists():
                raise ValidationError({'starts_at': 'This slot is no longer available.'})

            old_date = appointment.starts_at.date()
            appointment.starts_at = new_starts_at
            appointment.ends_at = new_ends_at
            appointment.status = Appointment.STATUS_PENDING
            appointment.save(update_fields=['starts_at', 'ends_at', 'status', 'updated_at'])

        cache.delete(f'slots:{appointment.doctor_id}:{appointment.workplace_id}:{old_date}')
        cache.delete(f'slots:{appointment.doctor_id}:{appointment.workplace_id}:{new_starts_at.date()}')

        try:
            from apps.notifications.tasks import send_appointment_rescheduled
            send_appointment_rescheduled.delay(str(appointment.id))
        except Exception:
            pass

        return Response(AppointmentSerializer(appointment).data)


class DoctorAppointmentListView(APIView):
    permission_classes = [IsDoctor]

    def get(self, request):
        qs = (
            Appointment.objects
            .filter(doctor=request.user)
            .select_related('doctor', 'doctor__doctor_profile', 'patient', 'workplace')
        )
        status_filter = request.query_params.get('status', '').strip()
        date_filter = request.query_params.get('date', '').strip()
        workplace_filter = request.query_params.get('workplace_id', '').strip()

        if status_filter:
            qs = qs.filter(status=status_filter)
        if date_filter:
            try:
                d = datetime.date.fromisoformat(date_filter)
                qs = qs.filter(starts_at__date=d)
            except ValueError:
                raise ValidationError({'date': 'Enter a valid date in YYYY-MM-DD format.'})
        if workplace_filter:
            qs = qs.filter(workplace_id=workplace_filter)

        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(AppointmentSerializer(page, many=True).data)


class DoctorAppointmentDetailView(APIView):
    permission_classes = [IsDoctor]

    def get(self, request, pk):
        try:
            appointment = (
                Appointment.objects
                .select_related('doctor', 'doctor__doctor_profile', 'patient', 'workplace')
                .get(pk=pk, doctor=request.user)
            )
        except Appointment.DoesNotExist:
            raise NotFound()
        return Response(AppointmentSerializer(appointment).data)


class DoctorAppointmentStatusView(APIView):
    permission_classes = [IsDoctor]

    def patch(self, request, pk):
        try:
            appointment = (
                Appointment.objects
                .select_related('doctor', 'doctor__doctor_profile', 'patient', 'workplace')
                .get(pk=pk, doctor=request.user)
            )
        except Appointment.DoesNotExist:
            raise NotFound()

        if appointment.status != Appointment.STATUS_PENDING:
            return Response(
                {'code': 'conflict', 'message': 'Only pending appointments can be confirmed or declined.'},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = AppointmentStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data['status']

        appointment.status = new_status
        appointment.save(update_fields=['status', 'updated_at'])

        try:
            from apps.notifications.tasks import send_booking_confirmed, send_booking_cancelled
            if new_status == Appointment.STATUS_CONFIRMED:
                send_booking_confirmed.delay(str(appointment.id))
            else:
                send_booking_cancelled.delay(str(appointment.id))
        except Exception:
            pass

        return Response(AppointmentSerializer(appointment).data)


class DoctorAppointmentNotesView(APIView):
    permission_classes = [IsDoctor]

    def patch(self, request, pk):
        try:
            appointment = (
                Appointment.objects
                .select_related('doctor', 'doctor__doctor_profile', 'patient', 'workplace')
                .get(pk=pk, doctor=request.user)
            )
        except Appointment.DoesNotExist:
            raise NotFound()

        serializer = DoctorNotesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        appointment.notes = serializer.validated_data['notes']
        appointment.save(update_fields=['notes', 'updated_at'])
        return Response(AppointmentSerializer(appointment).data)


class AppointmentReviewView(APIView):
    permission_classes = [IsPatient]

    def post(self, request, pk):
        try:
            appointment = Appointment.objects.select_related('doctor', 'patient').get(
                pk=pk, patient=request.user
            )
        except Appointment.DoesNotExist:
            raise NotFound()

        serializer = ReviewCreateSerializer(
            data=request.data,
            context={'appointment': appointment, 'request': request},
        )
        serializer.is_valid(raise_exception=True)
        review = serializer.save()
        return Response(ReviewSerializer(review).data, status=status.HTTP_201_CREATED)


class DoctorReviewListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            doctor = User.objects.get(pk=pk, role=User.ROLE_DOCTOR)
        except User.DoesNotExist:
            raise NotFound()

        reviews = Review.objects.filter(doctor=doctor).select_related('patient')
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(reviews, request)
        return paginator.get_paginated_response(ReviewSerializer(page, many=True).data)
