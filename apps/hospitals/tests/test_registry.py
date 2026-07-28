from rest_framework import status

from apps.appointments.tests.test_appointments import AppointmentTestBase, _register_and_login, doctor_payload
from apps.hospitals.models import Hospital
from apps.users.models import User

from .base import HOSPITALS_URL


class RegistryCreateTests(AppointmentTestBase):
    """AppointmentTestBase gives a verified doctor (self.doctor) and a
    patient (self.patient) with tokens — exactly what the registry
    endpoints need."""

    def test_doctor_can_create_registry_entry_from_free_text(self):
        self.as_doctor()
        res = self.client.post(HOSPITALS_URL, {'name': 'New City Clinic', 'city': 'baku'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['status'], Hospital.STATUS_PENDING_REVIEW)
        self.assertEqual(res.data['name'], 'New City Clinic')

    def test_duplicate_name_returns_existing_entry_not_a_second_row(self):
        self.as_doctor()
        first = self.client.post(HOSPITALS_URL, {'name': 'Bakı Klinikası', 'city': 'baku'}, format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        second = self.client.post(HOSPITALS_URL, {'name': 'Baki Klinikasi', 'city': 'baku'}, format='json')
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data['id'], first.data['id'])
        self.assertEqual(Hospital.objects.count(), 1)

    def test_duplicate_detection_is_scoped_to_city(self):
        self.as_doctor()
        first = self.client.post(HOSPITALS_URL, {'name': 'Central Hospital', 'city': 'baku'}, format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        second = self.client.post(HOSPITALS_URL, {'name': 'Central Hospital', 'city': 'ganja'}, format='json')
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(second.data['id'], first.data['id'])
        self.assertEqual(Hospital.objects.count(), 2)

    def test_new_entry_is_visible_to_other_doctors_immediately(self):
        self.as_doctor()
        create_res = self.client.post(HOSPITALS_URL, {'name': 'Fresh Clinic', 'city': 'baku'}, format='json')
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED)

        token2 = _register_and_login(self.client, doctor_payload(email='doctor2@test.com'))
        doc2 = User.objects.get(email='doctor2@test.com')
        doc2.doctor_profile.is_verified = True
        doc2.doctor_profile.save(update_fields=['is_verified'])
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token2}')

        list_res = self.client.get(HOSPITALS_URL, {'city': 'baku'})
        names = [row['name'] for row in list_res.data['results']]
        self.assertIn('Fresh Clinic', names)

    def test_merged_and_rejected_entries_excluded_from_search(self):
        rejected = Hospital.objects.create(name='Junk Entry', city='baku', status=Hospital.STATUS_REJECTED)
        merged = Hospital.objects.create(name='Absorbed Clinic', city='baku', status=Hospital.STATUS_MERGED)
        self.as_doctor()

        res = self.client.get(HOSPITALS_URL, {'city': 'baku'})
        ids = [row['id'] for row in res.data['results']]
        self.assertNotIn(str(rejected.id), ids)
        self.assertNotIn(str(merged.id), ids)

    def test_confirmed_entries_are_visible(self):
        confirmed = Hospital.objects.create(name='Vetted Hospital', city='baku', status=Hospital.STATUS_CONFIRMED)
        self.as_doctor()
        res = self.client.get(HOSPITALS_URL, {'city': 'baku'})
        ids = [row['id'] for row in res.data['results']]
        self.assertIn(str(confirmed.id), ids)

    def test_patient_cannot_create_registry_entry(self):
        self.as_patient()
        res = self.client.post(HOSPITALS_URL, {'name': 'X', 'city': 'baku'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_unverified_doctor_cannot_create_registry_entry(self):
        token = _register_and_login(self.client, doctor_payload(email='unverified@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        res = self.client.post(HOSPITALS_URL, {'name': 'X', 'city': 'baku'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_can_list_registry(self):
        # AllowAny by design: a not-yet-registered hospital must be able to
        # search the registry for itself before it has any account or token
        # — see apps.users.serializers.RegisterSerializer's claim-or-create
        # branch and HospitalListCreateView's docstring.
        self.client.credentials()
        res = self.client.get(HOSPITALS_URL, {'city': 'baku'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_anonymous_cannot_add_a_registry_entry(self):
        # POST stays doctor-only — only GET (search) is public.
        self.client.credentials()
        res = self.client.post(HOSPITALS_URL, {'name': 'X', 'city': 'baku'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
