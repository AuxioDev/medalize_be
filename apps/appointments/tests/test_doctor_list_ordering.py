import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status

from apps.appointments.models import Appointment, Review
from apps.doctors.models import Workplace, WorkingHours
from .test_appointments import AppointmentTestBase, DOCTORS_URL

User = get_user_model()


def _create_verified_doctor(email, first_name, last_name):
    doctor = User.objects.create_user(
        email=email, password='Pass1234', role='doctor',
        first_name=first_name, last_name=last_name,
    )
    doctor.doctor_profile.is_verified = True
    doctor.doctor_profile.save(update_fields=['is_verified'])
    return doctor


class OrderingTestBase(AppointmentTestBase):
    def _names(self, res):
        return [d['first_name'] for d in res.data['results']]

    def _create_workplace(self, doctor, latitude=None, longitude=None):
        return Workplace.objects.create(
            doctor=doctor, name=f'{doctor.first_name} Clinic', address='1 Test St',
            city='Baku', type='clinic', is_primary=True,
            latitude=latitude, longitude=longitude,
        )

    def _add_review(self, doctor, workplace, rating, slot_index):
        starts = timezone.now() - datetime.timedelta(days=30) + datetime.timedelta(minutes=30 * slot_index)
        appointment = Appointment.objects.create(
            doctor=doctor, patient=self.patient, workplace=workplace,
            starts_at=starts, ends_at=starts + datetime.timedelta(minutes=30),
            status=Appointment.STATUS_COMPLETED,
        )
        return Review.objects.create(
            appointment=appointment, doctor=doctor, patient=self.patient, rating=rating,
        )


class RatingOrderingTests(OrderingTestBase):
    def setUp(self):
        super().setUp()
        # Base doctor John has no reviews. Zara: 5.0, Bob: 3.0.
        self.zara = _create_verified_doctor('zara@test.com', 'Zara', 'High')
        zara_wp = self._create_workplace(self.zara)
        self._add_review(self.zara, zara_wp, 5, 0)
        self._add_review(self.zara, zara_wp, 5, 1)

        self.bob = _create_verified_doctor('bob@test.com', 'Bob', 'Low')
        bob_wp = self._create_workplace(self.bob)
        self._add_review(self.bob, bob_wp, 3, 0)

    def test_ordering_rating_desc(self):
        self.as_patient()
        res = self.client.get(f'{DOCTORS_URL}?ordering=-rating')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(self._names(res), ['Zara', 'Bob', 'John'])
        self.assertEqual(res.data['results'][0]['average_rating'], 5.0)
        self.assertIsNone(res.data['results'][2]['average_rating'])

    def test_ordering_rating_asc_keeps_unrated_last(self):
        self.as_patient()
        res = self.client.get(f'{DOCTORS_URL}?ordering=rating')
        self.assertEqual(self._names(res), ['Bob', 'Zara', 'John'])

    def test_unknown_ordering_falls_back_to_alphabetical(self):
        self.as_patient()
        res = self.client.get(f'{DOCTORS_URL}?ordering=bogus')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(self._names(res), ['Bob', 'John', 'Zara'])

    def test_no_ordering_keeps_alphabetical(self):
        self.as_patient()
        res = self.client.get(DOCTORS_URL)
        self.assertEqual(self._names(res), ['Bob', 'John', 'Zara'])


class NextSlotOrderingTests(OrderingTestBase):
    def setUp(self):
        super().setUp()
        # Base doctor John works on the weekday of today+8, whose first
        # occurrence inside the 14-day window is tomorrow (today+1).
        today = timezone.now().date()

        # Alice's first working day inside the window is today+5.
        self.alice = _create_verified_doctor('alice@test.com', 'Alice', 'Later')
        alice_wp = self._create_workplace(self.alice)
        WorkingHours.objects.create(
            workplace=alice_wp, weekday=(today + datetime.timedelta(days=5)).weekday(),
            start_time='09:00', end_time='17:00', is_active=True,
        )

        # Aaron has no working hours at all — no slot in the window.
        self.aaron = _create_verified_doctor('aaron@test.com', 'Aaron', 'Empty')
        self._create_workplace(self.aaron)

    def test_ordering_next_slot(self):
        self.as_patient()
        res = self.client.get(f'{DOCTORS_URL}?ordering=next_slot')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(self._names(res), ['John', 'Alice', 'Aaron'])
        results = res.data['results']
        self.assertIsNotNone(results[0]['next_slot_at'])
        self.assertIsNotNone(results[1]['next_slot_at'])
        self.assertIsNone(results[2]['next_slot_at'])
        self.assertLess(results[0]['next_slot_at'], results[1]['next_slot_at'])

    def test_next_slot_view_still_works_via_shared_helper(self):
        self.as_patient()
        res = self.client.get(f'{DOCTORS_URL}{self.doctor.pk}/next-slot/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(res.data['next_available_date'])
        res = self.client.get(f'{DOCTORS_URL}{self.aaron.pk}/next-slot/')
        self.assertIsNone(res.data['next_available_date'])

    def test_next_slot_at_is_null_without_ordering(self):
        self.as_patient()
        res = self.client.get(DOCTORS_URL)
        self.assertTrue(all(d['next_slot_at'] is None for d in res.data['results']))


BAKU = {'lat': '40.409300', 'lng': '49.867100'}


class DistanceOrderingTests(OrderingTestBase):
    def setUp(self):
        super().setUp()
        # Base doctor John's workplace has no coordinates.
        self.near = _create_verified_doctor('near@test.com', 'Nigar', 'Near')
        self._create_workplace(
            self.near, latitude=Decimal('40.410000'), longitude=Decimal('49.870000')
        )
        self.far = _create_verified_doctor('far@test.com', 'Farid', 'Far')
        self._create_workplace(
            self.far, latitude=Decimal('40.682800'), longitude=Decimal('46.360600')
        )

    def _get(self, **params):
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        return self.client.get(f'{DOCTORS_URL}?{query}')

    def test_ordering_distance_nearest_first_no_coords_last(self):
        self.as_patient()
        res = self._get(ordering='distance', **BAKU)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(self._names(res), ['Nigar', 'Farid', 'John'])
        results = res.data['results']
        self.assertLess(results[0]['distance_km'], 1)
        self.assertGreater(results[1]['distance_km'], 200)
        self.assertIsNone(results[2]['distance_km'])

    def test_ordering_distance_without_coordinates_returns_400(self):
        self.as_patient()
        res = self._get(ordering='distance')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_ordering_distance_with_invalid_coordinates_returns_400(self):
        self.as_patient()
        res = self._get(ordering='distance', lat='abc', lng='49.8')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        res = self._get(ordering='distance', lat='40.4')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_distance_km_annotated_without_distance_ordering(self):
        self.as_patient()
        res = self._get(ordering='-rating', **BAKU)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        by_name = {d['first_name']: d for d in res.data['results']}
        self.assertIsNotNone(by_name['Nigar']['distance_km'])
        self.assertIsNone(by_name['John']['distance_km'])

    def test_distance_km_null_when_no_coordinates_sent(self):
        self.as_patient()
        res = self.client.get(DOCTORS_URL)
        self.assertTrue(all(d['distance_km'] is None for d in res.data['results']))
