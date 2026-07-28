from unittest.mock import patch

from django.test import override_settings
from rest_framework import status

from apps.hospitals.models import HospitalDoctorLink
from apps.payments.providers.base import ProviderOrder
from apps.subscriptions.models import Subscription
from apps.subscriptions.plans import PLAN_BASIC, PLAN_HOSPITAL_BASIC, PLAN_HOSPITAL_PRO
from apps.subscriptions.tasks import sweep_subscriptions
from apps.users.models import User

from .base import (
    HOSPITAL_CHECKOUT_URL,
    HOSPITAL_LINKS_URL,
    HOSPITAL_PLANS_URL,
    HOSPITAL_SUBSCRIPTION_URL,
    WORKPLACES_URL,
    HospitalDashboardTestBase,
    HospitalTestBase,
    activate_hospital_subscription,
    approve_hospital,
    link_approve_url,
)

CREATE_PATCH = 'apps.payments.providers.payriff.PayriffProvider.create_order'
STATUS_PATCH = 'apps.payments.providers.payriff.PayriffProvider.check_status'


def fake_order(order_id='hospital-order-1'):
    return ProviderOrder(
        order_id=order_id,
        payment_url=f'https://payriff.example/checkout/{order_id}',
        session_id='sess-1',
    )


class NoTrialInvariantTests(HospitalTestBase):
    def test_approved_hospital_starts_pending_not_trialing(self):
        """The core requirement behind 'no trial for hospitals': approval
        must never start a trial the way it does for a doctor (see
        apps.subscriptions.signals.start_trial_on_verification, which is
        deliberately doctor-only)."""
        approve_hospital(self.hospital)
        sub = Subscription.objects.get(user=self.hospital_user)
        self.assertEqual(sub.status, Subscription.STATUS_PENDING)
        self.assertIsNone(sub.trial_ends_at)

        self.as_hospital()
        res = self.client.get(HOSPITAL_SUBSCRIPTION_URL)
        self.assertEqual(res.data['status'], Subscription.STATUS_PENDING)
        self.assertEqual(res.data['effective_plan'], 'none')

    def test_unapproved_hospital_plans_endpoint_403s(self):
        self.as_hospital()
        res = self.client.get(HOSPITAL_PLANS_URL)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_approved_hospital_sees_only_hospital_plans(self):
        approve_hospital(self.hospital)
        self.as_hospital()
        res = self.client.get(HOSPITAL_PLANS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        plans = {row['plan'] for row in res.data}
        self.assertEqual(plans, {PLAN_HOSPITAL_BASIC, PLAN_HOSPITAL_PRO})


@override_settings(PAYRIFF_MERCHANT_ID='test-merchant', PAYRIFF_SECRET_KEY='test-secret')
class WrongRolePlanTests(HospitalTestBase):
    def setUp(self):
        super().setUp()
        approve_hospital(self.hospital)

    @patch(CREATE_PATCH, return_value=fake_order())
    def test_hospital_cannot_checkout_a_doctor_plan(self, mock_create):
        self.as_hospital()
        res = self.client.post(HOSPITAL_CHECKOUT_URL, {'plan': PLAN_BASIC}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        mock_create.assert_not_called()

    @patch(CREATE_PATCH, return_value=fake_order())
    def test_doctor_cannot_checkout_a_hospital_plan(self, mock_create):
        from apps.appointments.tests.test_appointments import AppointmentTestBase
        # Reuse a verified doctor via the shared fixture pattern rather than
        # standing up a second TestCase — just register+verify inline here.
        from apps.appointments.tests.test_appointments import _register_and_login, doctor_payload
        token = _register_and_login(self.client, doctor_payload(email='plaindoc@test.com'))
        doc = User.objects.get(email='plaindoc@test.com')
        doc.doctor_profile.is_verified = True
        doc.doctor_profile.save(update_fields=['is_verified'])

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        res = self.client.post('/api/doctor/subscription/checkout/', {'plan': PLAN_HOSPITAL_BASIC}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        mock_create.assert_not_called()

    def test_service_layer_rejects_wrong_role_plan_even_if_serializer_did_not(self):
        """Belt-and-braces: the service-level guard in
        apps.subscriptions.service.create_subscription_checkout must reject
        a wrong-role plan even bypassing the serializer entirely."""
        from apps.subscriptions.service import create_subscription_checkout
        with self.assertRaises(Exception):
            create_subscription_checkout(self.hospital_user, PLAN_BASIC)


@override_settings(PAYRIFF_MERCHANT_ID='test-merchant', PAYRIFF_SECRET_KEY='test-secret')
class CheckoutActivationTests(HospitalTestBase):
    def setUp(self):
        super().setUp()
        approve_hospital(self.hospital)

    @patch(CREATE_PATCH, return_value=fake_order())
    def test_webhook_activates_hospital_subscription_and_grants_dashboard_access(self, mock_create):
        self.as_hospital()
        checkout_res = self.client.post(HOSPITAL_CHECKOUT_URL, {'plan': PLAN_HOSPITAL_BASIC}, format='json')
        self.assertEqual(checkout_res.status_code, status.HTTP_201_CREATED)

        # Before payment confirms: dashboard is still gated.
        dash_res = self.client.get(HOSPITAL_LINKS_URL)
        self.assertEqual(dash_res.status_code, status.HTTP_403_FORBIDDEN)

        with patch(STATUS_PATCH, return_value='paid'):
            webhook_res = self.client.post(
                '/api/payments/webhook/payriff/', {'orderId': 'hospital-order-1'}, format='json',
            )
        self.assertEqual(webhook_res.status_code, status.HTTP_200_OK)

        sub = Subscription.objects.get(user=self.hospital_user)
        self.assertEqual(sub.status, Subscription.STATUS_ACTIVE)
        self.assertEqual(sub.plan, PLAN_HOSPITAL_BASIC)

        # After payment: dashboard opens up.
        dash_res = self.client.get(HOSPITAL_LINKS_URL)
        self.assertEqual(dash_res.status_code, status.HTTP_200_OK)


class DoctorCapTests(HospitalDashboardTestBase):
    def _confirmed_filler_doctors(self, count):
        for i in range(count):
            doc = User.objects.create_user(
                email=f'capdoctor{i}@test.com', password='Pass1234', role=User.ROLE_DOCTOR,
                first_name='Doc', last_name=str(i),
            )
            HospitalDoctorLink.objects.create(
                hospital=self.hospital, doctor=doc, status=HospitalDoctorLink.STATUS_CONFIRMED,
                requested_by=HospitalDoctorLink.REQUESTED_BY_DOCTOR,
            )

    def test_doctor_cap_blocks_confirming_past_the_basic_limit(self):
        # Basic caps at 15 confirmed doctors — see plans.HOSPITAL_PLAN_LIMITS.
        self._confirmed_filler_doctors(15)

        self.as_doctor()
        wp_res = self.client.post(WORKPLACES_URL, {
            'address': '1 Main St', 'city': 'baku', 'type': 'hospital',
            'hospital': str(self.hospital.id),
        }, format='json')
        self.assertEqual(wp_res.status_code, status.HTTP_201_CREATED)
        link = HospitalDoctorLink.objects.get(hospital=self.hospital, doctor=self.doctor)

        self.as_hospital()
        res = self.client.post(link_approve_url(link.id))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data['code'], 'plan_limit_reached')
        self.assertEqual(res.data['resource'], 'doctors')

        link.refresh_from_db()
        self.assertEqual(link.status, HospitalDoctorLink.STATUS_PENDING)

    def test_downgrade_does_not_remove_existing_links_over_the_cap(self):
        activate_hospital_subscription(self.hospital_user, plan=PLAN_HOSPITAL_PRO)
        self._confirmed_filler_doctors(20)
        self.assertEqual(
            HospitalDoctorLink.objects.filter(
                hospital=self.hospital, status=HospitalDoctorLink.STATUS_CONFIRMED,
            ).count(),
            20,
        )

        # Downgrade to basic (cap 15) — existing 20 confirmed links survive.
        activate_hospital_subscription(self.hospital_user, plan=PLAN_HOSPITAL_BASIC)
        self.assertEqual(
            HospitalDoctorLink.objects.filter(
                hospital=self.hospital, status=HospitalDoctorLink.STATUS_CONFIRMED,
            ).count(),
            20,
        )

        # But a brand-new confirmation is now blocked.
        self.as_doctor()
        wp_res = self.client.post(WORKPLACES_URL, {
            'address': '1 Main St', 'city': 'baku', 'type': 'hospital',
            'hospital': str(self.hospital.id),
        }, format='json')
        link = HospitalDoctorLink.objects.get(hospital=self.hospital, doctor=self.doctor)
        self.as_hospital()
        res = self.client.post(link_approve_url(link.id))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


class SweepTests(HospitalTestBase):
    def setUp(self):
        super().setUp()
        approve_hospital(self.hospital)

    def test_sweep_never_transitions_a_pending_hospital_subscription(self):
        """A hospital that's approved but never checked out sits in
        STATUS_PENDING indefinitely — none of sweep_subscriptions'
        transition functions match STATUS_PENDING (they match
        TRIALING/ACTIVE/PAST_DUE), so the hourly sweep must be a no-op for
        it."""
        sub = Subscription.objects.get(user=self.hospital_user)
        self.assertEqual(sub.status, Subscription.STATUS_PENDING)

        sweep_subscriptions()

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.STATUS_PENDING)

    def test_sweep_expires_a_lapsed_hospital_and_dashboard_403s(self):
        import datetime
        from django.utils import timezone

        activate_hospital_subscription(self.hospital_user, plan=PLAN_HOSPITAL_BASIC)
        sub = Subscription.objects.get(user=self.hospital_user)
        sub.status = Subscription.STATUS_PAST_DUE
        sub.grace_ends_at = timezone.now() - datetime.timedelta(hours=1)
        sub.save(update_fields=['status', 'grace_ends_at'])

        sweep_subscriptions()

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.STATUS_EXPIRED)

        self.as_hospital()
        res = self.client.get(HOSPITAL_LINKS_URL)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data['code'], 'subscription_required')
