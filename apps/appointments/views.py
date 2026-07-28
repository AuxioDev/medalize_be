import datetime
import logging

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import (
    Avg, Case, Count, ExpressionWrapper, F, FloatField, Max, OuterRef, Q, Subquery, Value, When,
)
from django.db.models.functions import ACos, Cos, Greatest, Least, Radians, Sin
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

from apps.core.i18n import resolve_city_key
from apps.doctors.models import BlockedPeriod, Workplace, WorkingHours
from apps.subscriptions.entitlements import entitled_doctor_filter, limits_for, promoted_rank_case
from apps.users.permissions import IsDoctor, IsPatient
from .models import Appointment, CANCELLATION_WINDOW_HOURS, Favorite, Review, Waitlist
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
    ReviewUpdateSerializer,
)

User = get_user_model()


_NEXT_SLOT_CACHE_MISS = object()


def find_next_slot_at(doctor):
    """First free slot start (aware datetime) within 14 days, or None.

    Shared by DoctorNextSlotView and DoctorListView (ordering=next_slot).
    Cached the same way SlotListView caches a single day's slots (300s TTL,
    invalidated on every booking/cancel/decline/reschedule) — this used to
    recompute uncached on every call, up to 20x per doctor-list page load.
    A sentinel (not None) marks a cache miss, since "no slot in 14 days" is
    itself a valid, legitimately-cacheable result.
    """
    cache_key = f'next_slot:{doctor.id}'
    cached = cache.get(cache_key, _NEXT_SLOT_CACHE_MISS)
    if cached is not _NEXT_SLOT_CACHE_MISS:
        return cached
    result = _compute_next_slot_at(doctor)
    cache.set(cache_key, result, timeout=300)
    return result


def _compute_next_slot_at(doctor):
    """Pre-fetches all data for the 14-day window in 3 queries instead of
    running up to 42 individual queries (one per day × 3 models)."""
    try:
        slot_duration = doctor.doctor_profile.slot_duration_min
    except Exception:
        slot_duration = 30

    now = timezone.now()
    today = now.date()
    end_date = today + datetime.timedelta(days=13)

    all_wh = list(
        WorkingHours.objects
        .filter(workplace__doctor=doctor, is_active=True)
        .select_related('workplace')
    )
    all_blocked = list(BlockedPeriod.objects.filter(
        doctor=doctor,
        starts_at__date__lte=end_date,
        ends_at__date__gte=today,
    ))
    all_existing = list(Appointment.objects.filter(
        doctor=doctor,
        starts_at__date__range=(today, end_date),
    ).exclude(status__in=[Appointment.STATUS_CANCELLED, Appointment.STATUS_DECLINED]))

    delta = datetime.timedelta(minutes=slot_duration)

    for days_ahead in range(14):
        check_date = today + datetime.timedelta(days=days_ahead)
        weekday = check_date.weekday()

        day_whs = [wh for wh in all_wh if wh.weekday == weekday]
        if not day_whs:
            continue

        day_existing = [a for a in all_existing if a.starts_at.date() == check_date]
        day_blocked_all = [
            bp for bp in all_blocked
            if bp.starts_at.date() <= check_date <= bp.ends_at.date()
        ]

        for wh in day_whs:
            day_start = timezone.make_aware(
                datetime.datetime.combine(check_date, wh.start_time)
            )
            day_end = timezone.make_aware(
                datetime.datetime.combine(check_date, wh.end_time)
            )
            current = max(day_start, now)
            wh_blocked = [
                bp for bp in day_blocked_all
                if bp.workplace_id is None or bp.workplace_id == wh.workplace_id
            ]

            while current + delta <= day_end:
                w_end = current + delta
                occupied = any(
                    bp.starts_at < w_end and bp.ends_at > current for bp in wh_blocked
                ) or any(
                    a.starts_at < w_end and a.ends_at > current for a in day_existing
                )
                if not occupied:
                    return current
                current += delta

    return None


class DoctorListView(APIView):
    permission_classes = [IsAuthenticated]

    ORDERING_CHOICES = ('rating', '-rating', 'next_slot', 'distance')

    def get(self, request):
        qs = (
            User.objects
            .filter(role=User.ROLE_DOCTOR, is_active=True, doctor_profile__is_verified=True)
            .filter(**entitled_doctor_filter())
            .select_related('doctor_profile', 'subscription')
            .prefetch_related('workplaces')
            .annotate(
                avg_rating=Avg('doctor_reviews__rating'),
                total_reviews=Count('doctor_reviews', distinct=True),
                promo_rank=promoted_rank_case(),
            )
            .order_by('promo_rank', 'first_name', 'last_name', 'id')
        )
        name = request.query_params.get('name', '').strip()
        specialization = request.query_params.get('specialization', '').strip()
        city = request.query_params.get('city', '').strip()
        region = request.query_params.get('region', '').strip()
        min_rating = request.query_params.get('min_rating', '').strip()
        ordering = request.query_params.get('ordering', '').strip()

        if name:
            qs = qs.filter(Q(first_name__icontains=name) | Q(last_name__icontains=name))
        if specialization:
            qs = qs.filter(doctor_profile__specialization=specialization)
        if city:
            # Accepts a canonical key or free text in any of the 6 registry
            # languages/aliases — see apps.core.i18n.resolve_city_key. An
            # unresolvable value is treated as "no such city" (empty result,
            # not a 400) to match the soft-fail behavior of min_rating below.
            city_key = resolve_city_key(city)
            qs = qs.filter(workplaces__city=city_key).distinct() if city_key else qs.none()
        if region:
            qs = qs.filter(workplaces__region=region).distinct()
        if min_rating:
            try:
                min_rating_val = float(min_rating)
                qs = qs.filter(avg_rating__gte=min_rating_val)
            except ValueError:
                pass

        lat, lng = self._parse_coordinates(request, required=ordering == 'distance')
        if lat is not None and lng is not None:
            qs = self._annotate_distance(qs, lat, lng)

        if ordering in ('rating', '-rating'):
            direction = F('avg_rating').desc if ordering == '-rating' else F('avg_rating').asc
            qs = qs.order_by('promo_rank', direction(nulls_last=True), 'first_name', 'last_name', 'id')
        elif ordering == 'distance':
            qs = qs.order_by(
                'promo_rank', F('distance_km').asc(nulls_last=True), 'first_name', 'last_name', 'id'
            )

        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(qs, request)

        if ordering == 'next_slot':
            # Deliberate approximation: next-slot is computed and re-sorted only
            # within the already-paginated page, never for the whole table.
            for doctor in page:
                doctor.next_slot_at = find_next_slot_at(doctor)
            page = sorted(page, key=lambda d: (d.next_slot_at is None, d.next_slot_at))

        return paginator.get_paginated_response(
            DoctorPublicSerializer(page, many=True, context={'request': request}).data
        )

    def _parse_coordinates(self, request, required):
        lat = request.query_params.get('lat', '').strip()
        lng = request.query_params.get('lng', '').strip()
        if not lat and not lng and not required:
            return None, None
        try:
            return float(lat), float(lng)
        except ValueError:
            if required:
                raise ValidationError({
                    'lat': 'ordering=distance requires valid lat and lng query parameters.'
                })
            return None, None

    @staticmethod
    def _annotate_distance(qs, lat, lng):
        # Haversine on the primary workplace via plain ORM functions (no PostGIS).
        # Workplace.Meta.ordering puts is_primary first, matching
        # DoctorPublicSerializer.get_primary_workplace.
        primary_wp = Workplace.objects.filter(doctor=OuterRef('pk')).order_by('-is_primary', 'name')
        qs = qs.annotate(
            wp_lat=Subquery(primary_wp.values('latitude')[:1], output_field=FloatField()),
            wp_lng=Subquery(primary_wp.values('longitude')[:1], output_field=FloatField()),
        )
        lat_r = Radians(Value(lat, output_field=FloatField()))
        lng_r = Radians(Value(lng, output_field=FloatField()))
        # Clamp into [-1, 1] so float rounding can't push the value outside
        # ACos's domain and crash the query.
        cos_angle = Least(Value(1.0), Greatest(Value(-1.0),
            Cos(lat_r) * Cos(Radians(F('wp_lat')))
            * Cos(Radians(F('wp_lng')) - lng_r)
            + Sin(lat_r) * Sin(Radians(F('wp_lat')))
        ))
        # GREATEST/LEAST ignore NULLs in PostgreSQL, so missing coordinates
        # would silently clamp to -1 instead of yielding NULL — keep them NULL
        # explicitly so those doctors sort last.
        return qs.annotate(
            distance_km=Case(
                When(Q(wp_lat__isnull=True) | Q(wp_lng__isnull=True), then=Value(None)),
                default=ExpressionWrapper(
                    Value(6371.0) * ACos(cos_angle), output_field=FloatField()
                ),
                output_field=FloatField(),
            )
        )


class DoctorDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            doctor = (
                User.objects
                .filter(role='doctor', is_active=True, doctor_profile__is_verified=True)
                .filter(**entitled_doctor_filter())
                .select_related('doctor_profile', 'subscription')
                .prefetch_related('workplaces__working_hours')
                .get(pk=pk)
            )
        except User.DoesNotExist:
            raise NotFound()
        return Response(DoctorDetailSerializer(doctor, context={'request': request}).data)


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
                pk=pk, role='doctor', is_active=True, doctor_profile__is_verified=True,
                **entitled_doctor_filter(),
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

        now = timezone.now()
        free = []
        for w_start, w_end in windows:
            # Drop already-passed slots when querying today
            if requested_date == now.date() and w_start <= now:
                continue
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

    def get_throttles(self):
        # Only scope-throttle POST (booking a slot) — GET is the patient's
        # own appointment list, fetched routinely by the app, and must stay
        # under the generic `user` rate, not the tighter booking budget.
        if self.request.method == 'POST':
            self.throttle_scope = 'appointment_book'
        return super().get_throttles()

    def get(self, request):
        qs = (
            Appointment.objects
            .filter(patient=request.user)
            .select_related('doctor', 'doctor__doctor_profile', 'patient', 'dependent', 'workplace')
            .order_by('-starts_at')
        )
        status_filter = request.query_params.get('status', '').strip()
        if status_filter:
            qs = qs.filter(status=status_filter)

        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            AppointmentSerializer(page, many=True, context={'request': request}).data
        )

    def post(self, request):
        serializer = BookingSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        doctor = serializer.validated_data['_doctor']
        starts_at = serializer.validated_data['starts_at']
        ends_at = serializer.validated_data['_ends_at']

        try:
            with transaction.atomic():
                # Lock the doctor row so concurrent bookings for the same doctor
                # serialize. select_for_update on the (possibly empty) overlap
                # query alone locks nothing under READ COMMITTED and would let two
                # requests both pass the check and insert the same slot.
                User.objects.select_for_update().get(pk=doctor.pk)
                overlap = (
                    Appointment.objects
                    .filter(doctor=doctor, starts_at__lt=ends_at, ends_at__gt=starts_at)
                    .exclude(status__in=[Appointment.STATUS_CANCELLED, Appointment.STATUS_DECLINED])
                )
                if overlap.exists():
                    raise ValidationError({'starts_at': 'This slot is no longer available.'})

                appointment = serializer.save()
        except IntegrityError:
            # DB exclusion constraint backstop (appt_no_overlap_per_doctor).
            raise ValidationError({'starts_at': 'This slot is no longer available.'})

        # Remove from waitlist — patient found a slot, no longer needs to wait.
        Waitlist.objects.filter(
            patient=appointment.patient, doctor=appointment.doctor
        ).delete()

        cache.delete(
            f'slots:{appointment.doctor_id}:{appointment.workplace_id}'
            f':{appointment.starts_at.date()}'
        )
        cache.delete(f'next_slot:{appointment.doctor_id}')
        try:
            from apps.notifications.tasks import send_new_booking_pending
            send_new_booking_pending.delay(str(appointment.pk))
        except Exception:
            logger.exception('Failed to enqueue booking notification for appointment %s', appointment.pk)
        return Response(
            AppointmentSerializer(
                Appointment.objects.select_related(
                    'doctor', 'doctor__doctor_profile', 'patient', 'dependent', 'workplace'
                ).get(pk=appointment.pk),
                context={'request': request},
            ).data,
            status=status.HTTP_201_CREATED,
        )


class PatientAppointmentDetailView(APIView):
    permission_classes = [IsPatient]

    def get_throttles(self):
        # Only scope-throttle DELETE (cancelling) — GET (viewing a single
        # appointment's detail) stays under the generic `user` rate.
        if self.request.method == 'DELETE':
            self.throttle_scope = 'appointment_mutate'
        return super().get_throttles()

    def _get(self, pk, patient):
        try:
            return (
                Appointment.objects
                .select_related('doctor', 'doctor__doctor_profile', 'patient', 'dependent', 'workplace')
                .get(pk=pk, patient=patient)
            )
        except Appointment.DoesNotExist:
            raise NotFound()

    def get(self, request, pk):
        return Response(
            AppointmentSerializer(self._get(pk, request.user), context={'request': request}).data
        )

    def delete(self, request, pk):
        appointment = self._get(pk, request.user)

        if appointment.status not in [Appointment.STATUS_PENDING, Appointment.STATUS_CONFIRMED]:
            return Response(
                {'code': 'conflict', 'message': 'Only pending or confirmed appointments can be cancelled.'},
                status=status.HTTP_409_CONFLICT,
            )
        window_hours = getattr(
            appointment.doctor.doctor_profile, 'cancellation_window_hours',
            CANCELLATION_WINDOW_HOURS,
        )
        if appointment.starts_at <= timezone.now() + datetime.timedelta(hours=window_hours):
            return Response(
                {'code': 'conflict',
                 'message': f'Cannot cancel within {window_hours} hours of appointment.'},
                status=status.HTTP_409_CONFLICT,
            )

        appointment.status = Appointment.STATUS_CANCELLED
        appointment.save(update_fields=['status', 'updated_at'])

        try:
            from apps.notifications.tasks import send_booking_cancelled
            send_booking_cancelled.delay(str(appointment.id))
        except Exception:
            logger.exception('Failed to enqueue cancellation notification for appointment %s', appointment.id)

        cache.delete(
            f'slots:{appointment.doctor_id}:{appointment.workplace_id}'
            f':{appointment.starts_at.date()}'
        )
        cache.delete(f'next_slot:{appointment.doctor_id}')
        return Response(status=status.HTTP_204_NO_CONTENT)


class DoctorNextSlotView(APIView):
    """Returns the next available date (within 14 days) for a given doctor."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            doctor = User.objects.select_related('doctor_profile').get(
                pk=pk, role='doctor', is_active=True, doctor_profile__is_verified=True,
                **entitled_doctor_filter(),
            )
        except User.DoesNotExist:
            raise NotFound()

        next_slot = find_next_slot_at(doctor)
        return Response({
            'next_available_date':
                timezone.localtime(next_slot).date().isoformat() if next_slot else None,
        })


class PatientAppointmentRescheduleView(APIView):
    permission_classes = [IsPatient]
    throttle_scope = 'appointment_mutate'

    def patch(self, request, pk):
        try:
            appointment = (
                Appointment.objects
                .select_related('doctor', 'doctor__doctor_profile', 'patient', 'dependent', 'workplace')
                .get(pk=pk, patient=request.user)
            )
        except Appointment.DoesNotExist:
            raise NotFound()

        if appointment.status not in [
            Appointment.STATUS_PENDING,
            Appointment.STATUS_CONFIRMED,
            Appointment.STATUS_REQUIRES_RESCHEDULING,
        ]:
            return Response(
                {'code': 'conflict', 'message': 'This appointment can no longer be rescheduled.'},
                status=status.HTTP_409_CONFLICT,
            )
        # The 2-hour cutoff guards the doctor's schedule against last-minute
        # patient changes — but it must not block a move the doctor explicitly
        # requested (requires_rescheduling).
        window_hours = getattr(
            appointment.doctor.doctor_profile, 'cancellation_window_hours',
            CANCELLATION_WINDOW_HOURS,
        )
        if (appointment.status != Appointment.STATUS_REQUIRES_RESCHEDULING
                and appointment.starts_at <= timezone.now() + datetime.timedelta(hours=window_hours)):
            return Response(
                {'code': 'conflict',
                 'message': f'Cannot reschedule within {window_hours} hours of appointment.'},
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

        # Validate against the doctor's working hours for the new slot.
        local_starts = timezone.localtime(new_starts_at)
        weekday = local_starts.weekday()
        try:
            wh = WorkingHours.objects.get(
                workplace=appointment.workplace, weekday=weekday, is_active=True
            )
        except WorkingHours.DoesNotExist:
            raise ValidationError(
                {'starts_at': 'The doctor does not work on this day at this location.'}
            )
        day_start = timezone.make_aware(
            datetime.datetime.combine(local_starts.date(), wh.start_time)
        )
        day_end = timezone.make_aware(
            datetime.datetime.combine(local_starts.date(), wh.end_time)
        )
        if not (day_start <= new_starts_at and new_ends_at <= day_end):
            raise ValidationError(
                {'starts_at': "This slot is outside the doctor's working hours."}
            )

        # Require the new start to align to the doctor's slot grid.
        offset_minutes = (new_starts_at - day_start).total_seconds() / 60
        if offset_minutes < 0 or offset_minutes % slot_duration != 0:
            raise ValidationError(
                {'starts_at': 'Selected time is not a valid appointment slot.'}
            )

        # Validate against blocked periods.
        if BlockedPeriod.objects.filter(
            doctor=appointment.doctor,
            starts_at__lt=new_ends_at,
            ends_at__gt=new_starts_at,
        ).filter(Q(workplace=appointment.workplace) | Q(workplace__isnull=True)).exists():
            raise ValidationError({'starts_at': 'This slot is not available.'})

        try:
            with transaction.atomic():
                # Lock the doctor row so this move serializes against concurrent
                # bookings/reschedules for the same doctor (see booking view).
                User.objects.select_for_update().get(pk=appointment.doctor_id)
                overlap = (
                    Appointment.objects
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
        except IntegrityError:
            raise ValidationError({'starts_at': 'This slot is no longer available.'})

        cache.delete(f'slots:{appointment.doctor_id}:{appointment.workplace_id}:{old_date}')
        cache.delete(f'slots:{appointment.doctor_id}:{appointment.workplace_id}:{new_starts_at.date()}')
        cache.delete(f'next_slot:{appointment.doctor_id}')

        try:
            from apps.notifications.tasks import (
                send_appointment_rescheduled, notify_waitlist_slot_available,
            )
            send_appointment_rescheduled.delay(str(appointment.id))
            # Old slot is now free — notify waitlist patients.
            notify_waitlist_slot_available.delay(str(appointment.doctor_id))
        except Exception:
            logger.exception('Failed to enqueue reschedule notification for appointment %s', appointment.id)

        return Response(AppointmentSerializer(appointment, context={'request': request}).data)


class DoctorAppointmentListView(APIView):
    permission_classes = [IsDoctor]

    def get(self, request):
        qs = (
            Appointment.objects
            .filter(doctor=request.user)
            .select_related('doctor', 'doctor__doctor_profile', 'patient', 'dependent', 'workplace')
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
        return paginator.get_paginated_response(
            AppointmentSerializer(page, many=True, context={'request': request}).data
        )


class DoctorAppointmentDetailView(APIView):
    permission_classes = [IsDoctor]

    def get(self, request, pk):
        try:
            appointment = (
                Appointment.objects
                .select_related('doctor', 'doctor__doctor_profile', 'patient', 'dependent', 'workplace')
                .get(pk=pk, doctor=request.user)
            )
        except Appointment.DoesNotExist:
            raise NotFound()
        return Response(AppointmentSerializer(appointment, context={'request': request}).data)


class DoctorAppointmentStatusView(APIView):
    permission_classes = [IsDoctor]

    def patch(self, request, pk):
        try:
            appointment = (
                Appointment.objects
                .select_related('doctor', 'doctor__doctor_profile', 'patient', 'dependent', 'workplace')
                .get(pk=pk, doctor=request.user)
            )
        except Appointment.DoesNotExist:
            raise NotFound()

        serializer = AppointmentStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data['status']

        if new_status in (Appointment.STATUS_CONFIRMED, Appointment.STATUS_DECLINED):
            if appointment.status != Appointment.STATUS_PENDING:
                return Response(
                    {'code': 'conflict', 'message': 'Only pending appointments can be confirmed or declined.'},
                    status=status.HTTP_409_CONFLICT,
                )
        elif new_status == Appointment.STATUS_CANCELLED:
            if appointment.status not in (Appointment.STATUS_PENDING, Appointment.STATUS_CONFIRMED):
                return Response(
                    {'code': 'conflict', 'message': 'Only pending or confirmed appointments can be cancelled.'},
                    status=status.HTTP_409_CONFLICT,
                )
        elif new_status == Appointment.STATUS_COMPLETED:
            if appointment.status != Appointment.STATUS_CONFIRMED:
                return Response(
                    {'code': 'conflict', 'message': 'Only confirmed appointments can be marked as completed.'},
                    status=status.HTTP_409_CONFLICT,
                )
            # A prescription can only be issued for a completed appointment
            # (see apps.prescriptions), so this is also the gate that keeps a
            # doctor from completing — and then prescribing against — a visit
            # that hasn't actually started yet.
            if timezone.now() < appointment.starts_at:
                return Response(
                    {
                        'code': 'conflict',
                        'message': 'Cannot mark an appointment as completed before it has started.',
                    },
                    status=status.HTTP_409_CONFLICT,
                )
        elif new_status == Appointment.STATUS_REQUIRES_RESCHEDULING:
            # Doctor asks the patient to pick a new time for an upcoming appt.
            if appointment.status != Appointment.STATUS_CONFIRMED:
                return Response(
                    {'code': 'conflict', 'message': 'Only confirmed appointments can be marked for rescheduling.'},
                    status=status.HTTP_409_CONFLICT,
                )
        elif new_status == Appointment.STATUS_NO_SHOW:
            if appointment.status != Appointment.STATUS_CONFIRMED:
                return Response(
                    {'code': 'conflict', 'message': 'Only confirmed appointments can be marked as no-show.'},
                    status=status.HTTP_409_CONFLICT,
                )

        appointment.status = new_status
        appointment.save(update_fields=['status', 'updated_at'])

        # DECLINED frees the slot exactly like CANCELLED does (both are
        # excluded from occupancy checks everywhere) — the cache must be
        # invalidated on both, not just CANCELLED, or a declined slot stays
        # marked unavailable for up to 5 minutes after it actually freed up.
        if new_status in (Appointment.STATUS_CANCELLED, Appointment.STATUS_DECLINED):
            cache.delete(
                f'slots:{appointment.doctor_id}:{appointment.workplace_id}'
                f':{appointment.starts_at.date()}'
            )
            cache.delete(f'next_slot:{appointment.doctor_id}')

        try:
            from apps.notifications.tasks import (
                send_booking_confirmed, send_booking_cancelled, send_booking_declined,
                send_booking_no_show, send_appointment_completed, send_rescheduling_required,
            )
            if new_status == Appointment.STATUS_CONFIRMED:
                send_booking_confirmed.delay(str(appointment.id))
            elif new_status == Appointment.STATUS_DECLINED:
                send_booking_declined.delay(str(appointment.id))
            elif new_status == Appointment.STATUS_CANCELLED:
                send_booking_cancelled.delay(str(appointment.id))
            elif new_status == Appointment.STATUS_COMPLETED:
                send_appointment_completed.delay(str(appointment.id))
            elif new_status == Appointment.STATUS_REQUIRES_RESCHEDULING:
                send_rescheduling_required.delay(str(appointment.id))
            elif new_status == Appointment.STATUS_NO_SHOW:
                send_booking_no_show.delay(str(appointment.id))
        except Exception:
            logger.exception('Failed to enqueue status notification for appointment %s', appointment.id)

        return Response(AppointmentSerializer(appointment, context={'request': request}).data)


class DoctorAppointmentNotesView(APIView):
    permission_classes = [IsDoctor]

    def patch(self, request, pk):
        try:
            appointment = (
                Appointment.objects
                .select_related('doctor', 'doctor__doctor_profile', 'patient', 'dependent', 'workplace')
                .get(pk=pk, doctor=request.user)
            )
        except Appointment.DoesNotExist:
            raise NotFound()

        serializer = DoctorNotesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        appointment.notes = serializer.validated_data['notes']
        appointment.save(update_fields=['notes', 'updated_at'])
        return Response(AppointmentSerializer(appointment, context={'request': request}).data)


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

    def _get_own_review(self, request, pk):
        """The current patient's review on their own appointment, or 404."""
        try:
            appointment = Appointment.objects.select_related('review').get(
                pk=pk, patient=request.user
            )
        except Appointment.DoesNotExist:
            raise NotFound()
        try:
            return appointment.review
        except Review.DoesNotExist:
            raise NotFound()

    def patch(self, request, pk):
        review = self._get_own_review(request, pk)
        serializer = ReviewUpdateSerializer(
            data=request.data,
            context={'review': review, 'request': request},
        )
        serializer.is_valid(raise_exception=True)
        review.rating = serializer.validated_data['rating']
        review.comment = serializer.validated_data.get('comment', review.comment)
        review.save(update_fields=['rating', 'comment', 'updated_at'])
        return Response(ReviewSerializer(review).data)

    def delete(self, request, pk):
        # Deletion is deliberately not time-limited, unlike editing.
        review = self._get_own_review(request, pk)
        review.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


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


class WaitlistView(APIView):
    permission_classes = [IsPatient]

    def get(self, request):
        entries = Waitlist.objects.filter(patient=request.user).select_related('doctor')
        data = [
            {
                'id': str(e.id),
                'doctor_id': str(e.doctor_id),
                'doctor_name': f'{e.doctor.first_name} {e.doctor.last_name}'.strip() or e.doctor.email,
                'joined_at': e.created_at.isoformat(),
            }
            for e in entries
        ]
        return Response(data)

    def post(self, request):
        doctor_id = request.data.get('doctor_id')
        if not doctor_id:
            raise ValidationError({'doctor_id': 'This field is required.'})
        try:
            doctor = User.objects.get(
                pk=doctor_id, role=User.ROLE_DOCTOR, is_active=True, doctor_profile__is_verified=True
            )
        except (User.DoesNotExist, ValueError):
            raise NotFound('Doctor not found.')
        entry, created = Waitlist.objects.get_or_create(patient=request.user, doctor=doctor)
        if not created:
            return Response({'detail': 'Already on waitlist.'}, status=status.HTTP_200_OK)
        return Response({'id': str(entry.id), 'doctor_id': str(doctor.id)}, status=status.HTTP_201_CREATED)


class WaitlistDetailView(APIView):
    permission_classes = [IsPatient]

    def delete(self, request, pk):
        try:
            entry = Waitlist.objects.get(pk=pk, patient=request.user)
        except Waitlist.DoesNotExist:
            raise NotFound()
        entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class FavoriteListCreateView(APIView):
    permission_classes = [IsPatient]

    def get(self, request):
        # Full doctor cards (not just IDs) so the client can render the
        # favorites list without extra per-doctor requests.
        doctors = (
            User.objects
            .filter(role=User.ROLE_DOCTOR, favorited_by__patient=request.user)
            .select_related('doctor_profile', 'subscription')
            .prefetch_related('workplaces')
            .annotate(
                avg_rating=Avg('doctor_reviews__rating'),
                total_reviews=Count('doctor_reviews', distinct=True),
                favorited_at=Max('favorited_by__created_at'),
            )
            .order_by('-favorited_at')
        )
        return Response(
            DoctorPublicSerializer(doctors, many=True, context={'request': request}).data
        )

    def post(self, request):
        doctor_id = request.data.get('doctor_id')
        if not doctor_id:
            raise ValidationError({'doctor_id': 'This field is required.'})
        try:
            doctor = User.objects.get(
                pk=doctor_id, role=User.ROLE_DOCTOR, is_active=True, doctor_profile__is_verified=True
            )
        except (User.DoesNotExist, ValueError):
            raise NotFound('Doctor not found.')
        favorite, created = Favorite.objects.get_or_create(patient=request.user, doctor=doctor)
        return Response(
            {
                'id': str(favorite.id),
                'doctor_id': str(doctor.id),
                'created_at': favorite.created_at.isoformat(),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class FavoriteDetailView(APIView):
    permission_classes = [IsPatient]

    def delete(self, request, doctor_id):
        try:
            favorite = Favorite.objects.get(patient=request.user, doctor_id=doctor_id)
        except Favorite.DoesNotExist:
            raise NotFound()
        favorite.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DoctorStatsView(APIView):
    permission_classes = [IsDoctor]

    def get(self, request):
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month_end = month_start
        last_month_start = (month_start - datetime.timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        qs = Appointment.objects.filter(doctor=request.user)

        this_month = qs.filter(starts_at__gte=month_start).count()
        last_month = qs.filter(starts_at__gte=last_month_start, starts_at__lt=last_month_end).count()
        pending = qs.filter(status=Appointment.STATUS_PENDING).count()
        total_patients = qs.values('patient').distinct().count()

        decided = qs.filter(
            updated_at__gte=month_start,
            status__in=[Appointment.STATUS_CONFIRMED, Appointment.STATUS_DECLINED],
        )
        decided_count = decided.count()
        confirmed_count = decided.filter(status=Appointment.STATUS_CONFIRMED).count()
        acceptance_rate = round(confirmed_count / decided_count * 100) if decided_count else None

        data = {
            'appointments_this_month': this_month,
            'pending_count': pending,
        }
        # Başlanğıc gets the two numbers above only; the rest is a Peşəkar/
        # trial perk (see apps.subscriptions.plans.PLAN_LIMITS['advanced_stats']).
        if limits_for(request.user)['advanced_stats']:
            data.update({
                'appointments_last_month': last_month,
                'total_patients': total_patients,
                'acceptance_rate': acceptance_rate,
            })
        return Response(data)
