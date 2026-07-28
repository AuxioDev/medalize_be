import datetime
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework import status

from apps.appointments.models import Appointment
from apps.appointments.tests.test_appointments import (
    AppointmentTestBase,
    _register_and_login,
    doctor_payload,
    patient_payload,
)
from apps.doctors.models import Workplace
from apps.messaging.models import Thread
from apps.payments.providers.base import ProviderOrder
from apps.subscriptions.models import DoctorSubscription, SubscriptionPayment
from apps.subscriptions.plans import GRACE_DAYS, PLAN_BASIC, PLAN_PRO, TRIAL_DAYS
from apps.subscriptions.tasks import sweep_subscriptions

DOCTORS_URL = '/api/doctors/'
WORKPLACES_URL = '/api/doctor/workplaces/'
SUBSCRIPTION_URL = '/api/doctor/subscription/'
PLANS_URL = '/api/doctor/subscription/plans/'
CHECKOUT_URL = '/api/doctor/subscription/checkout/'
WEBHOOK_URL = '/api/payments/webhook/payriff/'
THREADS_URL = '/api/messaging/threads/'

CREATE_PATCH = 'apps.payments.providers.payriff.PayriffProvider.create_order'
STATUS_PATCH = 'apps.payments.providers.payriff.PayriffProvider.check_status'


def workplace_payload(**kwargs):
    data = {'name': 'Extra Clinic', 'address': '1 Side St', 'city': 'baku', 'type': 'clinic'}
    data.update(kwargs)
    return data


def fake_order(order_id='sub-order-1'):
    return ProviderOrder(
        order_id=order_id,
        payment_url=f'https://payriff.example/checkout/{order_id}',
        session_id='sess-1',
    )


class TrialStartTests(AppointmentTestBase):
    """AppointmentTestBase.setUp() already verifies self.doctor, which is
    exactly the transition this app hooks into."""

    def test_verification_starts_trial(self):
        sub = DoctorSubscription.objects.get(user=self.doctor)
        self.assertEqual(sub.status, DoctorSubscription.STATUS_TRIALING)
        self.assertIsNotNone(sub.trial_ends_at)
        expected = timezone.now() + datetime.timedelta(days=TRIAL_DAYS)
        self.assertLess(abs((sub.trial_ends_at - expected).total_seconds()), 5)

    def test_registration_alone_leaves_subscription_pending(self):
        token = _register_and_login(self.client, doctor_payload(email='fresh@test.com'))
        from apps.users.models import User
        fresh = User.objects.get(email='fresh@test.com')
        sub = DoctorSubscription.objects.get(user=fresh)
        self.assertEqual(sub.status, DoctorSubscription.STATUS_PENDING)
        self.assertIsNone(sub.trial_ends_at)

    def test_reverifying_an_already_trialing_doctor_does_not_restart_trial(self):
        sub = DoctorSubscription.objects.get(user=self.doctor)
        original_trial_end = sub.trial_ends_at

        self.doctor.doctor_profile.is_verified = False
        self.doctor.doctor_profile.save(update_fields=['is_verified'])
        self.doctor.doctor_profile.is_verified = True
        self.doctor.doctor_profile.save(update_fields=['is_verified'])

        sub.refresh_from_db()
        self.assertEqual(sub.status, DoctorSubscription.STATUS_TRIALING)
        self.assertEqual(sub.trial_ends_at, original_trial_end)


class SweepTransitionTests(AppointmentTestBase):
    def _sub(self):
        return DoctorSubscription.objects.get(user=self.doctor)

    def test_trial_past_deadline_becomes_past_due_with_grace_window(self):
        sub = self._sub()
        sub.trial_ends_at = timezone.now() - datetime.timedelta(hours=1)
        sub.save(update_fields=['trial_ends_at'])

        sweep_subscriptions()

        sub.refresh_from_db()
        self.assertEqual(sub.status, DoctorSubscription.STATUS_PAST_DUE)
        expected_grace = timezone.now() + datetime.timedelta(days=GRACE_DAYS)
        self.assertLess(abs((sub.grace_ends_at - expected_grace).total_seconds()), 5)

    def test_active_past_period_end_becomes_past_due(self):
        sub = self._sub()
        sub.status = DoctorSubscription.STATUS_ACTIVE
        sub.plan = PLAN_PRO
        sub.current_period_end = timezone.now() - datetime.timedelta(hours=1)
        sub.save(update_fields=['status', 'plan', 'current_period_end'])

        sweep_subscriptions()

        sub.refresh_from_db()
        self.assertEqual(sub.status, DoctorSubscription.STATUS_PAST_DUE)
        self.assertEqual(sub.plan, PLAN_PRO)  # plan is retained through past_due

    def test_past_due_after_grace_becomes_expired(self):
        sub = self._sub()
        sub.status = DoctorSubscription.STATUS_PAST_DUE
        sub.grace_ends_at = timezone.now() - datetime.timedelta(hours=1)
        sub.save(update_fields=['status', 'grace_ends_at'])

        sweep_subscriptions()

        sub.refresh_from_db()
        self.assertEqual(sub.status, DoctorSubscription.STATUS_EXPIRED)

    def test_trial_ending_reminder_sent_once(self):
        sub = self._sub()
        sub.trial_ends_at = timezone.now() + datetime.timedelta(hours=12)
        sub.save(update_fields=['trial_ends_at'])

        sweep_subscriptions()
        sub.refresh_from_db()
        self.assertEqual(sub.last_reminder_stage, DoctorSubscription.REMINDER_T1)

        # A second sweep in the same window must not re-stage/re-send.
        sweep_subscriptions()
        sub.refresh_from_db()
        self.assertEqual(sub.last_reminder_stage, DoctorSubscription.REMINDER_T1)
        self.assertEqual(sub.status, DoctorSubscription.STATUS_TRIALING)


class EntitlementVisibilityTests(AppointmentTestBase):
    def _expire(self):
        DoctorSubscription.objects.filter(user=self.doctor).update(
            status=DoctorSubscription.STATUS_EXPIRED,
        )

    def test_expired_doctor_hidden_from_list(self):
        self.as_patient()
        res = self.client.get(DOCTORS_URL)
        self.assertEqual(res.data['count'], 1)

        self._expire()
        res = self.client.get(DOCTORS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 0)

    def test_expired_doctor_detail_returns_404(self):
        self._expire()
        self.as_patient()
        res = self.client.get(f'/api/doctors/{self.doctor.id}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_past_due_doctor_still_visible(self):
        DoctorSubscription.objects.filter(user=self.doctor).update(
            status=DoctorSubscription.STATUS_PAST_DUE,
            grace_ends_at=timezone.now() + datetime.timedelta(days=GRACE_DAYS),
        )
        self.as_patient()
        res = self.client.get(DOCTORS_URL)
        self.assertEqual(res.data['count'], 1)

    def test_cannot_book_expired_doctor(self):
        self._expire()
        self.as_patient()
        res = self.client.post('/api/appointments/', {
            'doctor_id': str(self.doctor.id),
            'workplace_id': str(self.workplace.id),
            'starts_at': self._future_dt(10).isoformat(),
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class PromotedRankingTests(AppointmentTestBase):
    """self.doctor (trialing, from AppointmentTestBase) is 'Amy' first — an
    added Başlanğıc-tier doctor with a higher rating must still rank
    below the promoted trial/Pro doctor."""

    def setUp(self):
        super().setUp()
        from apps.users.models import User
        self.basic_token = _register_and_login(self.client, doctor_payload(
            email='basic-doctor@test.com', first_name='Zzz', last_name='Basic',
        ))
        self.basic_doctor = User.objects.get(email='basic-doctor@test.com')
        self.basic_doctor.doctor_profile.is_verified = True
        self.basic_doctor.doctor_profile.save(update_fields=['is_verified'])
        DoctorSubscription.objects.filter(user=self.basic_doctor).update(
            status=DoctorSubscription.STATUS_ACTIVE, plan=PLAN_BASIC,
            current_period_end=timezone.now() + datetime.timedelta(days=30),
        )
        Workplace.objects.create(
            doctor=self.basic_doctor, name='Basic Clinic', address='x',
            city='baku', region='baku', type='clinic',
        )

    def test_promoted_doctor_ranks_first_alphabetically_last(self):
        # self.doctor is 'John Smith' (trialing/promoted), self.basic_doctor
        # is 'Zzz Basic' (Başlanğıc/not promoted) — alphabetically Zzz would
        # sort after John anyway, so also check the reverse-name case below
        # to prove ordering is driven by promo_rank, not name.
        self.as_patient()
        res = self.client.get(DOCTORS_URL)
        names = [d['first_name'] for d in res.data['results']]
        self.assertEqual(names, ['John', 'Zzz'])

    def test_promoted_doctor_ranks_first_even_when_alphabetically_last(self):
        DoctorSubscription.objects.filter(user=self.doctor).update(plan='')  # still trialing
        self.doctor.first_name = 'Zzzoctor'
        self.doctor.save(update_fields=['first_name'])
        self.basic_doctor.first_name = 'Aaadoctor'
        self.basic_doctor.save(update_fields=['first_name'])

        self.as_patient()
        res = self.client.get(f'{DOCTORS_URL}?ordering=-rating')
        names = [d['first_name'] for d in res.data['results']]
        self.assertEqual(names, ['Zzzoctor', 'Aaadoctor'])

    def test_is_promoted_flag_on_public_serializer(self):
        self.as_patient()
        res = self.client.get(DOCTORS_URL)
        by_name = {d['first_name']: d['is_promoted'] for d in res.data['results']}
        self.assertTrue(by_name['John'])
        self.assertFalse(by_name['Zzz'])


class WorkplaceLimitTests(AppointmentTestBase):
    def test_basic_plan_limited_to_one_workplace(self):
        DoctorSubscription.objects.filter(user=self.doctor).update(
            status=DoctorSubscription.STATUS_ACTIVE, plan=PLAN_BASIC,
            current_period_end=timezone.now() + datetime.timedelta(days=30),
        )
        self.as_doctor()
        # self.workplace (from AppointmentTestBase) already counts as the 1st.
        res = self.client.post(WORKPLACES_URL, workplace_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data['code'], 'plan_limit_reached')

    def test_trial_allows_up_to_five_workplaces(self):
        self.as_doctor()
        for i in range(4):  # self.workplace is already #1
            res = self.client.post(WORKPLACES_URL, workplace_payload(name=f'Clinic {i}'), format='json')
            self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        res = self.client.post(WORKPLACES_URL, workplace_payload(name='One Too Many'), format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_downgrade_does_not_delete_existing_workplaces_over_the_new_limit(self):
        self.as_doctor()
        for i in range(4):
            self.client.post(WORKPLACES_URL, workplace_payload(name=f'Clinic {i}'), format='json')
        self.assertEqual(Workplace.objects.filter(doctor=self.doctor).count(), 5)

        DoctorSubscription.objects.filter(user=self.doctor).update(
            status=DoctorSubscription.STATUS_ACTIVE, plan=PLAN_BASIC,
            current_period_end=timezone.now() + datetime.timedelta(days=30),
        )
        self.assertEqual(Workplace.objects.filter(doctor=self.doctor).count(), 5)
        res = self.client.get(WORKPLACES_URL)
        self.assertEqual(len(res.data), 5)


class AppointmentMonthlyLimitTests(AppointmentTestBase):
    def setUp(self):
        super().setUp()
        DoctorSubscription.objects.filter(user=self.doctor).update(
            status=DoctorSubscription.STATUS_ACTIVE, plan=PLAN_BASIC,
            current_period_end=timezone.now() + datetime.timedelta(days=30),
        )

    def test_booking_rejected_once_monthly_cap_reached(self):
        for hour in range(9, 9 + 40 * 1):
            pass  # placeholder to keep loop readable; real bookings below use distinct slots

        # 40 non-cancelled appointments already on the books this month.
        for i in range(40):
            self._make_appointment(
                starts_at=self._future_dt(9) + datetime.timedelta(days=i % 5, hours=i // 5),
                ends_at=self._future_dt(9) + datetime.timedelta(days=i % 5, hours=i // 5, minutes=30),
            )
        self.as_patient()
        res = self.client.post('/api/appointments/', {
            'doctor_id': str(self.doctor.id),
            'workplace_id': str(self.workplace.id),
            'starts_at': self._future_dt(16, 30).isoformat(),
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cancelled_appointments_do_not_count_against_cap(self):
        for i in range(40):
            self._make_appointment(
                starts_at=self._future_dt(9) + datetime.timedelta(days=i % 5, hours=i // 5),
                ends_at=self._future_dt(9) + datetime.timedelta(days=i % 5, hours=i // 5, minutes=30),
                status=Appointment.STATUS_CANCELLED,
            )
        self.as_patient()
        res = self.client.post('/api/appointments/', {
            'doctor_id': str(self.doctor.id),
            'workplace_id': str(self.workplace.id),
            'starts_at': self._future_dt(16, 30).isoformat(),
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)


class ChatGateTests(AppointmentTestBase):
    def setUp(self):
        super().setUp()
        self.appointment = self._make_appointment()

    def _open_thread_as_patient(self):
        self.as_patient()
        return self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')

    def test_basic_plan_blocks_new_thread(self):
        DoctorSubscription.objects.filter(user=self.doctor).update(
            status=DoctorSubscription.STATUS_ACTIVE, plan=PLAN_BASIC,
            current_period_end=timezone.now() + datetime.timedelta(days=30),
        )
        res = self._open_thread_as_patient()
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data['code'], 'chat_unavailable')

    def test_existing_thread_still_reachable_after_downgrade(self):
        res = self._open_thread_as_patient()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        thread_id = res.data['id']

        DoctorSubscription.objects.filter(user=self.doctor).update(
            status=DoctorSubscription.STATUS_ACTIVE, plan=PLAN_BASIC,
            current_period_end=timezone.now() + datetime.timedelta(days=30),
        )
        res = self._open_thread_as_patient()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['id'], thread_id)
        self.assertEqual(Thread.objects.filter(patient=self.patient, doctor=self.doctor).count(), 1)

    def test_trial_allows_new_thread(self):
        res = self._open_thread_as_patient()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)


class StatsPayloadTests(AppointmentTestBase):
    STATS_URL = '/api/doctor/stats/'

    def test_basic_plan_gets_reduced_payload(self):
        DoctorSubscription.objects.filter(user=self.doctor).update(
            status=DoctorSubscription.STATUS_ACTIVE, plan=PLAN_BASIC,
            current_period_end=timezone.now() + datetime.timedelta(days=30),
        )
        self.as_doctor()
        res = self.client.get(self.STATS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('appointments_this_month', res.data)
        self.assertIn('pending_count', res.data)
        self.assertNotIn('acceptance_rate', res.data)
        self.assertNotIn('total_patients', res.data)

    def test_trial_gets_full_payload(self):
        self.as_doctor()
        res = self.client.get(self.STATS_URL)
        self.assertIn('acceptance_rate', res.data)
        self.assertIn('total_patients', res.data)


@override_settings(PAYRIFF_MERCHANT_ID='test-merchant', PAYRIFF_SECRET_KEY='test-secret')
class CheckoutAndWebhookTests(AppointmentTestBase):
    def setUp(self):
        super().setUp()
        self.as_doctor()

    def test_checkout_creates_pending_subscription_payment(self):
        with patch(CREATE_PATCH, return_value=fake_order('sub-1')) as mock_create:
            res = self.client.post(CHECKOUT_URL, {'plan': PLAN_PRO}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['payment_url'], 'https://payriff.example/checkout/sub-1')
        mock_create.assert_called_once()

        payment = SubscriptionPayment.objects.get(provider_order_id='sub-1')
        self.assertEqual(payment.status, SubscriptionPayment.STATUS_PENDING)
        self.assertEqual(payment.plan, PLAN_PRO)
        self.assertEqual(str(payment.amount), '39.99')

    @override_settings(PAYRIFF_MERCHANT_ID='', PAYRIFF_SECRET_KEY='')
    def test_checkout_503_when_payments_disabled(self):
        with patch(CREATE_PATCH) as mock_create:
            res = self.client.post(CHECKOUT_URL, {'plan': PLAN_BASIC}, format='json')
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        mock_create.assert_not_called()

    def test_webhook_activates_subscription_and_sets_period(self):
        with patch(CREATE_PATCH, return_value=fake_order('sub-2')):
            self.client.post(CHECKOUT_URL, {'plan': PLAN_BASIC}, format='json')

        self.client.credentials()  # webhook needs no auth
        with patch(STATUS_PATCH, return_value=SubscriptionPayment.STATUS_PAID):
            res = self.client.post(WEBHOOK_URL, {'transactionId': 'sub-2'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        sub = DoctorSubscription.objects.get(user=self.doctor)
        self.assertEqual(sub.status, DoctorSubscription.STATUS_ACTIVE)
        self.assertEqual(sub.plan, PLAN_BASIC)
        self.assertIsNotNone(sub.current_period_end)

    def test_renewal_extends_from_future_period_end_not_from_now(self):
        future_end = timezone.now() + datetime.timedelta(days=10)
        DoctorSubscription.objects.filter(user=self.doctor).update(
            status=DoctorSubscription.STATUS_ACTIVE, plan=PLAN_BASIC,
            current_period_end=future_end,
        )
        with patch(CREATE_PATCH, return_value=fake_order('sub-3')):
            self.client.post(CHECKOUT_URL, {'plan': PLAN_BASIC}, format='json')

        self.client.credentials()
        with patch(STATUS_PATCH, return_value=SubscriptionPayment.STATUS_PAID):
            self.client.post(WEBHOOK_URL, {'transactionId': 'sub-3'}, format='json')

        sub = DoctorSubscription.objects.get(user=self.doctor)
        expected = future_end + datetime.timedelta(days=30)
        self.assertLess(abs((sub.current_period_end - expected).total_seconds()), 5)

    def test_lapsed_period_renews_from_now_not_from_the_past(self):
        DoctorSubscription.objects.filter(user=self.doctor).update(
            status=DoctorSubscription.STATUS_PAST_DUE, plan=PLAN_BASIC,
            current_period_end=timezone.now() - datetime.timedelta(days=5),
            grace_ends_at=timezone.now() + datetime.timedelta(days=GRACE_DAYS),
        )
        with patch(CREATE_PATCH, return_value=fake_order('sub-4')):
            self.client.post(CHECKOUT_URL, {'plan': PLAN_BASIC}, format='json')

        self.client.credentials()
        with patch(STATUS_PATCH, return_value=SubscriptionPayment.STATUS_PAID):
            self.client.post(WEBHOOK_URL, {'transactionId': 'sub-4'}, format='json')

        sub = DoctorSubscription.objects.get(user=self.doctor)
        expected = timezone.now() + datetime.timedelta(days=30)
        self.assertLess(abs((sub.current_period_end - expected).total_seconds()), 5)
        self.assertEqual(sub.status, DoctorSubscription.STATUS_ACTIVE)

    def test_appointment_payment_webhook_still_resolves_to_payment_not_subscription(self):
        # Regression guard for the shared-webhook routing change in
        # apps.payments.service.handle_webhook_ping: an appointment payment's
        # order id must still resolve to apps.payments.Payment, never fall
        # through to the subscriptions lookup.
        from apps.payments.models import Payment
        self.doctor.doctor_profile.consultation_fee = '50.00'
        self.doctor.doctor_profile.save(update_fields=['consultation_fee'])
        appointment = self._make_appointment(status=Appointment.STATUS_PENDING)

        self.as_patient()
        with patch(CREATE_PATCH, return_value=fake_order('appt-order-1')):
            self.client.post(f'/api/appointments/{appointment.id}/payment/')

        self.client.credentials()
        with patch(STATUS_PATCH, return_value=Payment.STATUS_PAID):
            res = self.client.post(WEBHOOK_URL, {'transactionId': 'appt-order-1'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        payment = Payment.objects.get(provider_order_id='appt-order-1')
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertFalse(SubscriptionPayment.objects.filter(provider_order_id='appt-order-1').exists())


class SubscriptionStatusEndpointTests(AppointmentTestBase):
    def test_get_returns_trial_status_and_limits(self):
        self.as_doctor()
        res = self.client.get(SUBSCRIPTION_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], DoctorSubscription.STATUS_TRIALING)
        self.assertEqual(res.data['effective_plan'], 'trial')
        self.assertTrue(res.data['limits']['chat'])
        self.assertIn('usage', res.data)
        self.assertEqual(res.data['usage']['workplaces'], 1)

    def test_plans_endpoint_lists_both_tiers(self):
        self.as_doctor()
        res = self.client.get(PLANS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        plans = {p['plan']: p['price'] for p in res.data}
        self.assertEqual(plans[PLAN_BASIC], '19.99')
        self.assertEqual(plans[PLAN_PRO], '39.99')

    def test_patient_cannot_access_subscription_endpoints(self):
        self.as_patient()
        res = self.client.get(SUBSCRIPTION_URL)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


class LoginPayloadTests(AppointmentTestBase):
    def test_login_response_includes_subscription_block(self):
        res = self.client.post('/api/auth/login/', {
            'email': 'doctor@test.com', 'password': 'Pass1234',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('subscription', res.data)
        self.assertEqual(res.data['subscription']['status'], DoctorSubscription.STATUS_TRIALING)

    def test_me_endpoint_includes_subscription_block(self):
        self.as_doctor()
        res = self.client.get('/api/auth/me/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('subscription', res.data)
        self.assertEqual(res.data['subscription']['effective_plan'], 'trial')
