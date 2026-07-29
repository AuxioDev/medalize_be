from django.test import Client, override_settings
from rest_framework import status

from apps.appointments.models import Appointment
from apps.appointments.tests.test_appointments import AppointmentTestBase
from apps.payments.models import Payment
from apps.payments.service import get_provider, payments_enabled
from apps.subscriptions.models import Subscription

MOCK_CHECKOUT_URL = '/api/payments/mock-checkout/'


def payment_url(appointment_id):
    return f'/api/appointments/{appointment_id}/payment/'


@override_settings(PAYMENT_PROVIDER='mock', PAYRIFF_MERCHANT_ID='', PAYRIFF_SECRET_KEY='')
class ProviderSelectionTests(AppointmentTestBase):
    def test_mock_provider_is_enabled_without_any_payriff_credentials(self):
        # Deliberately blank Payriff creds above — the mock provider must
        # not depend on them at all.
        self.assertTrue(payments_enabled())

    def test_get_provider_returns_the_mock_provider(self):
        from apps.payments.providers.mock import MockCardProvider
        self.assertIsInstance(get_provider(), MockCardProvider)
        self.assertEqual(get_provider().name, 'mock')


@override_settings(PAYMENT_PROVIDER='mock')
class AppointmentMockCheckoutTests(AppointmentTestBase):
    def setUp(self):
        super().setUp()
        self.doctor.doctor_profile.consultation_fee = '50.00'
        self.doctor.doctor_profile.save(update_fields=['consultation_fee'])
        self.appointment = self._make_appointment(status=Appointment.STATUS_PENDING)
        self.as_patient()

    def test_checkout_creates_a_pending_payment_pointing_at_our_own_mock_checkout_page(self):
        res = self.client.post(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.data)

        payment = Payment.objects.get(appointment=self.appointment)
        self.assertEqual(payment.status, Payment.STATUS_PENDING)
        self.assertEqual(payment.provider, 'mock')
        self.assertIn('/api/payments/mock-checkout/', payment.payment_url)
        self.assertIn(payment.provider_order_id, payment.payment_url)

    def test_mock_checkout_page_renders_without_authentication(self):
        self.client.post(payment_url(self.appointment.id))
        payment = Payment.objects.get(appointment=self.appointment)

        self.client.credentials()  # simulate the external browser: no JWT at all
        res = self.client.get(
            MOCK_CHECKOUT_URL,
            {'order_id': payment.provider_order_id, 'amount': '50.00', 'currency': 'AZN',
             'description': 'Medalize', 'approve_url': 'http://x/', 'cancel_url': 'http://x/'},
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(payment.provider_order_id.encode(), res.content)

    def test_submitting_the_mock_card_form_marks_the_payment_paid(self):
        self.client.post(payment_url(self.appointment.id))
        payment = Payment.objects.get(appointment=self.appointment)
        self.assertEqual(payment.status, Payment.STATUS_PENDING)

        self.client.credentials()
        res = self.client.post(MOCK_CHECKOUT_URL, {
            'order_id': payment.provider_order_id,
            'approve_url': payment.payment_url,  # any same-backend URL is accepted
        })
        self.assertEqual(res.status_code, 302)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertIsNotNone(payment.paid_at)

    def test_mock_checkout_post_works_without_a_csrf_token(self):
        # Regression test: MockCheckoutView is a plain Django View (not a
        # DRF APIView), so it's subject to CsrfViewMiddleware unless
        # explicitly exempted — a real external browser (no prior same-site
        # cookie/token from this app) hits this, but Django's test Client
        # doesn't enforce CSRF by default, so only a Client constructed with
        # enforce_csrf_checks=True actually catches a regression here.
        self.client.post(payment_url(self.appointment.id))
        payment = Payment.objects.get(appointment=self.appointment)

        strict_client = Client(enforce_csrf_checks=True)
        res = strict_client.post(MOCK_CHECKOUT_URL, {
            'order_id': payment.provider_order_id,
            'approve_url': payment.payment_url,
        })
        self.assertEqual(res.status_code, 302, res.content)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)

    def test_mock_checkout_refuses_to_redirect_to_a_foreign_host(self):
        self.client.post(payment_url(self.appointment.id))
        payment = Payment.objects.get(appointment=self.appointment)

        self.client.credentials()
        res = self.client.post(MOCK_CHECKOUT_URL, {
            'order_id': payment.provider_order_id,
            'approve_url': 'https://evil.example/steal',
        })
        # Confirmation still happens — only the redirect itself is refused.
        self.assertNotEqual(res.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)


@override_settings(PAYMENT_PROVIDER='mock')
class SubscriptionMockCheckoutTests(AppointmentTestBase):
    """AppointmentTestBase already gives a verified (trialing) doctor —
    reused here purely for the authenticated client, not the trial state."""

    def test_subscription_checkout_and_mock_confirmation_activates_the_plan(self):
        self.as_doctor()
        checkout = self.client.post(
            '/api/doctor/subscription/checkout/', {'plan': 'basic'}, format='json',
        )
        self.assertEqual(checkout.status_code, status.HTTP_201_CREATED, checkout.data)

        sub = Subscription.objects.get(user=self.doctor)
        payment_row = sub.subscription_payments.get()
        self.assertEqual(payment_row.provider, 'mock')

        self.client.credentials()
        res = self.client.post(MOCK_CHECKOUT_URL, {
            'order_id': payment_row.provider_order_id,
            'approve_url': payment_row.payment_url,
        })
        self.assertEqual(res.status_code, 302)

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.STATUS_ACTIVE)
        self.assertEqual(sub.plan, 'basic')
