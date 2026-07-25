from unittest.mock import patch

from django.test import override_settings
from rest_framework import status

from apps.appointments.models import Appointment
from apps.appointments.tests.test_appointments import (
    AppointmentTestBase,
    _register_and_login,
    doctor_payload,
    patient_payload,
)
from apps.notifications.models import Notification
from apps.payments.models import Payment
from apps.payments.providers.base import ProviderOrder
from apps.payments.providers.payriff import PayriffError

CREATE_PATCH = 'apps.payments.providers.payriff.PayriffProvider.create_order'
STATUS_PATCH = 'apps.payments.providers.payriff.PayriffProvider.check_status'

WEBHOOK_URL = '/api/payments/webhook/payriff/'
RETURN_URL = '/api/payments/return/'


def payment_url(appointment_id):
    return f'/api/appointments/{appointment_id}/payment/'


def fake_order(order_id='order-1'):
    return ProviderOrder(
        order_id=order_id,
        payment_url=f'https://payriff.example/checkout/{order_id}',
        session_id='sess-1',
    )


@override_settings(PAYRIFF_MERCHANT_ID='test-merchant', PAYRIFF_SECRET_KEY='test-secret')
class PaymentTestBase(AppointmentTestBase):
    """A pending appointment with a consultation fee set, authenticated as
    the owning patient — same shape as apps.prescriptions.tests.test_
    prescriptions.PrescriptionTestBase."""

    def setUp(self):
        super().setUp()
        self.doctor.doctor_profile.consultation_fee = '50.00'
        self.doctor.doctor_profile.save(update_fields=['consultation_fee'])
        self.appointment = self._make_appointment(status=Appointment.STATUS_PENDING)
        self.as_patient()

    def _create_payment(self, order_id='order-1'):
        with patch(CREATE_PATCH, return_value=fake_order(order_id)):
            res = self.client.post(payment_url(self.appointment.id))
        assert res.status_code == status.HTTP_200_OK, res.data
        return Payment.objects.get(appointment=self.appointment)


class CreatePaymentTests(PaymentTestBase):
    def test_creates_pending_payment_and_returns_payment_url(self):
        with patch(CREATE_PATCH, return_value=fake_order('order-1')) as mock_create:
            res = self.client.post(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], Payment.STATUS_PENDING)
        self.assertEqual(res.data['payment_url'], 'https://payriff.example/checkout/order-1')
        self.assertEqual(str(res.data['amount']), '50.00')
        self.assertEqual(res.data['currency'], 'AZN')
        mock_create.assert_called_once()

        payment = Payment.objects.get(appointment=self.appointment)
        self.assertEqual(payment.provider_order_id, 'order-1')
        self.assertEqual(payment.provider, 'payriff')
        self.assertEqual(payment.doctor, self.doctor)
        self.assertEqual(payment.patient, self.patient)

    def test_amount_is_a_snapshot_not_a_live_reference(self):
        self._create_payment('order-1')
        self.doctor.doctor_profile.consultation_fee = '999.00'
        self.doctor.doctor_profile.save(update_fields=['consultation_fee'])
        payment = Payment.objects.get(appointment=self.appointment)
        self.assertEqual(str(payment.amount), '50.00')

    def test_second_post_reuses_pending_payment_without_calling_provider_again(self):
        with patch(CREATE_PATCH, return_value=fake_order('order-1')) as mock_create:
            self.client.post(payment_url(self.appointment.id))
            res = self.client.post(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['payment_url'], 'https://payriff.example/checkout/order-1')
        mock_create.assert_called_once()
        self.assertEqual(Payment.objects.filter(appointment=self.appointment).count(), 1)

    def test_paid_payment_is_returned_as_is_without_calling_provider(self):
        self._create_payment('order-1')
        payment = Payment.objects.get(appointment=self.appointment)
        payment.status = Payment.STATUS_PAID
        payment.save(update_fields=['status'])

        with patch(CREATE_PATCH) as mock_create:
            res = self.client.post(payment_url(self.appointment.id))
        mock_create.assert_not_called()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], Payment.STATUS_PAID)

    def test_failed_payment_is_retried_with_a_fresh_order(self):
        payment = self._create_payment('order-1')
        payment.status = Payment.STATUS_FAILED
        payment.save(update_fields=['status'])

        with patch(CREATE_PATCH, return_value=fake_order('order-2')) as mock_create:
            res = self.client.post(payment_url(self.appointment.id))
        mock_create.assert_called_once()
        self.assertEqual(res.data['status'], Payment.STATUS_PENDING)
        self.assertEqual(res.data['payment_url'], 'https://payriff.example/checkout/order-2')
        # Still exactly one Payment row (OneToOneField) — reused, not duplicated.
        self.assertEqual(Payment.objects.filter(appointment=self.appointment).count(), 1)

    def test_other_patients_appointment_returns_404(self):
        other_token = _register_and_login(self.client, patient_payload(email='other-payer@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        with patch(CREATE_PATCH) as mock_create:
            res = self.client.post(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        mock_create.assert_not_called()

    def test_doctor_cannot_create_payment(self):
        self.as_doctor()
        res = self.client.post(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_create_payment(self):
        self.client.force_authenticate(None)
        res = self.client.post(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class FeatureDisabledTests(PaymentTestBase):
    @override_settings(PAYRIFF_MERCHANT_ID='', PAYRIFF_SECRET_KEY='')
    def test_post_returns_503_when_unconfigured(self):
        with patch(CREATE_PATCH) as mock_create:
            res = self.client.post(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        mock_create.assert_not_called()
        self.assertFalse(Payment.objects.filter(appointment=self.appointment).exists())

    @override_settings(PAYRIFF_MERCHANT_ID='only-merchant-set', PAYRIFF_SECRET_KEY='')
    def test_disabled_when_only_one_credential_is_set(self):
        res = self.client.post(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_booking_flow_unaffected_when_payments_disabled(self):
        # The non-negotiable requirement from the phase spec: with no Payriff
        # credentials, booking/appointment endpoints work exactly as before.
        with override_settings(PAYRIFF_MERCHANT_ID='', PAYRIFF_SECRET_KEY=''):
            res = self.client.get(f'/api/appointments/{self.appointment.id}/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_doctor_status_transition_not_gated_by_payment(self):
        # DoctorAppointmentStatusView must not be touched/gated by this phase.
        with override_settings(PAYRIFF_MERCHANT_ID='', PAYRIFF_SECRET_KEY=''):
            self.as_doctor()
            res = self.client.patch(
                f'/api/doctor/appointments/{self.appointment.id}/status/',
                {'status': Appointment.STATUS_CONFIRMED}, format='json',
            )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, Appointment.STATUS_CONFIRMED)


class GetPaymentTests(PaymentTestBase):
    def test_get_returns_404_before_any_payment_created(self):
        res = self.client.get(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_returns_created_payment(self):
        self._create_payment('order-1')
        res = self.client.get(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], Payment.STATUS_PENDING)
        self.assertEqual(res.data['payment_url'], 'https://payriff.example/checkout/order-1')

    def test_get_is_owner_only_other_patient_gets_404(self):
        self._create_payment('order-1')
        other_token = _register_and_login(self.client, patient_payload(email='other-viewer@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.get(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_allows_treating_doctor_to_view_status(self):
        # GET is open to either participant (mirrors
        # AppointmentPrescriptionView) — the treating doctor also finds it
        # useful to see whether a visit was paid for.
        self._create_payment('order-1')
        self.as_doctor()
        res = self.client.get(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], Payment.STATUS_PENDING)

    def test_get_is_not_open_to_an_unrelated_doctor(self):
        self._create_payment('order-1')
        other_doctor_token = _register_and_login(self.client, doctor_payload(email='other-doctor@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_doctor_token}')
        res = self.client.get(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_gets_401(self):
        self.client.force_authenticate(None)
        res = self.client.get(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class WebhookTests(PaymentTestBase):
    def test_webhook_trusts_check_status_not_request_body(self):
        payment = self._create_payment('order-1')
        with patch(STATUS_PATCH, return_value=Payment.STATUS_PAID) as mock_status:
            res = self.client.post(
                WEBHOOK_URL,
                # Body claims FAILED/DECLINED — must be entirely ignored.
                {'transactionId': 'order-1', 'status': 'FAILED', 'paymentStatus': 'DECLINED'},
                format='json',
            )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mock_status.assert_called_once_with('order-1')
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertIsNotNone(payment.paid_at)

    def test_webhook_claiming_paid_is_ignored_if_check_status_disagrees(self):
        payment = self._create_payment('order-1')
        with patch(STATUS_PATCH, return_value=Payment.STATUS_FAILED):
            res = self.client.post(
                WEBHOOK_URL, {'transactionId': 'order-1', 'status': 'SUCCESS'}, format='json',
            )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_FAILED)
        self.assertIsNone(payment.paid_at)

    def test_webhook_marking_paid_sends_notification(self):
        # Same convention as apps.prescriptions.tests.test_prescriptions
        # (test_prescription_issued_task_creates_notification): call the
        # Celery task function directly rather than relying only on the
        # `.delay()` fired from inside handle_webhook_ping, which needs a
        # real broker unless the top-level celery app package happens to be
        # imported by the current test process — not guaranteed for a plain
        # `manage.py test <label>` run.
        payment = self._create_payment('order-2')
        with patch(STATUS_PATCH, return_value=Payment.STATUS_PAID):
            res = self.client.post(WEBHOOK_URL, {'transactionId': 'order-2'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        from apps.notifications.tasks import send_payment_received
        send_payment_received(str(payment.id))

        notif = Notification.objects.filter(user=self.patient).latest('sent_at')
        self.assertIn(self.doctor.first_name, notif.title + notif.message)

    def test_webhook_for_unknown_order_id_still_returns_200(self):
        with patch(STATUS_PATCH) as mock_status:
            res = self.client.post(WEBHOOK_URL, {'transactionId': 'no-such-order'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mock_status.assert_not_called()

    def test_webhook_with_no_order_id_still_returns_200(self):
        with patch(STATUS_PATCH) as mock_status:
            res = self.client.post(WEBHOOK_URL, {'nonsense': 'field'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mock_status.assert_not_called()

    def test_webhook_survives_provider_exception_and_still_returns_200(self):
        payment = self._create_payment('order-3')
        with patch(STATUS_PATCH, side_effect=PayriffError('network error')):
            res = self.client.post(WEBHOOK_URL, {'transactionId': 'order-3'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PENDING)

    def test_webhook_requires_no_authentication(self):
        payment = self._create_payment('order-4')
        self.client.credentials()  # strip the JWT the base class attached
        with patch(STATUS_PATCH, return_value=Payment.STATUS_PAID):
            res = self.client.post(WEBHOOK_URL, {'transactionId': 'order-4'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)


class ReturnPageTests(PaymentTestBase):
    def test_return_page_is_public_and_returns_html(self):
        self.client.credentials()
        res = self.client.get(f'{RETURN_URL}?result=approve')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('text/html', res['Content-Type'])

    def test_return_page_does_not_mutate_payment_status(self):
        payment = self._create_payment('order-5')
        self.client.credentials()
        self.client.get(f'{RETURN_URL}?result=approve&lang=en')
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PENDING)

    def test_return_page_localizes_by_lang_param(self):
        self.client.credentials()
        res = self.client.get(f'{RETURN_URL}?result=approve&lang=ru')
        self.assertIn('Оплата обработана', res.content.decode('utf-8'))

    def test_return_page_falls_back_to_english_for_unknown_lang(self):
        self.client.credentials()
        res = self.client.get(f'{RETURN_URL}?result=approve&lang=xx')
        self.assertIn('Payment processed', res.content.decode('utf-8'))
