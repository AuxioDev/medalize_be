import datetime
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework import status

from apps.appointments.models import Appointment
from apps.appointments.tests.test_appointments import (
    APPOINTMENTS_URL,
    DOCTOR_APPOINTMENTS_URL,
    AppointmentTestBase,
    _register_and_login,
    doctor_payload,
    patient_payload,
)
from apps.family.models import Dependent
from apps.notifications.models import Notification
from apps.payments.models import Payment
from apps.payments.providers.base import ProviderOrder
from apps.payments.providers.payriff import PayriffError

CREATE_PATCH = 'apps.payments.providers.payriff.PayriffProvider.create_order'
STATUS_PATCH = 'apps.payments.providers.payriff.PayriffProvider.check_status'
REFUND_PATCH = 'apps.payments.providers.payriff.PayriffProvider.refund_order'

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

    def _paid_payment(self, order_id='order-1'):
        """A Payment already marked PAID — the precondition every refund
        trigger point actually acts on (see refund_payment: any other
        status is a no-op)."""
        payment = self._create_payment(order_id)
        payment.status = Payment.STATUS_PAID
        payment.paid_at = timezone.now()
        payment.save(update_fields=['status', 'paid_at'])
        return payment


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


class PaymentDependentInheritanceTests(PaymentTestBase):
    """Payment.dependent is denormalized from appointment.dependent only at
    first creation — same as doctor/patient — with no separate input, and it
    must not be reassigned on later retries/reuses of the same row."""

    def setUp(self):
        super().setUp()
        self.dependent = Dependent.objects.create(
            managed_by=self.patient, first_name='Kid', last_name='Doe', relationship='child',
        )
        self.appointment.dependent = self.dependent
        self.appointment.save(update_fields=['dependent'])

    def test_payment_inherits_dependent_from_appointment_on_creation(self):
        with patch(CREATE_PATCH, return_value=fake_order('order-dep-1')):
            res = self.client.post(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['dependent']['id'], str(self.dependent.id))
        payment = Payment.objects.get(appointment=self.appointment)
        self.assertEqual(payment.dependent_id, self.dependent.id)
        # patient stays the account owner, unaffected by dependent.
        self.assertEqual(payment.patient, self.patient)

    def test_payment_dependent_is_null_when_appointment_has_none(self):
        self.appointment.dependent = None
        self.appointment.save(update_fields=['dependent'])
        with patch(CREATE_PATCH, return_value=fake_order('order-dep-2')):
            res = self.client.post(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNone(res.data['dependent'])

    def test_dependent_is_not_reassigned_when_retrying_a_failed_payment(self):
        with patch(CREATE_PATCH, return_value=fake_order('order-dep-3')):
            self.client.post(payment_url(self.appointment.id))
        payment = Payment.objects.get(appointment=self.appointment)
        self.assertEqual(payment.dependent_id, self.dependent.id)

        payment.status = Payment.STATUS_FAILED
        payment.save(update_fields=['status'])
        # Even if the appointment's dependent were to change in between (it
        # never does in practice — dependent_id isn't editable post-booking —
        # but this confirms the retry path truly never touches it).
        self.appointment.dependent = None
        self.appointment.save(update_fields=['dependent'])

        with patch(CREATE_PATCH, return_value=fake_order('order-dep-4')):
            res = self.client.post(payment_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.dependent_id, self.dependent.id)


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

    def test_return_page_auto_redirects_into_the_app_via_deep_link(self):
        # medalize:// is registered in ios/Runner/Info.plist's
        # CFBundleURLTypes and the second intent-filter in
        # android/app/.../AndroidManifest.xml — opening it alone brings the
        # app to the foreground, which triggers the existing
        # AppLifecycleState.resumed status recheck (no Dart-side deep-link
        # parsing needed). Both the auto-refresh meta tag and the fallback
        # button must carry it, since some mobile browsers require a user
        # gesture before following a non-http(s) redirect.
        self.client.credentials()
        res = self.client.get(f'{RETURN_URL}?result=approve&lang=en')
        html = res.content.decode('utf-8')
        deep_link = 'medalize://payment-return?result=approve&lang=en'
        self.assertIn(f'content="0;url={deep_link}"', html)
        self.assertIn(f'href="{deep_link}"', html)


class RefundServiceTests(PaymentTestBase):
    """Direct unit tests of apps.payments.service.refund_payment /
    refund_appointment_payment — the shared primitive every cancellation/
    decline/expiry trigger point below calls into."""

    def test_refund_payment_marks_refunded_on_success(self):
        from apps.payments.service import refund_payment
        payment = self._paid_payment()
        with patch(REFUND_PATCH, return_value=None) as mock_refund:
            result = refund_payment(payment, reason='test')
        mock_refund.assert_called_once_with('order-1', payment.amount)
        self.assertEqual(result.status, Payment.STATUS_REFUNDED)
        self.assertIsNotNone(result.refunded_at)

    def test_refund_payment_marks_refund_failed_on_provider_error_without_raising(self):
        from apps.payments.service import refund_payment
        payment = self._paid_payment()
        with patch(REFUND_PATCH, side_effect=PayriffError('network error')):
            result = refund_payment(payment, reason='test')  # must not raise
        self.assertEqual(result.status, Payment.STATUS_REFUND_FAILED)

    def test_refund_payment_is_noop_for_a_never_captured_pending_payment(self):
        from apps.payments.service import refund_payment
        payment = self._create_payment()  # still PENDING — never paid
        with patch(REFUND_PATCH) as mock_refund:
            result = refund_payment(payment, reason='test')
        mock_refund.assert_not_called()
        self.assertEqual(result.status, Payment.STATUS_PENDING)

    def test_refund_payment_is_idempotent_once_already_refunded(self):
        from apps.payments.service import refund_payment
        payment = self._paid_payment()
        with patch(REFUND_PATCH, return_value=None):
            refund_payment(payment, reason='first')
        with patch(REFUND_PATCH) as mock_refund:
            result = refund_payment(payment, reason='second')
        mock_refund.assert_not_called()
        self.assertEqual(result.status, Payment.STATUS_REFUNDED)

    def test_refund_appointment_payment_is_noop_when_no_payment_exists(self):
        from apps.payments.service import refund_appointment_payment
        with patch(REFUND_PATCH) as mock_refund:
            result = refund_appointment_payment(self.appointment, reason='test')
        self.assertIsNone(result)
        mock_refund.assert_not_called()


class PatientCancellationRefundTests(PaymentTestBase):
    """Wiring test for PatientAppointmentDetailView.delete — the binary
    window policy (outside → full refund, inside → cancellation succeeds
    with no refund) against a real PAID payment."""

    def test_cancel_outside_window_refunds_paid_appointment_in_full(self):
        self.appointment.status = Appointment.STATUS_CONFIRMED
        self.appointment.save(update_fields=['status'])
        payment = self._paid_payment()

        with patch(REFUND_PATCH, return_value=None) as mock_refund:
            res = self.client.delete(f'{APPOINTMENTS_URL}{self.appointment.id}/')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['refund_eligible'])
        self.assertEqual(res.data['payment']['status'], Payment.STATUS_REFUNDED)
        mock_refund.assert_called_once()
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_REFUNDED)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, Appointment.STATUS_CANCELLED)

    def test_cancel_inside_window_does_not_refund_paid_appointment(self):
        soon = timezone.now() + datetime.timedelta(hours=1)
        self.appointment.starts_at = soon
        self.appointment.ends_at = soon + datetime.timedelta(minutes=30)
        self.appointment.status = Appointment.STATUS_CONFIRMED
        self.appointment.save(update_fields=['starts_at', 'ends_at', 'status'])
        payment = self._paid_payment()

        with patch(REFUND_PATCH) as mock_refund:
            res = self.client.delete(f'{APPOINTMENTS_URL}{self.appointment.id}/')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data['refund_eligible'])
        self.assertEqual(res.data['payment']['status'], Payment.STATUS_PAID)
        mock_refund.assert_not_called()
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, Appointment.STATUS_CANCELLED)

    def test_a_refund_failure_does_not_block_the_cancellation(self):
        self.appointment.status = Appointment.STATUS_CONFIRMED
        self.appointment.save(update_fields=['status'])
        payment = self._paid_payment()

        with patch(REFUND_PATCH, side_effect=PayriffError('down')):
            res = self.client.delete(f'{APPOINTMENTS_URL}{self.appointment.id}/')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['payment']['status'], Payment.STATUS_REFUND_FAILED)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, Appointment.STATUS_CANCELLED)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_REFUND_FAILED)


class DoctorDeclineRefundTests(PaymentTestBase):
    def test_declining_a_pending_paid_appointment_refunds_in_full(self):
        # self.appointment is PENDING per PaymentTestBase.setUp.
        payment = self._paid_payment()
        self.as_doctor()
        with patch(REFUND_PATCH, return_value=None) as mock_refund:
            res = self.client.patch(
                f'{DOCTOR_APPOINTMENTS_URL}{self.appointment.id}/status/',
                {'status': 'declined'}, format='json',
            )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mock_refund.assert_called_once()
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_REFUNDED)


class DoctorCancelRefundTests(PaymentTestBase):
    """A doctor-initiated cancellation always refunds in full regardless of
    timing — the binary window only governs late-cancellation tracking
    (doctor_cancelled_late), never the refund."""

    def _confirmed_paid(self, starts_at=None):
        if starts_at is not None:
            self.appointment.starts_at = starts_at
            self.appointment.ends_at = starts_at + datetime.timedelta(minutes=30)
        self.appointment.status = Appointment.STATUS_CONFIRMED
        self.appointment.save()
        return self._paid_payment()

    def test_cancel_outside_window_refunds_in_full_and_is_not_flagged_late(self):
        payment = self._confirmed_paid()
        self.as_doctor()
        with patch(REFUND_PATCH, return_value=None) as mock_refund:
            res = self.client.patch(
                f'{DOCTOR_APPOINTMENTS_URL}{self.appointment.id}/status/',
                {'status': 'cancelled'}, format='json',
            )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mock_refund.assert_called_once()
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_REFUNDED)
        self.appointment.refresh_from_db()
        self.assertFalse(self.appointment.doctor_cancelled_late)

    def test_cancel_inside_window_still_refunds_in_full_but_is_flagged_late(self):
        soon = timezone.now() + datetime.timedelta(hours=1)
        payment = self._confirmed_paid(starts_at=soon)
        self.as_doctor()
        with patch(REFUND_PATCH, return_value=None) as mock_refund:
            res = self.client.patch(
                f'{DOCTOR_APPOINTMENTS_URL}{self.appointment.id}/status/',
                {'status': 'cancelled'}, format='json',
            )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mock_refund.assert_called_once()  # still a FULL refund despite lateness
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_REFUNDED)
        self.appointment.refresh_from_db()
        self.assertTrue(self.appointment.doctor_cancelled_late)


class ExpireStalePendingRefundTests(PaymentTestBase):
    def test_expiring_a_paid_pending_appointment_refunds_in_full(self):
        payment = self._paid_payment()
        past = timezone.now() - datetime.timedelta(minutes=5)
        self.appointment.starts_at = past
        self.appointment.ends_at = past + datetime.timedelta(minutes=30)
        self.appointment.save(update_fields=['starts_at', 'ends_at'])

        from apps.notifications.tasks import expire_stale_pending_appointments
        with patch(REFUND_PATCH, return_value=None) as mock_refund:
            expire_stale_pending_appointments()

        mock_refund.assert_called_once()
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_REFUNDED)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, Appointment.STATUS_DECLINED)


class DoctorDeactivationRefundTests(PaymentTestBase):
    def test_deactivating_doctor_refunds_paid_future_appointments(self):
        payment = self._confirmed_and_paid()

        with patch(REFUND_PATCH, return_value=None) as mock_refund:
            self.doctor.is_active = False
            self.doctor.save(update_fields=['is_active'])

        mock_refund.assert_called_once()
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_REFUNDED)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, Appointment.STATUS_CANCELLED)

    def _confirmed_and_paid(self):
        self.appointment.status = Appointment.STATUS_CONFIRMED
        self.appointment.save(update_fields=['status'])
        return self._paid_payment()


class NoShowRefundTests(PaymentTestBase):
    def test_no_show_does_not_attempt_a_refund(self):
        past = timezone.now() - datetime.timedelta(minutes=5)
        self.appointment.starts_at = past
        self.appointment.ends_at = past + datetime.timedelta(minutes=30)
        self.appointment.status = Appointment.STATUS_CONFIRMED
        self.appointment.save(update_fields=['starts_at', 'ends_at', 'status'])
        payment = self._paid_payment()

        self.as_doctor()
        with patch(REFUND_PATCH) as mock_refund:
            res = self.client.patch(
                f'{DOCTOR_APPOINTMENTS_URL}{self.appointment.id}/status/',
                {'status': 'no_show'}, format='json',
            )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mock_refund.assert_not_called()
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)
