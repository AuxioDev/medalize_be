from rest_framework import status

from apps.appointments.models import Appointment
from apps.appointments.tests.test_appointments import AppointmentTestBase, _register_and_login, patient_payload
from apps.family.models import Dependent
from apps.medications.models import Medication
from apps.notifications.models import Notification
from apps.notifications.tasks import send_prescription_issued
from apps.prescriptions.models import Prescription, PrescriptionItem


def _prescription_url(appointment_id):
    return f'/api/appointments/{appointment_id}/prescription/'


def _detail_url(pk):
    return f'/api/prescriptions/{pk}/'


def _apply_url(pk):
    return f'/api/prescriptions/{pk}/apply/'


LIST_URL = '/api/prescriptions/'


def _items_payload():
    return [
        {'drug_name': 'Amoxicillin', 'dosage': '500mg', 'frequency': 'Twice a day',
         'duration': '7 days', 'instructions': 'Take with food'},
        {'drug_name': 'Paracetamol', 'dosage': '500mg', 'frequency': 'As needed',
         'duration': '', 'instructions': ''},
    ]


class PrescriptionTestBase(AppointmentTestBase):
    def setUp(self):
        super().setUp()
        self.completed_appointment = self._make_appointment(status=Appointment.STATUS_COMPLETED)


class IssuePrescriptionTests(PrescriptionTestBase):
    def setUp(self):
        super().setUp()
        self.as_doctor()

    def test_doctor_can_issue_prescription_for_completed_appointment(self):
        res = self.client.post(
            _prescription_url(self.completed_appointment.id),
            {'notes': 'Follow up in 2 weeks', 'items': _items_payload()},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res.data['items']), 2)
        self.assertEqual(res.data['notes'], 'Follow up in 2 weeks')
        prescription = Prescription.objects.get(appointment=self.completed_appointment)
        self.assertEqual(prescription.doctor, self.doctor)
        self.assertEqual(prescription.patient, self.patient)
        self.assertEqual(PrescriptionItem.objects.filter(prescription=prescription).count(), 2)

    def test_prescription_issued_task_creates_notification(self):
        # Call the Celery task function directly (same convention as
        # apps/users/tests/test_i18n.py) instead of relying only on
        # `.delay()` — that requires a real broker unless the top-level
        # celery app package happens to be imported by the current process,
        # which isn't guaranteed for a plain `manage.py test <label>` run.
        # (The view's own `.delay()` call may *also* fire eagerly depending
        # on that — hence checking the latest notification rather than
        # assuming exactly one exists.)
        res = self.client.post(
            _prescription_url(self.completed_appointment.id),
            {'items': _items_payload()},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        prescription = Prescription.objects.get(appointment=self.completed_appointment)

        send_prescription_issued(str(prescription.id))

        notif = Notification.objects.filter(user=self.patient).latest('sent_at')
        self.assertIn(self.doctor.first_name, notif.title + notif.message)

    def test_cannot_issue_for_non_completed_appointment(self):
        # Different slot than self.completed_appointment (both default to the
        # same starts_at) — same doctor can't have two overlapping active
        # appointments (appt_no_overlap_per_doctor constraint).
        pending = self._make_appointment(
            status=Appointment.STATUS_PENDING,
            starts_at=self._future_dt(12), ends_at=self._future_dt(12, 30),
        )
        res = self.client.post(
            _prescription_url(pending.id), {'items': _items_payload()}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_issue_twice_for_same_appointment(self):
        res1 = self.client.post(
            _prescription_url(self.completed_appointment.id),
            {'items': _items_payload()}, format='json',
        )
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        res2 = self.client.post(
            _prescription_url(self.completed_appointment.id),
            {'items': _items_payload()}, format='json',
        )
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Prescription.objects.filter(appointment=self.completed_appointment).count(), 1)

    def test_requires_at_least_one_item(self):
        res = self.client.post(
            _prescription_url(self.completed_appointment.id), {'items': []}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_other_doctors_appointment_returns_404(self):
        from django.contrib.auth import get_user_model
        other_doctor_token = _register_and_login(
            self.client, {
                'email': 'other-doc@test.com', 'password': 'Pass1234', 'password_confirm': 'Pass1234',
                'role': 'doctor', 'first_name': 'Other', 'last_name': 'Doc',
            },
        )
        # Must be verified too, or IsDoctorVerified (403) would mask the 404
        # ownership check this test is actually exercising.
        other_doctor = get_user_model().objects.get(email='other-doc@test.com')
        other_doctor.doctor_profile.is_verified = True
        other_doctor.doctor_profile.save(update_fields=['is_verified'])
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_doctor_token}')
        res = self.client.post(
            _prescription_url(self.completed_appointment.id), {'items': _items_payload()}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_unverified_doctor_cannot_issue(self):
        self.doctor.doctor_profile.is_verified = False
        self.doctor.doctor_profile.save(update_fields=['is_verified'])
        res = self.client.post(
            _prescription_url(self.completed_appointment.id), {'items': _items_payload()}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_patient_cannot_issue_prescription(self):
        self.as_patient()
        res = self.client.post(
            _prescription_url(self.completed_appointment.id), {'items': _items_payload()}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


class PrescriptionDependentInheritanceTests(PrescriptionTestBase):
    """Prescription.dependent is denormalized from appointment.dependent at
    creation time — same as doctor/patient — with no separate input."""

    def setUp(self):
        super().setUp()
        self.dependent = Dependent.objects.create(
            managed_by=self.patient, first_name='Kid', last_name='Doe', relationship='child',
        )
        self.dependent_appointment = self._make_appointment(
            status=Appointment.STATUS_COMPLETED, dependent=self.dependent,
            starts_at=self._future_dt(15), ends_at=self._future_dt(15, 30),
        )

    def test_prescription_inherits_dependent_from_appointment(self):
        self.as_doctor()
        res = self.client.post(
            _prescription_url(self.dependent_appointment.id), {'items': _items_payload()}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['dependent']['id'], str(self.dependent.id))
        prescription = Prescription.objects.get(appointment=self.dependent_appointment)
        self.assertEqual(prescription.dependent_id, self.dependent.id)
        # patient stays the account owner, unaffected by dependent.
        self.assertEqual(prescription.patient, self.patient)

    def test_prescription_dependent_is_null_when_appointment_has_none(self):
        self.as_doctor()
        res = self.client.post(
            _prescription_url(self.completed_appointment.id), {'items': _items_payload()}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(res.data['dependent'])
        prescription = Prescription.objects.get(appointment=self.completed_appointment)
        self.assertIsNone(prescription.dependent_id)


class GetPrescriptionTests(PrescriptionTestBase):
    def _issue(self):
        self.as_doctor()
        res = self.client.post(
            _prescription_url(self.completed_appointment.id), {'items': _items_payload()}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        return res.data['id']

    def test_get_returns_404_when_no_prescription_yet(self):
        self.as_patient()
        res = self.client.get(_prescription_url(self.completed_appointment.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_patient_can_get_own_prescription(self):
        self._issue()
        self.as_patient()
        res = self.client.get(_prescription_url(self.completed_appointment.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data['items']), 2)

    def test_issuing_doctor_can_get_prescription(self):
        self._issue()
        self.as_doctor()
        res = self.client.get(_prescription_url(self.completed_appointment.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_unrelated_user_gets_404_not_403(self):
        self._issue()
        other_token = _register_and_login(self.client, patient_payload(email='unrelated@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.get(_prescription_url(self.completed_appointment.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_prescription_detail_view_same_permission_rules(self):
        prescription_id = self._issue()
        self.as_patient()
        res = self.client.get(_detail_url(prescription_id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        other_token = _register_and_login(self.client, patient_payload(email='unrelated2@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.get(_detail_url(prescription_id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_patient_prescription_list_returns_only_own(self):
        self._issue()
        other_appointment = self._make_appointment(
            status=Appointment.STATUS_COMPLETED, starts_at=self._future_dt(14),
            ends_at=self._future_dt(14, 30),
        )
        other_patient_token = _register_and_login(self.client, patient_payload(email='list-other@test.com'))

        self.as_doctor()
        # Issue a second prescription tied to the *original* patient for a
        # different appointment, plus confirm an unrelated patient sees none.
        self.client.post(
            _prescription_url(other_appointment.id), {'items': _items_payload()}, format='json',
        )

        self.as_patient()
        res = self.client.get(LIST_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 2)

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_patient_token}')
        res = self.client.get(LIST_URL)
        self.assertEqual(res.data['count'], 0)


class ApplyPrescriptionTests(PrescriptionTestBase):
    def _issue(self):
        self.as_doctor()
        res = self.client.post(
            _prescription_url(self.completed_appointment.id), {'items': _items_payload()}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        return res.data['id']

    def test_apply_creates_medications_from_items(self):
        prescription_id = self._issue()
        self.as_patient()
        res = self.client.post(_apply_url(prescription_id))
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res.data), 2)
        names = {m['name'] for m in res.data}
        self.assertEqual(names, {'Amoxicillin', 'Paracetamol'})
        for med in Medication.objects.filter(patient=self.patient):
            self.assertEqual(med.source, Medication.SOURCE_PRESCRIPTION)
            self.assertEqual(med.schedules.count(), 0)
            self.assertIsNotNone(med.prescription_item_id)

    def test_apply_is_idempotent_does_not_duplicate(self):
        prescription_id = self._issue()
        self.as_patient()
        res1 = self.client.post(_apply_url(prescription_id))
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        res2 = self.client.post(_apply_url(prescription_id))
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res2.data), 2)
        self.assertEqual(Medication.objects.filter(patient=self.patient).count(), 2)

    def test_apply_requires_owner_patient(self):
        prescription_id = self._issue()
        other_token = _register_and_login(self.client, patient_payload(email='apply-other@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.post(_apply_url(prescription_id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Medication.objects.count(), 0)

    def test_doctor_cannot_apply(self):
        prescription_id = self._issue()
        res = self.client.post(_apply_url(prescription_id))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
