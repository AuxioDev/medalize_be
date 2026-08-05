import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.appointments.models import Appointment, Review
from apps.appointments.tests.test_appointments import AppointmentTestBase, _register_and_login
from apps.doctors.models import Workplace
from apps.family.models import Dependent
from apps.medications.models import Medication
from apps.notifications.models import FCMToken
from apps.payments.models import Payment
from apps.prescriptions.models import Prescription
from apps.records.models import MedicalRecord
from apps.subscriptions.models import Subscription
from apps.subscriptions.plans import PLAN_PRO
from apps.users.models import UserDevice

User = get_user_model()

DELETE_URL = '/api/auth/delete/'
LOGIN_URL = '/api/auth/login/'
REFRESH_URL = '/api/auth/token/refresh/'

REFUND_PATCH = 'apps.payments.providers.payriff.PayriffProvider.refund_order'


def _login(client, email, password='Pass1234'):
    cache.clear()
    res = client.post(LOGIN_URL, {'email': email, 'password': password}, format='json')
    cache.clear()
    return res


@override_settings(PAYRIFF_MERCHANT_ID='test-merchant', PAYRIFF_SECRET_KEY='test-secret')
class AccountDeleteTestBase(AppointmentTestBase):
    """Doctor + patient + a shared workplace (from AppointmentTestBase),
    plus the delete-endpoint helpers every test below needs."""

    def _delete(self, password='Pass1234'):
        return self.client.post(DELETE_URL, {'password': password}, format='json')

    def _paid_payment(self, appointment, order_id='order-1'):
        payment = Payment.objects.create(
            appointment=appointment, patient=appointment.patient, doctor=appointment.doctor,
            amount='50.00', status=Payment.STATUS_PAID, paid_at=timezone.now(),
            provider='payriff', provider_order_id=order_id,
        )
        return payment


class AuthenticationTests(AccountDeleteTestBase):
    """Deletion requires a fresh, correct password — same re-auth contract
    as AccountDeactivateView, checked first because every other test below
    depends on it actually gating the irreversible action."""

    def test_wrong_password_returns_400_and_account_untouched(self):
        self.as_patient()
        res = self._delete(password='WrongPass1')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.patient.refresh_from_db()
        self.assertTrue(self.patient.is_active)
        self.assertFalse(self.patient.is_deleted)

    def test_missing_password_returns_400(self):
        self.as_patient()
        res = self.client.post(DELETE_URL, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_without_auth_returns_401(self):
        res = self._delete()
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_correct_password_returns_200(self):
        self.as_patient()
        res = self._delete()
        self.assertEqual(res.status_code, status.HTTP_200_OK)


class PatientDeletionPIITests(AccountDeleteTestBase):
    def test_scrubs_pii_and_sets_deleted_flags(self):
        original_email = self.patient.email
        self.as_patient()
        res = self._delete()
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.patient.refresh_from_db()
        self.assertFalse(self.patient.is_active)
        self.assertTrue(self.patient.is_deleted)
        self.assertIsNotNone(self.patient.deleted_at)
        self.assertEqual(self.patient.first_name, '')
        self.assertEqual(self.patient.last_name, '')
        self.assertEqual(self.patient.phone, '')
        self.assertNotEqual(self.patient.email, original_email)
        self.assertFalse(self.patient.has_usable_password())

    def test_clears_patient_profile_fields(self):
        self.patient.patient_profile.date_of_birth = datetime.date(1990, 1, 1)
        self.patient.patient_profile.allergies = 'Penicillin'
        self.patient.patient_profile.chronic_conditions = 'Asthma'
        self.patient.patient_profile.city = 'baku'
        self.patient.patient_profile.save()

        self.as_patient()
        self._delete()

        self.patient.patient_profile.refresh_from_db()
        self.assertIsNone(self.patient.patient_profile.date_of_birth)
        self.assertEqual(self.patient.patient_profile.allergies, '')
        self.assertEqual(self.patient.patient_profile.chronic_conditions, '')
        self.assertEqual(self.patient.patient_profile.city, '')

    def test_login_fails_after_deletion(self):
        self.as_patient()
        self._delete()
        res = _login(self.client, 'patient@test.com')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_revokes_all_sessions_including_other_devices(self):
        other_session = self._login2()
        self.as_patient()
        res = self._delete()
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        cache.clear()
        self.client.credentials()
        refresh_res = self.client.post(
            REFRESH_URL, {'refresh': other_session['refresh']}, format='json',
        )
        self.assertEqual(refresh_res.status_code, status.HTTP_401_UNAUTHORIZED)

    def _login2(self):
        return _login(self.client, 'patient@test.com').data

    def test_devices_and_fcm_tokens_are_deleted(self):
        UserDevice.objects.create(
            user=self.patient, device_id='dev-1', last_seen_at=timezone.now(),
        )
        FCMToken.objects.create(user=self.patient, token='fcm-token-1')

        self.as_patient()
        self._delete()

        self.assertFalse(UserDevice.objects.filter(user=self.patient).exists())
        self.assertFalse(FCMToken.objects.filter(user=self.patient).exists())


class PatientMedicalContentErasureTests(AccountDeleteTestBase):
    def test_erases_medical_records_including_the_file(self):
        record = MedicalRecord.objects.create(
            patient=self.patient, title='Blood test',
            file=SimpleUploadedFile('scan.pdf', b'%PDF-1.4\nfake', content_type='application/pdf'),
        )
        file_name = record.file.name

        self.as_patient()
        self._delete()

        self.assertFalse(MedicalRecord.objects.filter(pk=record.pk).exists())
        self.assertFalse(default_storage.exists(file_name))

    def test_erases_medications(self):
        Medication.objects.create(patient=self.patient, name='Ibuprofen', dosage='200mg')

        self.as_patient()
        self._delete()

        self.assertFalse(Medication.objects.filter(patient=self.patient).exists())

    def test_erases_prescriptions_and_items(self):
        past = timezone.now() - datetime.timedelta(days=3)
        appt = self._make_appointment(
            starts_at=past, ends_at=past + datetime.timedelta(minutes=30),
            status=Appointment.STATUS_COMPLETED,
        )
        prescription = Prescription.objects.create(
            appointment=appt, doctor=self.doctor, patient=self.patient, notes='Take with food',
        )
        prescription.items.create(drug_name='Amoxicillin', dosage='500mg')

        self.as_patient()
        self._delete()

        self.assertFalse(Prescription.objects.filter(pk=prescription.pk).exists())


class PatientAppointmentCascadeTests(AccountDeleteTestBase):
    def test_cancels_and_refunds_future_paid_appointment(self):
        appt = self._make_appointment(status=Appointment.STATUS_CONFIRMED)  # 8 days out
        payment = self._paid_payment(appt)

        with patch(REFUND_PATCH, return_value=None) as mock_refund:
            self.as_patient()
            res = self._delete()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mock_refund.assert_called_once()

        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.STATUS_CANCELLED)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_REFUNDED)
        # The transactional fact (amount) survives; the link to the now-
        # deleted patient does not.
        self.assertEqual(str(payment.amount), '50.00')
        self.assertIsNone(payment.patient)

    def test_does_not_touch_far_future_pending_appointment_status_beyond_cancelling(self):
        appt = self._make_appointment(status=Appointment.STATUS_PENDING)
        self.as_patient()
        self._delete()
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.STATUS_CANCELLED)

    def test_retains_past_appointment_but_scrubs_reason_and_notes(self):
        past = timezone.now() - datetime.timedelta(days=3)
        appt = self._make_appointment(
            starts_at=past, ends_at=past + datetime.timedelta(minutes=30),
            status=Appointment.STATUS_COMPLETED,
            reason='Chest pain, please advise', notes='Recommended follow-up in 2 weeks',
        )

        self.as_patient()
        self._delete()

        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.STATUS_COMPLETED)  # untouched
        self.assertEqual(appt.reason, '')
        self.assertEqual(appt.notes, '')

    def test_review_is_retained_but_reviewer_link_is_anonymized(self):
        past = timezone.now() - datetime.timedelta(days=3)
        appt = self._make_appointment(
            starts_at=past, ends_at=past + datetime.timedelta(minutes=30),
            status=Appointment.STATUS_COMPLETED,
        )
        review = Review.objects.create(
            appointment=appt, doctor=self.doctor, patient=self.patient,
            rating=5, comment='Excellent care',
        )

        self.as_patient()
        self._delete()

        review.refresh_from_db()
        self.assertIsNone(review.patient)
        self.assertEqual(review.rating, 5)  # the doctor's aggregate rating is unaffected
        self.assertEqual(review.comment, 'Excellent care')
        self.assertEqual(review.doctor_id, self.doctor.id)


class PatientDependentCascadeTests(AccountDeleteTestBase):
    def test_soft_deletes_dependents_and_invalidates_pending_consent_token(self):
        dependent = Dependent.objects.create(
            managed_by=self.patient, first_name='Alex', last_name='Doe',
            relationship=Dependent.RELATIONSHIP_SPOUSE,
            date_of_birth=datetime.date(1990, 1, 1),
            contact_email='alex@example.com',
            consent_token_hash='some-hash', consent_token_expires_at=timezone.now() + datetime.timedelta(days=10),
        )

        self.as_patient()
        self._delete()

        dependent.refresh_from_db()
        self.assertFalse(dependent.is_active)
        self.assertEqual(dependent.consent_token_hash, '')
        self.assertIsNone(dependent.consent_token_expires_at)

    def test_already_inactive_dependent_is_left_alone(self):
        dependent = Dependent.objects.create(
            managed_by=self.patient, first_name='Sam', relationship=Dependent.RELATIONSHIP_CHILD,
            is_active=False,
        )
        self.as_patient()
        self._delete()
        dependent.refresh_from_db()
        self.assertFalse(dependent.is_active)


class DoctorDeletionTests(AccountDeleteTestBase):
    def test_scrubs_doctor_profile_and_unverifies(self):
        self.doctor.doctor_profile.bio = 'Experienced cardiologist'
        self.doctor.doctor_profile.license_number = 'AZ-12345'
        self.doctor.doctor_profile.save()

        self.as_doctor()
        res = self._delete()
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.doctor.doctor_profile.refresh_from_db()
        self.assertEqual(self.doctor.doctor_profile.bio, '')
        self.assertEqual(self.doctor.doctor_profile.license_number, '')
        self.assertFalse(self.doctor.doctor_profile.is_verified)

    def test_workplace_is_retained_untouched(self):
        self.as_doctor()
        self._delete()
        # Not deleted (and could not be — Appointment.workplace is PROTECT),
        # and not itself considered PII.
        self.assertTrue(Workplace.objects.filter(pk=self.workplace.pk).exists())

    def test_deleting_doctor_with_active_subscription_and_future_paid_appointments(self):
        """The path the phase spec calls out explicitly: a doctor with a
        live subscription AND future paid bookings deletes their account —
        both must be handled, not just the simple case."""
        # A Subscription row is auto-created at registration (see
        # apps.subscriptions.signals.create_subscription) — update it in
        # place rather than creating a second one (unique on user).
        subscription = self.doctor.subscription
        subscription.plan = PLAN_PRO
        subscription.status = Subscription.STATUS_ACTIVE
        subscription.current_period_end = timezone.now() + datetime.timedelta(days=15)
        subscription.save()
        appt1 = self._make_appointment(status=Appointment.STATUS_CONFIRMED)
        appt2 = self._make_appointment(
            starts_at=self._future_dt(14), ends_at=self._future_dt(14, 30),
            status=Appointment.STATUS_PENDING,
        )
        payment1 = self._paid_payment(appt1, order_id='order-a')

        with patch(REFUND_PATCH, return_value=None) as mock_refund:
            self.as_doctor()
            res = self._delete()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mock_refund.assert_called_once()

        appt1.refresh_from_db()
        appt2.refresh_from_db()
        self.assertEqual(appt1.status, Appointment.STATUS_CANCELLED)
        self.assertEqual(appt2.status, Appointment.STATUS_CANCELLED)

        payment1.refresh_from_db()
        self.assertEqual(payment1.status, Payment.STATUS_REFUNDED)
        self.assertIsNone(payment1.doctor)

        subscription.refresh_from_db()
        self.assertEqual(subscription.status, Subscription.STATUS_EXPIRED)
        self.assertIsNone(subscription.current_period_end)

    def test_does_not_erase_other_patients_prescriptions_or_appointment_notes(self):
        """A doctor deleting their account must not strip a still-active
        patient of their own medical history — only the doctor's own
        identity (already scrubbed on the User row) becomes anonymous."""
        past = timezone.now() - datetime.timedelta(days=3)
        appt = self._make_appointment(
            starts_at=past, ends_at=past + datetime.timedelta(minutes=30),
            status=Appointment.STATUS_COMPLETED, notes='Prescribed antibiotics',
        )
        prescription = Prescription.objects.create(
            appointment=appt, doctor=self.doctor, patient=self.patient, notes='Take with food',
        )

        self.as_doctor()
        self._delete()

        appt.refresh_from_db()
        self.assertEqual(appt.notes, 'Prescribed antibiotics')
        self.assertTrue(Prescription.objects.filter(pk=prescription.pk).exists())
        prescription.refresh_from_db()
        self.assertEqual(prescription.doctor_id, self.doctor.id)  # FK left in place


class HospitalDeletionTests(APITestCase):
    """Out of the phase spec's explicit focus (which is doctor-centric),
    but a hospital account is also a billable Subscription holder — make
    sure deleting one doesn't leave its subscription active/billable."""

    def setUp(self):
        cache.clear()
        self.hospital_token = _register_and_login(self.client, {
            'email': 'hospital@test.com', 'password': 'Pass1234', 'password_confirm': 'Pass1234',
            'role': 'hospital', 'first_name': 'City', 'last_name': 'Hospital',
            'privacy_consent': True, 'hospital_name': 'City Hospital', 'hospital_city': 'baku',
        })
        self.hospital_user = User.objects.get(email='hospital@test.com')

    def test_cancels_subscription(self):
        # Auto-created at registration (see
        # apps.subscriptions.signals.create_subscription) — update in place.
        subscription = self.hospital_user.subscription
        subscription.status = Subscription.STATUS_ACTIVE
        subscription.current_period_end = timezone.now() + datetime.timedelta(days=10)
        subscription.save()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.hospital_token}')
        res = self.client.post(DELETE_URL, {'password': 'Pass1234'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        subscription.refresh_from_db()
        self.assertEqual(subscription.status, Subscription.STATUS_EXPIRED)
