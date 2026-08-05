import datetime

from cryptography.fernet import Fernet
from django.test import override_settings
from rest_framework import status

from apps.appointments.admin import ReviewAdmin
from apps.appointments.models import Appointment, Review
from apps.messaging.models import Message, Thread

from .test_appointments import AppointmentTestBase

TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()


def _review_url(appointment_id):
    return f'/api/appointments/{appointment_id}/review/'


@override_settings(ASSISTANT_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class ReviewNeedsManualReviewTests(AppointmentTestBase):
    """(2a) — a review is flagged for manual review when there's no message
    history between the patient/doctor pair predating the appointment, the
    signature of a doctor gaming their own rating with self-controlled
    patient accounts rather than a real patient relationship."""

    def setUp(self):
        super().setUp()
        self.appointment = self._make_appointment(status=Appointment.STATUS_COMPLETED)
        self.as_patient()

    def _post_review(self, appointment=None):
        appointment = appointment or self.appointment
        return self.client.post(
            _review_url(appointment.id), {'rating': 5, 'comment': 'Great doctor'}, format='json',
        )

    def test_review_without_any_messages_is_flagged(self):
        res = self._post_review()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        review = Review.objects.get(appointment=self.appointment)
        self.assertTrue(review.needs_manual_review)

    def test_review_with_message_predating_appointment_is_not_flagged(self):
        thread = Thread.objects.create(patient=self.patient, doctor=self.doctor)
        msg = Message.objects.create(thread=thread, sender=self.patient, body='Hi doctor, question')
        Message.objects.filter(pk=msg.pk).update(
            created_at=self.appointment.starts_at - datetime.timedelta(days=1)
        )

        res = self._post_review()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        review = Review.objects.get(appointment=self.appointment)
        self.assertFalse(review.needs_manual_review)

    def test_message_sent_by_doctor_predating_appointment_also_clears_flag(self):
        # Either side's message counts as evidence of real contact.
        thread = Thread.objects.create(patient=self.patient, doctor=self.doctor)
        msg = Message.objects.create(thread=thread, sender=self.doctor, body='Welcome!')
        Message.objects.filter(pk=msg.pk).update(
            created_at=self.appointment.starts_at - datetime.timedelta(hours=2)
        )

        res = self._post_review()
        review = Review.objects.get(appointment=self.appointment)
        self.assertFalse(review.needs_manual_review)

    def test_message_after_appointment_start_does_not_clear_flag(self):
        # Contact only after the booking already existed doesn't prove the
        # relationship predates it — e.g. messages sent to arrange/confirm
        # the very appointment being reviewed.
        thread = Thread.objects.create(patient=self.patient, doctor=self.doctor)
        msg = Message.objects.create(thread=thread, sender=self.patient, body='See you soon')
        Message.objects.filter(pk=msg.pk).update(
            created_at=self.appointment.starts_at + datetime.timedelta(hours=1)
        )

        res = self._post_review()
        review = Review.objects.get(appointment=self.appointment)
        self.assertTrue(review.needs_manual_review)

    def test_message_with_a_different_doctor_does_not_clear_flag(self):
        other_token = None
        from apps.appointments.tests.test_appointments import _register_and_login, doctor_payload
        from django.contrib.auth import get_user_model
        User = get_user_model()

        other_token = _register_and_login(self.client, doctor_payload(email='other-doc@test.com'))
        other_doctor = User.objects.get(email='other-doc@test.com')
        thread = Thread.objects.create(patient=self.patient, doctor=other_doctor)
        msg = Message.objects.create(thread=thread, sender=self.patient, body='Hi')
        Message.objects.filter(pk=msg.pk).update(
            created_at=self.appointment.starts_at - datetime.timedelta(days=1)
        )

        self.as_patient()
        res = self._post_review()
        review = Review.objects.get(appointment=self.appointment)
        self.assertTrue(review.needs_manual_review)

    def test_needs_manual_review_not_client_writable(self):
        res = self.client.post(
            _review_url(self.appointment.id),
            {'rating': 5, 'comment': 'x', 'needs_manual_review': False},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        review = Review.objects.get(appointment=self.appointment)
        # Ignored — no genuine prior contact, so still flagged regardless of
        # what the client sent.
        self.assertTrue(review.needs_manual_review)

    def test_review_admin_surfaces_needs_manual_review(self):
        self.assertIn('needs_manual_review', ReviewAdmin.list_display)
        self.assertIn('needs_manual_review', ReviewAdmin.list_filter)


class ReviewCreateThrottleTests(AppointmentTestBase):
    """(2b) — no more than 5 reviews from the same request IP within 24h,
    regardless of which (or how many) patient accounts post them."""

    def setUp(self):
        super().setUp()
        self.as_patient()

    def _completed_appointment(self, hour):
        return self._make_appointment(status=Appointment.STATUS_COMPLETED, starts_at=self._future_dt(hour))

    def test_sixth_review_from_same_ip_in_a_day_is_throttled(self):
        for hour in (8, 9, 10, 11, 12):
            appt = self._completed_appointment(hour)
            res = self.client.post(_review_url(appt.id), {'rating': 5, 'comment': 'x'}, format='json')
            self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        appt = self._completed_appointment(13)
        res = self.client.post(_review_url(appt.id), {'rating': 5, 'comment': 'x'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertFalse(Review.objects.filter(appointment=appt).exists())

    def test_throttle_is_per_ip_not_per_patient_account(self):
        """The whole point of an IP throttle (vs. a per-user one) is that it
        still catches a doctor spinning up several patient accounts from the
        same machine/script — each posting just one review each. The second
        patient is registered *before* the 5 review posts below (rather than
        in between) because _register_and_login clears the cache as part of
        registering/logging in, which would otherwise reset the very IP
        throttle bucket this test is trying to observe."""
        from apps.appointments.tests.test_appointments import _register_and_login, patient_payload
        from django.contrib.auth import get_user_model
        User = get_user_model()

        other_token = _register_and_login(self.client, patient_payload(email='sixth-patient@test.com'))
        other_patient = User.objects.get(email='sixth-patient@test.com')

        self.as_patient()
        for hour in (8, 9, 10, 11, 12):
            appt = self._completed_appointment(hour)
            res = self.client.post(_review_url(appt.id), {'rating': 5, 'comment': 'x'}, format='json')
            self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        appt = self._make_appointment(
            patient=other_patient, status=Appointment.STATUS_COMPLETED, starts_at=self._future_dt(13),
        )
        res = self.client.post(_review_url(appt.id), {'rating': 5, 'comment': 'x'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_patch_and_delete_are_not_ip_throttled(self):
        appt = self._completed_appointment(8)
        self.client.post(_review_url(appt.id), {'rating': 3, 'comment': 'ok'}, format='json')
        for _ in range(10):
            res = self.client.patch(_review_url(appt.id), {'rating': 4, 'comment': 'better'}, format='json')
            self.assertNotEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
