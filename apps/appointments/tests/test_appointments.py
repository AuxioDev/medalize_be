import datetime
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.appointments.models import Appointment
from apps.doctors.models import Workplace, WorkingHours

User = get_user_model()

REGISTER_URL = '/api/auth/register/'
LOGIN_URL = '/api/auth/login/'
DOCTORS_URL = '/api/doctors/'
APPOINTMENTS_URL = '/api/appointments/'
DOCTOR_APPOINTMENTS_URL = '/api/doctor/appointments/'


def doctor_payload(**kwargs):
    data = {'email': 'doctor@test.com', 'password': 'Pass1234', 'password_confirm': 'Pass1234',
            'role': 'doctor', 'first_name': 'John', 'last_name': 'Smith'}
    data.update(kwargs)
    return data


def patient_payload(**kwargs):
    data = {'email': 'patient@test.com', 'password': 'Pass1234', 'password_confirm': 'Pass1234',
            'role': 'patient', 'first_name': 'Jane', 'last_name': 'Doe'}
    data.update(kwargs)
    return data


def _register_and_login(client, payload):
    cache.clear()
    client.post(REGISTER_URL, payload, format='json')
    cache.clear()
    res = client.post(LOGIN_URL, {'email': payload['email'], 'password': payload['password']}, format='json')
    cache.clear()
    return res.data['access']


class AppointmentTestBase(APITestCase):
    """A verified doctor with one workplace + a patient, both with tokens."""

    def setUp(self):
        self.doctor_token = _register_and_login(self.client, doctor_payload())
        self.doctor = User.objects.get(email='doctor@test.com')
        self.doctor.doctor_profile.is_verified = True
        self.doctor.doctor_profile.save(update_fields=['is_verified'])

        self.patient_token = _register_and_login(self.client, patient_payload())
        self.patient = User.objects.get(email='patient@test.com')

        self.workplace = Workplace.objects.create(
            doctor=self.doctor, name='Test Clinic', address='123 Main St',
            city='Baku', type='clinic',
        )
        # A date 8 days out, with that weekday's working hours active 09:00–17:00.
        self.future_date = (timezone.now() + datetime.timedelta(days=8)).date()
        WorkingHours.objects.create(
            workplace=self.workplace, weekday=self.future_date.weekday(),
            start_time='09:00', end_time='17:00', is_active=True,
        )

    def as_patient(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.patient_token}')

    def as_doctor(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.doctor_token}')

    def _future_dt(self, hour=10, minute=0):
        return timezone.make_aware(
            datetime.datetime.combine(self.future_date, datetime.time(hour, minute))
        )

    def _make_appointment(self, **kwargs):
        starts = kwargs.pop('starts_at', self._future_dt(10))
        ends = kwargs.pop('ends_at', starts + datetime.timedelta(minutes=30))
        data = dict(
            doctor=self.doctor, patient=self.patient, workplace=self.workplace,
            starts_at=starts, ends_at=ends,
        )
        data.update(kwargs)
        return Appointment.objects.create(**data)


class DoctorDiscoveryTests(AppointmentTestBase):
    def test_list_doctors_returns_verified_doctor(self):
        self.as_patient()
        res = self.client.get(DOCTORS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 1)

    def test_list_doctors_excludes_unverified(self):
        self.doctor.doctor_profile.is_verified = False
        self.doctor.doctor_profile.save(update_fields=['is_verified'])
        self.as_patient()
        res = self.client.get(DOCTORS_URL)
        self.assertEqual(res.data['count'], 0)

    def test_list_doctors_requires_auth(self):
        res = self.client.get(DOCTORS_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_doctors_filter_by_name(self):
        self.as_patient()
        res = self.client.get(f'{DOCTORS_URL}?name=John')
        self.assertEqual(res.data['count'], 1)
        res = self.client.get(f'{DOCTORS_URL}?name=Nobody')
        self.assertEqual(res.data['count'], 0)

    def test_doctor_detail_returns_200(self):
        self.as_patient()
        res = self.client.get(f'{DOCTORS_URL}{self.doctor.pk}/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('workplaces', res.data)

    def test_doctor_detail_unknown_id_returns_404(self):
        self.as_patient()
        res = self.client.get(f'{DOCTORS_URL}{uuid.uuid4()}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class SlotTests(AppointmentTestBase):
    def _slots_url(self, date=None, workplace=None):
        date = date or self.future_date.isoformat()
        workplace = workplace if workplace is not None else self.workplace.pk
        return f'{DOCTORS_URL}{self.doctor.pk}/slots/?date={date}&workplace_id={workplace}'

    def test_slots_returns_full_day(self):
        self.as_patient()
        res = self.client.get(self._slots_url())
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # 09:00–17:00 at 30 min = 16 slots.
        self.assertEqual(len(res.data['slots']), 16)

    def test_slots_missing_date_returns_400(self):
        self.as_patient()
        res = self.client.get(f'{DOCTORS_URL}{self.doctor.pk}/slots/?workplace_id={self.workplace.pk}')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_slots_invalid_date_returns_400(self):
        self.as_patient()
        res = self.client.get(self._slots_url(date='not-a-date'))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_slots_inactive_weekday_returns_empty(self):
        # A date whose weekday has no active working hours.
        other = self.future_date + datetime.timedelta(days=1)
        self.as_patient()
        res = self.client.get(self._slots_url(date=other.isoformat()))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['slots'], [])

    def test_slots_excludes_booked_window(self):
        self._make_appointment(starts_at=self._future_dt(10), status=Appointment.STATUS_CONFIRMED)
        self.as_patient()
        res = self.client.get(self._slots_url())
        starts = [s['starts_at'] for s in res.data['slots']]
        booked = self._future_dt(10).isoformat()
        self.assertNotIn(booked, starts)
        self.assertEqual(len(res.data['slots']), 15)

    def test_slots_unknown_workplace_returns_404(self):
        self.as_patient()
        res = self.client.get(self._slots_url(workplace=uuid.uuid4()))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class BookingTests(AppointmentTestBase):
    def _booking_payload(self, **kwargs):
        data = {
            'doctor_id': str(self.doctor.pk),
            'workplace_id': str(self.workplace.pk),
            'starts_at': self._future_dt(11).isoformat(),
            'reason': 'Checkup',
        }
        data.update(kwargs)
        return data

    def test_patient_can_book_returns_201(self):
        self.as_patient()
        res = self.client.post(APPOINTMENTS_URL, self._booking_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['status'], Appointment.STATUS_PENDING)
        self.assertEqual(res.data['reason'], 'Checkup')

    def test_booking_in_past_returns_400(self):
        self.as_patient()
        past = (timezone.now() - datetime.timedelta(days=1)).isoformat()
        res = self.client.post(APPOINTMENTS_URL, self._booking_payload(starts_at=past), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_booking_overlapping_slot_returns_400(self):
        self._make_appointment(starts_at=self._future_dt(11), status=Appointment.STATUS_CONFIRMED)
        self.as_patient()
        res = self.client.post(APPOINTMENTS_URL, self._booking_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_booking_unknown_doctor_returns_400(self):
        self.as_patient()
        res = self.client.post(
            APPOINTMENTS_URL, self._booking_payload(doctor_id=str(uuid.uuid4())), format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_doctor_cannot_book(self):
        self.as_doctor()
        res = self.client.post(APPOINTMENTS_URL, self._booking_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_booking_requires_auth(self):
        res = self.client.post(APPOINTMENTS_URL, self._booking_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class PatientAppointmentTests(AppointmentTestBase):
    def test_list_returns_only_own(self):
        other = User.objects.create_user(
            email='p2@test.com', password='Pass1234', role='patient',
            first_name='P', last_name='2',
        )
        self._make_appointment()
        self._make_appointment(patient=other, starts_at=self._future_dt(12))
        self.as_patient()
        res = self.client.get(APPOINTMENTS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 1)

    def test_list_status_filter(self):
        self._make_appointment(status=Appointment.STATUS_PENDING)
        self._make_appointment(starts_at=self._future_dt(12), status=Appointment.STATUS_CONFIRMED)
        self.as_patient()
        res = self.client.get(f'{APPOINTMENTS_URL}?status=confirmed')
        self.assertEqual(res.data['count'], 1)

    def test_detail_returns_200(self):
        appt = self._make_appointment()
        self.as_patient()
        res = self.client.get(f'{APPOINTMENTS_URL}{appt.pk}/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_cannot_view_other_patients_appointment(self):
        other = User.objects.create_user(
            email='p2@test.com', password='Pass1234', role='patient',
            first_name='P', last_name='2',
        )
        appt = self._make_appointment(patient=other)
        self.as_patient()
        res = self.client.get(f'{APPOINTMENTS_URL}{appt.pk}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_cancel_pending_returns_204(self):
        appt = self._make_appointment(status=Appointment.STATUS_PENDING)
        self.as_patient()
        res = self.client.delete(f'{APPOINTMENTS_URL}{appt.pk}/')
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.STATUS_CANCELLED)

    def test_cancel_completed_returns_409(self):
        appt = self._make_appointment(status=Appointment.STATUS_COMPLETED)
        self.as_patient()
        res = self.client.delete(f'{APPOINTMENTS_URL}{appt.pk}/')
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)

    def test_cancel_within_2_hours_returns_409(self):
        soon = timezone.now() + datetime.timedelta(hours=1)
        appt = self._make_appointment(starts_at=soon, status=Appointment.STATUS_CONFIRMED)
        self.as_patient()
        res = self.client.delete(f'{APPOINTMENTS_URL}{appt.pk}/')
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)

    def test_reschedule_from_requires_rescheduling_bypasses_2h_window(self):
        # The doctor asked to move it, so even if the original slot is within the
        # 2-hour window the patient must still be able to pick a new time.
        soon = timezone.now() + datetime.timedelta(hours=1)
        appt = self._make_appointment(
            starts_at=soon, status=Appointment.STATUS_REQUIRES_RESCHEDULING,
        )
        new_time = self._future_dt(11)
        self.as_patient()
        res = self.client.patch(
            f'{APPOINTMENTS_URL}{appt.pk}/reschedule/',
            {'starts_at': new_time.isoformat()}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.STATUS_PENDING)

    def test_can_cancel_flags_true_for_future_confirmed(self):
        appt = self._make_appointment(status=Appointment.STATUS_CONFIRMED)
        self.as_patient()
        res = self.client.get(f'{APPOINTMENTS_URL}{appt.pk}/')
        self.assertTrue(res.data['can_cancel'])
        self.assertTrue(res.data['can_reschedule'])

    def test_can_cancel_flag_false_within_window(self):
        soon = timezone.now() + datetime.timedelta(hours=1)
        appt = self._make_appointment(starts_at=soon, status=Appointment.STATUS_CONFIRMED)
        self.as_patient()
        res = self.client.get(f'{APPOINTMENTS_URL}{appt.pk}/')
        self.assertFalse(res.data['can_cancel'])

    def test_can_reschedule_flag_true_for_requires_rescheduling(self):
        soon = timezone.now() + datetime.timedelta(hours=1)
        appt = self._make_appointment(
            starts_at=soon, status=Appointment.STATUS_REQUIRES_RESCHEDULING,
        )
        self.as_patient()
        res = self.client.get(f'{APPOINTMENTS_URL}{appt.pk}/')
        self.assertFalse(res.data['can_cancel'])
        self.assertTrue(res.data['can_reschedule'])

    def test_can_cancel_respects_per_doctor_window(self):
        # Doctor widens the window to 24h → an appointment 5h away is no longer
        # cancellable (would be cancellable under the default 2h window).
        self.doctor.doctor_profile.cancellation_window_hours = 24
        self.doctor.doctor_profile.save(update_fields=['cancellation_window_hours'])
        in_5h = timezone.now() + datetime.timedelta(hours=5)
        appt = self._make_appointment(starts_at=in_5h, status=Appointment.STATUS_CONFIRMED)
        self.as_patient()
        res = self.client.get(f'{APPOINTMENTS_URL}{appt.pk}/')
        self.assertFalse(res.data['can_cancel'])


class DoctorAppointmentTests(AppointmentTestBase):
    def test_doctor_list_returns_only_own(self):
        self._make_appointment()
        self.as_doctor()
        res = self.client.get(DOCTOR_APPOINTMENTS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 1)

    def test_patient_cannot_use_doctor_list(self):
        self.as_patient()
        res = self.client.get(DOCTOR_APPOINTMENTS_URL)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_doctor_list_invalid_date_filter_returns_400(self):
        self.as_doctor()
        res = self.client.get(f'{DOCTOR_APPOINTMENTS_URL}?date=bad')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_confirm_pending_appointment(self):
        appt = self._make_appointment(status=Appointment.STATUS_PENDING)
        self.as_doctor()
        res = self.client.patch(
            f'{DOCTOR_APPOINTMENTS_URL}{appt.pk}/status/', {'status': 'confirmed'}, format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], Appointment.STATUS_CONFIRMED)

    def test_decline_pending_appointment(self):
        appt = self._make_appointment(status=Appointment.STATUS_PENDING)
        self.as_doctor()
        res = self.client.patch(
            f'{DOCTOR_APPOINTMENTS_URL}{appt.pk}/status/', {'status': 'declined'}, format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], Appointment.STATUS_DECLINED)

    def test_confirm_non_pending_returns_409(self):
        appt = self._make_appointment(status=Appointment.STATUS_CONFIRMED)
        self.as_doctor()
        res = self.client.patch(
            f'{DOCTOR_APPOINTMENTS_URL}{appt.pk}/status/', {'status': 'confirmed'}, format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)

    def test_status_invalid_value_returns_400(self):
        appt = self._make_appointment(status=Appointment.STATUS_PENDING)
        self.as_doctor()
        res = self.client.patch(
            f'{DOCTOR_APPOINTMENTS_URL}{appt.pk}/status/', {'status': 'banana'}, format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_request_reschedule_on_confirmed(self):
        appt = self._make_appointment(status=Appointment.STATUS_CONFIRMED)
        self.as_doctor()
        res = self.client.patch(
            f'{DOCTOR_APPOINTMENTS_URL}{appt.pk}/status/',
            {'status': 'requires_rescheduling'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], Appointment.STATUS_REQUIRES_RESCHEDULING)

    def test_request_reschedule_on_pending_returns_409(self):
        appt = self._make_appointment(status=Appointment.STATUS_PENDING)
        self.as_doctor()
        res = self.client.patch(
            f'{DOCTOR_APPOINTMENTS_URL}{appt.pk}/status/',
            {'status': 'requires_rescheduling'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)

    def test_mark_no_show_on_confirmed(self):
        appt = self._make_appointment(status=Appointment.STATUS_CONFIRMED)
        self.as_doctor()
        res = self.client.patch(
            f'{DOCTOR_APPOINTMENTS_URL}{appt.pk}/status/',
            {'status': 'no_show'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], Appointment.STATUS_NO_SHOW)

    def test_mark_no_show_on_pending_returns_409(self):
        appt = self._make_appointment(status=Appointment.STATUS_PENDING)
        self.as_doctor()
        res = self.client.patch(
            f'{DOCTOR_APPOINTMENTS_URL}{appt.pk}/status/',
            {'status': 'no_show'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)

    def test_update_notes(self):
        appt = self._make_appointment(status=Appointment.STATUS_CONFIRMED)
        self.as_doctor()
        res = self.client.patch(
            f'{DOCTOR_APPOINTMENTS_URL}{appt.pk}/notes/', {'notes': 'Patient is fine'}, format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['notes'], 'Patient is fine')

    def test_doctor_cannot_touch_other_doctors_appointment(self):
        other = User.objects.create_user(
            email='d2@test.com', password='Pass1234', role='doctor',
            first_name='D', last_name='2',
        )
        appt = self._make_appointment(doctor=other, status=Appointment.STATUS_PENDING)
        self.as_doctor()
        res = self.client.patch(
            f'{DOCTOR_APPOINTMENTS_URL}{appt.pk}/status/', {'status': 'confirmed'}, format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
