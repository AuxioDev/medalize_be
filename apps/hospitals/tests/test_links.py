import datetime

from django.utils import timezone
from rest_framework import status

from apps.appointments.models import Appointment
from apps.appointments.tests.test_appointments import _register_and_login, patient_payload
from apps.doctors.models import Workplace
from apps.hospitals.models import Hospital, HospitalDoctorLink
from apps.users.models import User

from .base import (
    DOCTOR_HOSPITAL_LINKS_URL,
    HOSPITAL_DOCTOR_INVITE_URL,
    HOSPITAL_LINKS_URL,
    WORKPLACES_URL,
    HospitalDashboardTestBase,
    activate_hospital_subscription,
    approve_hospital,
    doctor_link_accept_url,
    hospital_payload,
    link_approve_url,
    link_reject_url,
    link_remove_url,
)


class LinkRequestTests(HospitalDashboardTestBase):
    def _create_workplace_at_hospital(self):
        self.as_doctor()
        res = self.client.post(WORKPLACES_URL, {
            'address': '1 Main St', 'city': 'baku', 'type': 'hospital',
            'hospital': str(self.hospital.id),
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        return res.data

    def test_picking_hospital_on_workplace_creates_pending_link(self):
        self._create_workplace_at_hospital()
        link = HospitalDoctorLink.objects.get(hospital=self.hospital, doctor=self.doctor)
        self.assertEqual(link.status, HospitalDoctorLink.STATUS_PENDING)
        self.assertEqual(link.requested_by, HospitalDoctorLink.REQUESTED_BY_DOCTOR)

    def test_workplace_without_name_defaults_to_hospital_name(self):
        data = self._create_workplace_at_hospital()
        self.assertEqual(data['name'], self.hospital.name)

    def test_confirmed_doctor_appears_in_our_doctors_pending_does_not(self):
        wp = self._create_workplace_at_hospital()
        link = HospitalDoctorLink.objects.get(hospital=self.hospital, doctor=self.doctor)

        self.as_hospital()
        pending_res = self.client.get(HOSPITAL_LINKS_URL, {'status': 'pending'})
        self.assertEqual(pending_res.data['count'], 1)
        confirmed_res = self.client.get(HOSPITAL_LINKS_URL, {'status': 'confirmed'})
        self.assertEqual(confirmed_res.data['count'], 0)

        approve_res = self.client.post(link_approve_url(link.id))
        self.assertEqual(approve_res.status_code, status.HTTP_200_OK)

        pending_res = self.client.get(HOSPITAL_LINKS_URL, {'status': 'pending'})
        self.assertEqual(pending_res.data['count'], 0)
        confirmed_res = self.client.get(HOSPITAL_LINKS_URL, {'status': 'confirmed'})
        self.assertEqual(confirmed_res.data['count'], 1)
        self.assertEqual(confirmed_res.data['results'][0]['doctor']['id'], str(self.doctor.id))

    def test_reject_link_leaves_workplace_and_appointments_intact(self):
        """The single most important test in the whole feature: a hospital
        decision must never affect the doctor's own operational ability —
        their workplace keeps working and every booked appointment survives
        untouched."""
        wp_data = self._create_workplace_at_hospital()
        workplace = Workplace.objects.get(pk=wp_data['id'])
        link = HospitalDoctorLink.objects.get(hospital=self.hospital, doctor=self.doctor)

        patient_token = _register_and_login(self.client, patient_payload())
        patient = User.objects.get(email='patient@test.com')
        starts = timezone.now() + datetime.timedelta(days=5)
        appt = Appointment.objects.create(
            doctor=self.doctor, patient=patient, workplace=workplace,
            starts_at=starts, ends_at=starts + datetime.timedelta(minutes=30),
            status=Appointment.STATUS_CONFIRMED,
        )

        self.as_hospital()
        res = self.client.post(link_reject_url(link.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Workplace itself: untouched.
        workplace.refresh_from_db()
        self.assertTrue(Workplace.objects.filter(pk=workplace.pk).exists())
        self.assertIsNone(workplace.hospital)

        # The appointment: completely untouched (not cancelled, still
        # confirmed, still pointing at the same workplace).
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.STATUS_CONFIRMED)
        self.assertEqual(appt.workplace_id, workplace.id)

        # The doctor can still see/manage their own workplace.
        self.as_doctor()
        list_res = self.client.get(WORKPLACES_URL)
        self.assertEqual(list_res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_res.data), 1)

    def test_reject_link_nulls_workplace_hospital_fk(self):
        wp_data = self._create_workplace_at_hospital()
        link = HospitalDoctorLink.objects.get(hospital=self.hospital, doctor=self.doctor)

        self.as_hospital()
        self.client.post(link_reject_url(link.id))

        link.refresh_from_db()
        self.assertEqual(link.status, HospitalDoctorLink.STATUS_REJECTED)
        workplace = Workplace.objects.get(pk=wp_data['id'])
        self.assertIsNone(workplace.hospital)

    def test_remove_confirmed_doctor_does_not_cancel_their_appointments(self):
        wp_data = self._create_workplace_at_hospital()
        workplace = Workplace.objects.get(pk=wp_data['id'])
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
        )

        self.as_hospital()
        res = self.client.delete(link_remove_url(link.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        link.refresh_from_db()
        self.assertEqual(link.status, HospitalDoctorLink.STATUS_REMOVED)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.STATUS_CONFIRMED)

    def test_invite_then_accept_confirms_link(self):
        self.as_hospital()
        res = self.client.post(HOSPITAL_DOCTOR_INVITE_URL, {'doctor_id': str(self.doctor.id)}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        link_id = res.data['id']

        link = HospitalDoctorLink.objects.get(pk=link_id)
        self.assertEqual(link.status, HospitalDoctorLink.STATUS_INVITED)
        self.assertEqual(link.requested_by, HospitalDoctorLink.REQUESTED_BY_HOSPITAL)

        self.as_doctor()
        list_res = self.client.get(DOCTOR_HOSPITAL_LINKS_URL)
        self.assertEqual(len(list_res.data), 1)
        self.assertEqual(list_res.data[0]['status'], HospitalDoctorLink.STATUS_INVITED)

        accept_res = self.client.post(doctor_link_accept_url(link_id))
        self.assertEqual(accept_res.status_code, status.HTTP_200_OK)
        link.refresh_from_db()
        self.assertEqual(link.status, HospitalDoctorLink.STATUS_CONFIRMED)
        self.assertEqual(link.decided_by_id, self.doctor.id)

    def test_link_cannot_be_approved_by_a_different_hospital(self):
        wp_data = self._create_workplace_at_hospital()
        link = HospitalDoctorLink.objects.get(hospital=self.hospital, doctor=self.doctor)

        other_token = _register_and_login(self.client, hospital_payload(
            email='other_hospital@test.com', hospital_name='Other Hospital', hospital_city='ganja',
        ))
        other_user = User.objects.get(email='other_hospital@test.com')
        other_hospital = Hospital.objects.get(owner=other_user)
        approve_hospital(other_hospital)
        activate_hospital_subscription(other_user)

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.post(link_approve_url(link.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

        link.refresh_from_db()
        self.assertEqual(link.status, HospitalDoctorLink.STATUS_PENDING)

    def test_doctor_token_cannot_call_hospital_approve_endpoint(self):
        wp_data = self._create_workplace_at_hospital()
        link = HospitalDoctorLink.objects.get(hospital=self.hospital, doctor=self.doctor)

        self.as_doctor()
        res = self.client.post(link_approve_url(link.id))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
