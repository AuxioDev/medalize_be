from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.appointments.models import Appointment
from apps.doctors.models import Workplace
from apps.hospitals.models import HospitalDoctorLink
from apps.hospitals.permissions import IsHospitalApproved
from apps.payments.service import payments_enabled
from apps.users.permissions import IsDoctor

from .entitlements import subscription_summary
from .models import Subscription
from .plans import (
    HOSPITAL_PLAN_LIMITS,
    PLAN_LIMITS,
    PLAN_NAMES,
    PLAN_PRICES,
    PLANS_FOR_ROLE,
    ROLE_DOCTOR,
    ROLE_HOSPITAL,
)
from .serializers import SubscriptionCheckoutSerializer
from .service import create_subscription_checkout


class DoctorSubscriptionView(APIView):
    permission_classes = [IsDoctor]

    def get(self, request):
        # Defensive get_or_create: every doctor gets a row at registration
        # (apps.subscriptions.signals.create_subscription) and every
        # pre-existing one was backfilled by migration 0002 — this only
        # matters if that ever drifts.
        subscription, _ = Subscription.objects.get_or_create(user=request.user)
        data = subscription_summary(subscription, role=ROLE_DOCTOR)

        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        data['usage'] = {
            'workplaces': Workplace.objects.filter(doctor=request.user).count(),
            'appointments_this_month': (
                Appointment.objects
                .filter(doctor=request.user, starts_at__gte=month_start)
                .exclude(status__in=[Appointment.STATUS_CANCELLED, Appointment.STATUS_DECLINED])
                .count()
            ),
        }
        return Response(data)


class SubscriptionPlanListView(APIView):
    """Plan catalog for the paywall screen. IsDoctor rather than AllowAny —
    these plans are doctor-only, per the product decision that patients
    never see or pay for anything here. Hospital plans have their own list
    view (apps.hospitals.views.HospitalSubscriptionPlanListView), scoped by
    PLANS_FOR_ROLE the same way."""

    permission_classes = [IsDoctor]

    def get(self, request):
        doctor_plans = PLANS_FOR_ROLE[ROLE_DOCTOR]
        return Response([
            {
                'plan': plan,
                'name': PLAN_NAMES[plan],
                'price': str(PLAN_PRICES[plan]),
                'currency': 'AZN',
                'limits': PLAN_LIMITS[plan],
            }
            for plan in doctor_plans
        ])


class SubscriptionCheckoutView(APIView):
    permission_classes = [IsDoctor]
    throttle_scope = 'subscription_checkout'

    def post(self, request):
        if not payments_enabled():
            return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)
        serializer = SubscriptionCheckoutSerializer(data=request.data, context={'role': ROLE_DOCTOR})
        serializer.is_valid(raise_exception=True)
        payment = create_subscription_checkout(request.user, serializer.validated_data['plan'])
        return Response({'payment_url': payment.payment_url}, status=status.HTTP_201_CREATED)


class HospitalSubscriptionView(APIView):
    """Mirrors DoctorSubscriptionView. IsHospitalApproved, not
    IsHospitalSubscribed — a hospital needs to see its own plan/paywall
    state (`usage`, `effective_plan: 'none'`) precisely while it has no
    active subscription yet, which IsHospitalSubscribed would 403 on."""

    permission_classes = [IsHospitalApproved]

    def get(self, request):
        subscription, _ = Subscription.objects.get_or_create(user=request.user)
        data = subscription_summary(subscription, role=ROLE_HOSPITAL)
        data['usage'] = {
            'doctors': HospitalDoctorLink.objects.filter(
                hospital=request.user.hospital, status=HospitalDoctorLink.STATUS_CONFIRMED,
            ).count(),
        }
        return Response(data)


class HospitalSubscriptionPlanListView(APIView):
    permission_classes = [IsHospitalApproved]

    def get(self, request):
        hospital_plans = PLANS_FOR_ROLE[ROLE_HOSPITAL]
        return Response([
            {
                'plan': plan,
                'name': PLAN_NAMES[plan],
                'price': str(PLAN_PRICES[plan]),
                'currency': 'AZN',
                'limits': HOSPITAL_PLAN_LIMITS[plan],
            }
            for plan in hospital_plans
        ])


class HospitalSubscriptionCheckoutView(APIView):
    permission_classes = [IsHospitalApproved]
    throttle_scope = 'subscription_checkout'

    def post(self, request):
        if not payments_enabled():
            return Response(status=status.HTTP_503_SERVICE_UNAVAILABLE)
        serializer = SubscriptionCheckoutSerializer(data=request.data, context={'role': ROLE_HOSPITAL})
        serializer.is_valid(raise_exception=True)
        payment = create_subscription_checkout(request.user, serializer.validated_data['plan'])
        return Response({'payment_url': payment.payment_url}, status=status.HTTP_201_CREATED)
