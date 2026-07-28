from rest_framework import status

from apps.hospitals.models import Hospital
from apps.users.models import User

from .base import (
    HOSPITAL_SUBSCRIPTION_URL,
    ME_URL,
    REGISTER_URL,
    HospitalTestBase,
    hospital_payload,
)


class HospitalRegistrationTests(HospitalTestBase):
    def test_register_hospital_creates_entry_with_pending_claim(self):
        self.assertEqual(self.hospital.claim_status, Hospital.CLAIM_PENDING)
        self.assertEqual(self.hospital.owner_id, self.hospital_user.id)
        # A brand-new, doctor-never-touched entry is also unvetted on the
        # registry axis — same STATUS_PENDING_REVIEW a doctor's "add your
        # variant" would get.
        self.assertEqual(self.hospital.status, Hospital.STATUS_PENDING_REVIEW)

    def test_registration_requires_hospital_name_and_city_or_claim(self):
        res = self.client.post(REGISTER_URL, hospital_payload(
            email='incomplete@test.com', hospital_name='', hospital_city='',
        ), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email='incomplete@test.com').exists())

    def test_claiming_existing_entry_sets_claim_pending_not_approved(self):
        existing = Hospital.objects.create(
            name='Existing Hospital', city='ganja', status=Hospital.STATUS_CONFIRMED,
        )
        res = self.client.post(REGISTER_URL, hospital_payload(
            email='claimer@test.com', hospital_name='', hospital_city='',
            hospital_id=str(existing.id),
        ), format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        existing.refresh_from_db()
        self.assertEqual(existing.claim_status, Hospital.CLAIM_PENDING)
        self.assertEqual(existing.owner.email, 'claimer@test.com')
        # Claiming a pre-existing, already-vetted entry must not touch its
        # registry status.
        self.assertEqual(existing.status, Hospital.STATUS_CONFIRMED)

    def test_cannot_claim_an_already_owned_entry(self):
        res = self.client.post(REGISTER_URL, hospital_payload(
            email='second_claimer@test.com', hospital_name='', hospital_city='',
            hospital_id=str(self.hospital.id),
        ), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        # The whole registration must roll back — a 'hospital' User must
        # never exist without a Hospital attached.
        self.assertFalse(User.objects.filter(email='second_claimer@test.com').exists())

    def test_cannot_claim_a_rejected_entry(self):
        rejected = Hospital.objects.create(name='Junk', city='baku', status=Hospital.STATUS_REJECTED)
        res = self.client.post(REGISTER_URL, hospital_payload(
            email='claimer2@test.com', hospital_name='', hospital_city='',
            hospital_id=str(rejected.id),
        ), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email='claimer2@test.com').exists())

    def test_unapproved_hospital_gets_403_with_hospital_not_approved_code(self):
        self.as_hospital()
        res = self.client.get(HOSPITAL_SUBSCRIPTION_URL)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data['code'], 'hospital_not_approved')

    def test_me_payload_includes_hospital_block(self):
        self.as_hospital()
        res = self.client.get(ME_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['role'], 'hospital')
        self.assertFalse(res.data['is_verified'])
        self.assertEqual(res.data['profile']['claim_status'], Hospital.CLAIM_PENDING)

    def test_doctor_role_unaffected_by_hospital_branch(self):
        """Guards against the hospital branch anywhere in RegisterSerializer/
        build_login_payload/MeSerializer accidentally running for a plain
        doctor registration."""
        from apps.appointments.tests.test_appointments import _register_and_login, doctor_payload
        token = _register_and_login(self.client, doctor_payload(email='plain_doctor@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        res = self.client.get(ME_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['role'], 'doctor')
        self.assertNotIn('claim_status', res.data['profile'])
        self.assertIn('specialization', res.data['profile'])
