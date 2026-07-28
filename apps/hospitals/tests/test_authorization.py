import datetime
import uuid

from django.utils import timezone
from rest_framework import status

from apps.appointments.models import Appointment
from apps.appointments.tests.test_appointments import _register_and_login, patient_payload
from apps.doctors.models import Workplace
from apps.hospitals.models import HospitalDoctorLink
from apps.users.models import User

from .base import (
    HOSPITAL_APPOINTMENTS_URL,
    HOSPITAL_DOCTOR_INVITE_URL,
    HOSPITAL_DOCTOR_SEARCH_URL,
    HOSPITAL_LINKS_URL,
    WORKPLACES_URL,
    HospitalDashboardTestBase,
    HospitalTestBase,
    approve_hospital,
    doctor_workplaces_url,
    link_approve_url,
    link_reject_url,
    link_remove_url,
    workplace_hours_url,
)


class HoursEditingAuthorizationTests(HospitalDashboardTestBase):
    """Trap: Workplace.hospital is set by the *doctor* at request time,
    before any confirmation — resolving a workplace by that FK alone would
    let a hospital rewrite the schedule of a doctor who merely asked to
    join. Must also require a CONFIRMED HospitalDoctorLink."""

    def _create_workplace_at_hospital(self):
        self.as_doctor()
        res = self.client.post(WORKPLACES_URL, {
            'address': '1 Main St', 'city': 'baku', 'type': 'hospital',
            'hospital': str(self.hospital.id),
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        return res.data

    def test_hospital_cannot_edit_hours_for_doctor_with_only_a_pending_link(self):
        wp_data = self._create_workplace_at_hospital()
        # Link is still pending — never approved.
        self.as_hospital()
        res = self.client.put(workplace_hours_url(wp_data['id']), [
            {'weekday': 0, 'start_time': '08:00', 'end_time': '20:00', 'is_active': True},
        ], format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

        # Hours are genuinely unchanged.
        workplace = Workplace.objects.get(pk=wp_data['id'])
        self.assertFalse(workplace.working_hours.filter(weekday=0, is_active=True).exists())

    def test_hospital_cannot_edit_hours_after_link_removed(self):
        wp_data = self._create_workplace_at_hospital()
        link = HospitalDoctorLink.objects.get(hospital=self.hospital, doctor=self.doctor)
        self.as_hospital()
        self.client.post(link_approve_url(link.id))
        self.client.delete(link_remove_url(link.id))

        res = self.client.put(workplace_hours_url(wp_data['id']), [
            {'weekday': 0, 'start_time': '08:00', 'end_time': '20:00', 'is_active': True},
        ], format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_hospital_can_edit_hours_for_a_confirmed_doctor(self):
        wp_data = self._create_workplace_at_hospital()
        link = HospitalDoctorLink.objects.get(hospital=self.hospital, doctor=self.doctor)
        self.as_hospital()
        self.client.post(link_approve_url(link.id))

        res = self.client.put(workplace_hours_url(wp_data['id']), [
            {'weekday': 0, 'start_time': '08:00', 'end_time': '20:00', 'is_active': True},
        ], format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        workplace = Workplace.objects.get(pk=wp_data['id'])
        self.assertTrue(workplace.working_hours.filter(weekday=0, is_active=True, start_time='08:00:00').exists())

    def test_hospital_cannot_edit_hours_for_another_hospitals_workplace(self):
        wp_data = self._create_workplace_at_hospital()
        link = HospitalDoctorLink.objects.get(hospital=self.hospital, doctor=self.doctor)
        self.as_hospital()
        self.client.post(link_approve_url(link.id))

        from apps.hospitals.models import Hospital
        from .base import activate_hospital_subscription, hospital_payload
        other_token = _register_and_login(self.client, hospital_payload(
            email='other_hospital@test.com', hospital_name='Other Hospital', hospital_city='ganja',
        ))
        other_user = User.objects.get(email='other_hospital@test.com')
        other_hospital = Hospital.objects.get(owner=other_user)
        approve_hospital(other_hospital)
        activate_hospital_subscription(other_user)

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.get(workplace_hours_url(wp_data['id']))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_hospital_doctor_workplaces_endpoint_requires_confirmed_link(self):
        self._create_workplace_at_hospital()
        self.as_hospital()
        res = self.client.get(doctor_workplaces_url(self.doctor.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class AppointmentFeedAuthorizationTests(HospitalDashboardTestBase):
    def _confirmed_workplace_and_appointment(self):
        self.as_doctor()
        wp_res = self.client.post(WORKPLACES_URL, {
            'address': '1 Main St', 'city': 'baku', 'type': 'hospital',
            'hospital': str(self.hospital.id),
        }, format='json')
        workplace = Workplace.objects.get(pk=wp_res.data['id'])
        link = HospitalDoctorLink.objects.get(hospital=self.hospital, doctor=self.doctor)
        self.as_hospital()
        self.client.post(link_approve_url(link.id))

        patient_token = _register_and_login(self.client, patient_payload())
        patient = User.objects.get(email='patient@test.com')
        starts = timezone.now() + datetime.timedelta(days=5)
        appt = Appointment.objects.create(
            doctor=self.doctor, patient=patient, workplace=workplace,
            starts_at=starts, ends_at=starts + datetime.timedelta(minutes=30),
            status=Appointment.STATUS_CONFIRMED,
            reason='Chest pain, family history of heart disease',
            notes='Patient reports symptoms consistent with anxiety, not cardiac.',
        )
        return workplace, appt

    def test_hospital_appointments_payload_contains_no_patient_name_reason_or_notes(self):
        _, appt = self._confirmed_workplace_and_appointment()
        self.as_hospital()
        res = self.client.get(HOSPITAL_APPOINTMENTS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 1)
        row = res.data['results'][0]

        # Exact key set, not just "doesn't contain X" — a future field added
        # to this serializer must fail this test loudly rather than silently
        # widening what a hospital can see.
        self.assertEqual(set(row.keys()), {'id', 'doctor', 'workplace', 'starts_at', 'ends_at', 'status'})
        self.assertEqual(set(row['doctor'].keys()), {
            'id', 'first_name', 'last_name', 'specialization', 'specialization_display',
        })
        self.assertEqual(set(row['workplace'].keys()), {'id', 'name'})

        payload_str = str(res.data)
        self.assertNotIn('Chest pain', payload_str)
        self.assertNotIn('anxiety', payload_str)
        self.assertNotIn('patient@test.com', payload_str)

    def test_hospital_appointments_excludes_the_doctors_other_workplaces(self):
        _, hospital_appt = self._confirmed_workplace_and_appointment()

        # A second, private-practice workplace for the same doctor, with its
        # own appointment — must never show up in the hospital's feed.
        self.as_doctor()
        private_wp = Workplace.objects.create(
            doctor=self.doctor, name='Private Office', address='9 Side St',
            city='baku', region='baku', type='private',
        )
        patient = User.objects.get(email='patient@test.com')
        starts = timezone.now() + datetime.timedelta(days=6)
        private_appt = Appointment.objects.create(
            doctor=self.doctor, patient=patient, workplace=private_wp,
            starts_at=starts, ends_at=starts + datetime.timedelta(minutes=30),
            status=Appointment.STATUS_CONFIRMED,
        )

        self.as_hospital()
        res = self.client.get(HOSPITAL_APPOINTMENTS_URL)
        ids = [row['id'] for row in res.data['results']]
        self.assertIn(str(hospital_appt.id), ids)
        self.assertNotIn(str(private_appt.id), ids)


class DoctorSearchAuthorizationTests(HospitalDashboardTestBase):
    def test_hospital_doctor_search_does_not_expose_emails(self):
        self.as_hospital()
        res = self.client.get(HOSPITAL_DOCTOR_SEARCH_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(res.data['count'], 1)
        row = res.data['results'][0]
        self.assertEqual(set(row.keys()), {
            'id', 'first_name', 'last_name', 'specialization', 'specialization_display',
        })
        self.assertNotIn('email', str(res.data))

    def test_search_excludes_doctors_with_an_active_link_already(self):
        self.as_hospital()
        self.client.post(HOSPITAL_DOCTOR_INVITE_URL, {'doctor_id': str(self.doctor.id)}, format='json')
        res = self.client.get(HOSPITAL_DOCTOR_SEARCH_URL)
        ids = [row['id'] for row in res.data['results']]
        self.assertNotIn(str(self.doctor.id), ids)


class UnsubscribedHospitalAuthorizationTests(HospitalTestBase):
    """An approved-but-not-yet-subscribed hospital must 403 on every
    dashboard endpoint (no trial — see plans.TRIAL_ROLES) while still being
    able to reach /profile/ and /status/."""

    def setUp(self):
        super().setUp()
        approve_hospital(self.hospital)
        self.as_hospital()

    def test_unsubscribed_hospital_403s_on_every_dashboard_endpoint(self):
        random_id = uuid.uuid4()
        endpoints = [
            ('get', HOSPITAL_LINKS_URL),
            ('post', link_approve_url(random_id)),
            ('post', link_reject_url(random_id)),
            ('delete', link_remove_url(random_id)),
            ('get', HOSPITAL_DOCTOR_SEARCH_URL),
            ('post', HOSPITAL_DOCTOR_INVITE_URL),
            ('get', doctor_workplaces_url(random_id)),
            ('get', workplace_hours_url(random_id)),
            ('put', workplace_hours_url(random_id)),
            ('get', HOSPITAL_APPOINTMENTS_URL),
        ]
        for method, url in endpoints:
            with self.subTest(method=method, url=url):
                res = getattr(self.client, method)(url)
                self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(res.data['code'], 'subscription_required')

    def test_unsubscribed_hospital_can_still_reach_profile_and_status(self):
        from .base import HOSPITAL_PROFILE_URL, HOSPITAL_STATUS_URL
        self.assertEqual(self.client.get(HOSPITAL_PROFILE_URL).status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(HOSPITAL_STATUS_URL).status_code, status.HTTP_200_OK)


class CrossRoleAuthorizationTests(HospitalDashboardTestBase):
    def test_doctor_token_cannot_call_hospital_endpoints(self):
        self.as_doctor()
        res = self.client.get(HOSPITAL_LINKS_URL)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_patient_token_cannot_call_hospital_endpoints(self):
        patient_token = _register_and_login(self.client, patient_payload())
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {patient_token}')
        res = self.client.get(HOSPITAL_LINKS_URL)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
