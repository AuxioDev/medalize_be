import datetime
import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.appointments.models import Appointment
from apps.doctors.models import Workplace
from apps.doctors.serializers import WorkplaceSerializer
from apps.doctors.services import (
    full_week_hours,
    invalidate_doctor_slots,
    replace_working_hours,
    validated_hours_items,
)
from apps.users.models import User
from apps.users.permissions import IsDoctorVerified

from . import services as hospital_services
from .matching import find_duplicates
from .models import Hospital, HospitalDoctorLink
from .permissions import IsHospital, IsHospitalApproved, IsHospitalSubscribed
from .serializers import (
    DoctorBriefSerializer,
    DoctorHospitalLinkSerializer,
    HospitalAppointmentSerializer,
    HospitalCreateSerializer,
    HospitalLinkSerializer,
    HospitalProfileSerializer,
    HospitalRegistrySerializer,
)


def _parse_date_param(value, name):
    if not value:
        return None
    try:
        datetime.date.fromisoformat(value)
        return value
    except (ValueError, TypeError):
        raise ValidationError({name: 'Enter a valid date in YYYY-MM-DD format.'})


def _parse_uuid_param(value, name):
    if not value:
        return None
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ValidationError({name: 'Enter a valid id.'})
    return value


def _get_own_hospital(request):
    """IsHospital (unlike IsHospitalApproved/IsHospitalSubscribed) only
    checks role, not that a Hospital row is actually attached — by design,
    every 'hospital'-role User gets one attached at registration (see
    apps.users.serializers.RegisterSerializer's claim-or-create branch), so
    this should be unreachable in practice. Defensive 404 instead of a
    500 RelatedObjectDoesNotExist if that invariant is ever violated (a
    data-migration mistake, manual DB surgery, etc.)."""
    hospital = getattr(request.user, 'hospital', None)
    if hospital is None:
        raise NotFound()
    return hospital


def _get_hospital_link(hospital, link_id):
    try:
        return (
            HospitalDoctorLink.objects
            .select_related('doctor', 'doctor__doctor_profile', 'hospital')
            .get(pk=link_id, hospital=hospital)
        )
    except (HospitalDoctorLink.DoesNotExist, DjangoValidationError, ValueError):
        raise NotFound()


def _get_confirmed_workplace(hospital, workplace_id):
    """Resolves a workplace scoped to this hospital's dashboard. Checks BOTH
    the Workplace.hospital FK AND a currently-CONFIRMED HospitalDoctorLink —
    the FK alone is set by the doctor at *request* time, before any
    confirmation, so trusting it alone would let a hospital rewrite the
    schedule of a doctor who merely asked to join (and never agreed to
    anything). 404, not 403, on a miss — matches apps.doctors.views.
    _get_workplace's posture for an out-of-scope object."""
    try:
        workplace = Workplace.objects.select_related('doctor').get(pk=workplace_id, hospital=hospital)
    except (Workplace.DoesNotExist, DjangoValidationError, ValueError):
        raise NotFound()
    is_confirmed = HospitalDoctorLink.objects.filter(
        hospital=hospital, doctor_id=workplace.doctor_id, status=HospitalDoctorLink.STATUS_CONFIRMED,
    ).exists()
    if not is_confirmed:
        raise NotFound()
    return workplace


# ─── Registry (shared with the doctor's workplace-hospital picker) ─────────

class HospitalListCreateView(APIView):
    """GET is public (AllowAny), same posture as apps.core.views.
    locations_list — a doctor's workplace picker needs it while
    authenticated, but a not-yet-registered hospital's registration screen
    (see apps.users.serializers.RegisterSerializer's claim-or-create branch)
    needs to search this same registry to find itself *before* it has any
    account or token at all. The data exposed (name/address/city/coords) is
    a public directory, not PII, so the wider posture costs nothing. POST —
    "add your variant" — stays IsDoctorVerified and throttled: it's the
    only user-submitted-content endpoint in the app, and a new row is
    immediately visible to every other doctor."""

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsDoctorVerified()]
        return [AllowAny()]

    def get_throttles(self):
        # Scoped throttle only applies to POST — see the DRF docs' own
        # pattern for a single view mixing a throttled and unthrottled
        # method. GET still gets the default anon/user throttles.
        self.throttle_scope = 'hospital_create' if self.request.method == 'POST' else None
        return super().get_throttles()

    def get(self, request):
        qs = Hospital.objects.filter(status__in=Hospital.VISIBLE_STATUSES)
        q = request.query_params.get('q', '').strip()
        city = request.query_params.get('city', '').strip()
        if city:
            qs = qs.filter(city=city)
        if q:
            qs = qs.filter(name__icontains=q)
        qs = qs.order_by('name')

        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            HospitalRegistrySerializer(page, many=True, context={'request': request}).data
        )

    def post(self, request):
        serializer = HospitalCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data['name']
        city = serializer.validated_data['city']

        duplicates = find_duplicates(name, city)
        if duplicates:
            existing = duplicates[0]
            return Response(
                HospitalRegistrySerializer(existing, context={'request': request}).data,
                status=status.HTTP_200_OK,
            )

        hospital = serializer.save(created_by=request.user)
        return Response(
            HospitalRegistrySerializer(hospital, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class HospitalDetailView(APIView):
    # Same AllowAny posture as HospitalListCreateView.get — see that view's
    # docstring.
    permission_classes = [AllowAny]

    def get(self, request, pk):
        try:
            hospital = Hospital.objects.get(pk=pk, status__in=Hospital.VISIBLE_STATUSES)
        except (Hospital.DoesNotExist, DjangoValidationError, ValueError):
            raise NotFound()
        return Response(HospitalRegistrySerializer(hospital, context={'request': request}).data)


# ─── Account ────────────────────────────────────────────────────────────

class HospitalProfileView(APIView):
    """IsHospital only (not IsHospitalApproved) — a hospital must be able to
    view and correct its own submitted details while its claim is still
    under admin review."""

    permission_classes = [IsHospital]

    def get(self, request):
        return Response(
            HospitalProfileSerializer(_get_own_hospital(request), context={'request': request}).data
        )

    def patch(self, request):
        serializer = HospitalProfileSerializer(
            _get_own_hospital(request), data=request.data, partial=True, context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class HospitalStatusView(APIView):
    """Poll target for the mobile pending-approval screen — mirrors
    apps.doctors' equivalent doctor-verification poll. Cheap and
    unauthenticated-state-free by design so it can be hit every ~20s."""

    permission_classes = [IsHospital]

    def get(self, request):
        from apps.subscriptions.entitlements import subscription_summary
        from apps.subscriptions.models import Subscription
        from apps.subscriptions.plans import ROLE_HOSPITAL

        hospital = _get_own_hospital(request)
        try:
            subscription = request.user.subscription
        except Subscription.DoesNotExist:
            subscription = None
        return Response({
            'claim_status': hospital.claim_status,
            'hospital_status': hospital.status,
            'subscription': subscription_summary(subscription, role=ROLE_HOSPITAL),
        })


# ─── Dashboard (requires an active paid subscription — no trial) ──────────

class HospitalDoctorLinkListView(APIView):
    permission_classes = [IsHospitalSubscribed]

    def get(self, request):
        qs = (
            HospitalDoctorLink.objects
            .filter(hospital=request.user.hospital)
            .select_related('doctor', 'doctor__doctor_profile')
        )
        status_param = request.query_params.get('status', '').strip()
        if status_param:
            qs = qs.filter(status=status_param)

        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            HospitalLinkSerializer(page, many=True, context={'request': request}).data
        )


class HospitalDoctorLinkApproveView(APIView):
    permission_classes = [IsHospitalSubscribed]

    def post(self, request, link_id):
        link = _get_hospital_link(request.user.hospital, link_id)
        link = hospital_services.approve_link(link, decided_by=request.user)
        return Response(HospitalLinkSerializer(link, context={'request': request}).data)


class HospitalDoctorLinkRejectView(APIView):
    permission_classes = [IsHospitalSubscribed]

    def post(self, request, link_id):
        link = _get_hospital_link(request.user.hospital, link_id)
        link = hospital_services.reject_link(link, decided_by=request.user)
        return Response(HospitalLinkSerializer(link, context={'request': request}).data)


class HospitalDoctorLinkRemoveView(APIView):
    permission_classes = [IsHospitalSubscribed]

    def delete(self, request, link_id):
        link = _get_hospital_link(request.user.hospital, link_id)
        link = hospital_services.remove_doctor(link, decided_by=request.user)
        return Response(HospitalLinkSerializer(link, context={'request': request}).data)


class HospitalDoctorSearchView(APIView):
    """For the "invite a doctor" flow. Returns name + specialization only —
    never email/phone (see DoctorBriefSerializer's docstring) — and excludes
    doctors this hospital already has an active (pending/invited/confirmed)
    link with, so the invite button never races the request/approve flow."""

    permission_classes = [IsHospitalSubscribed]

    def get(self, request):
        hospital = request.user.hospital
        excluded_ids = HospitalDoctorLink.objects.filter(
            hospital=hospital,
            status__in=(
                HospitalDoctorLink.STATUS_PENDING,
                HospitalDoctorLink.STATUS_INVITED,
                HospitalDoctorLink.STATUS_CONFIRMED,
            ),
        ).values_list('doctor_id', flat=True)

        qs = (
            User.objects
            .filter(role=User.ROLE_DOCTOR, is_active=True, doctor_profile__is_verified=True)
            .exclude(id__in=excluded_ids)
            .select_related('doctor_profile')
            .order_by('first_name', 'last_name')
        )
        q = request.query_params.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(doctor_profile__specialization__icontains=q)
            )

        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            DoctorBriefSerializer(page, many=True, context={'request': request}).data
        )


class HospitalDoctorInviteView(APIView):
    permission_classes = [IsHospitalSubscribed]

    def post(self, request):
        doctor_id = _parse_uuid_param(request.data.get('doctor_id'), 'doctor_id')
        if not doctor_id:
            raise ValidationError({'doctor_id': 'This field is required.'})
        try:
            doctor = User.objects.select_related('doctor_profile').get(
                pk=doctor_id, role=User.ROLE_DOCTOR, is_active=True, doctor_profile__is_verified=True,
            )
        except User.DoesNotExist:
            raise NotFound()

        link = hospital_services.invite_doctor(request.user.hospital, doctor, invited_by=request.user)
        return Response(
            HospitalLinkSerializer(link, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class HospitalDoctorWorkplacesView(APIView):
    """Lets the hospital pick which of a confirmed doctor's workplaces (at
    this hospital) to edit hours for. Same confirmed-link check as
    _get_confirmed_workplace, one level up."""

    permission_classes = [IsHospitalSubscribed]

    def get(self, request, doctor_id):
        hospital = request.user.hospital
        is_confirmed = HospitalDoctorLink.objects.filter(
            hospital=hospital, doctor_id=doctor_id, status=HospitalDoctorLink.STATUS_CONFIRMED,
        ).exists()
        if not is_confirmed:
            raise NotFound()
        workplaces = (
            Workplace.objects
            .filter(hospital=hospital, doctor_id=doctor_id)
            .prefetch_related('working_hours')
        )
        return Response(WorkplaceSerializer(workplaces, many=True, context={'request': request}).data)


class HospitalWorkplaceHoursView(APIView):
    permission_classes = [IsHospitalSubscribed]

    def get(self, request, workplace_id):
        workplace = _get_confirmed_workplace(request.user.hospital, workplace_id)
        return Response(full_week_hours(workplace))

    def put(self, request, workplace_id):
        workplace = _get_confirmed_workplace(request.user.hospital, workplace_id)
        items = validated_hours_items(request.data)
        with transaction.atomic():
            replace_working_hours(workplace, items)
        invalidate_doctor_slots(workplace.doctor_id)
        workplace.refresh_from_db()
        return Response(full_week_hours(workplace))


class HospitalAppointmentListView(APIView):
    """The hospital's appointments feed — deliberately filters on BOTH
    workplace__hospital AND a CONFIRMED link (belt and braces, same
    reasoning as _get_confirmed_workplace: the sync_* helpers in
    apps.hospitals.services should always keep Workplace.hospital null once
    a link leaves CONFIRMED, but this endpoint doesn't rely on that alone).
    Uses HospitalAppointmentSerializer — no patient identity or clinical
    notes, see that serializer's docstring."""

    permission_classes = [IsHospitalSubscribed]

    def get(self, request):
        hospital = request.user.hospital
        confirmed_doctor_ids = HospitalDoctorLink.objects.filter(
            hospital=hospital, status=HospitalDoctorLink.STATUS_CONFIRMED,
        ).values_list('doctor_id', flat=True)

        qs = (
            Appointment.objects
            .filter(workplace__hospital=hospital, doctor_id__in=confirmed_doctor_ids)
            .select_related('doctor', 'doctor__doctor_profile', 'workplace')
            .order_by('-starts_at')
        )

        doctor_id = _parse_uuid_param(request.query_params.get('doctor', '').strip(), 'doctor')
        status_param = request.query_params.get('status', '').strip()
        date_from = _parse_date_param(request.query_params.get('date_from', '').strip(), 'date_from')
        date_to = _parse_date_param(request.query_params.get('date_to', '').strip(), 'date_to')

        if doctor_id:
            qs = qs.filter(doctor_id=doctor_id)
        if status_param:
            qs = qs.filter(status=status_param)
        if date_from:
            qs = qs.filter(starts_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(starts_at__date__lte=date_to)

        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            HospitalAppointmentSerializer(page, many=True, context={'request': request}).data
        )


# ─── Doctor-side counterparts (accepting/declining a hospital's invite) ───

class DoctorHospitalLinkListView(APIView):
    permission_classes = [IsDoctorVerified]

    def get(self, request):
        qs = (
            HospitalDoctorLink.objects
            .filter(doctor=request.user)
            .select_related('hospital')
        )
        return Response(DoctorHospitalLinkSerializer(qs, many=True, context={'request': request}).data)


def _get_doctor_link(doctor, link_id):
    try:
        return HospitalDoctorLink.objects.select_related('hospital').get(pk=link_id, doctor=doctor)
    except (HospitalDoctorLink.DoesNotExist, DjangoValidationError, ValueError):
        raise NotFound()


class DoctorHospitalLinkAcceptView(APIView):
    permission_classes = [IsDoctorVerified]

    def post(self, request, link_id):
        link = _get_doctor_link(request.user, link_id)
        if link.status != HospitalDoctorLink.STATUS_INVITED:
            raise ValidationError({'detail': 'Only an invited link can be accepted.'})
        link = hospital_services.approve_link(link, decided_by=request.user)
        return Response(DoctorHospitalLinkSerializer(link, context={'request': request}).data)


class DoctorHospitalLinkDeclineView(APIView):
    permission_classes = [IsDoctorVerified]

    def post(self, request, link_id):
        link = _get_doctor_link(request.user, link_id)
        link = hospital_services.reject_link(link, decided_by=request.user)
        return Response(DoctorHospitalLinkSerializer(link, context={'request': request}).data)
