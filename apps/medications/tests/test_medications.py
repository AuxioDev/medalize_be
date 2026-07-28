from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.appointments.tests.test_appointments import (
    _register_and_login, doctor_payload, patient_payload,
)
from apps.family.models import Dependent
from apps.medications.models import DoseLog, Medication, MedicationSchedule

MEDICATIONS_URL = '/api/medications/'
DOSE_LOGS_URL = '/api/medications/dose-logs/'


def _detail_url(pk):
    return f'{MEDICATIONS_URL}{pk}/'


class MedicationTestBase(APITestCase):
    def setUp(self):
        self.patient_token = _register_and_login(self.client, patient_payload())
        self.doctor_token = _register_and_login(self.client, doctor_payload())
        self.as_patient()

    def as_patient(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.patient_token}')

    def as_doctor(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.doctor_token}')

    def as_anonymous(self):
        self.client.credentials()


class MedicationCreateBlockedTests(MedicationTestBase):
    """A Medication only ever exists because a doctor prescribed it (see
    apps.prescriptions.PrescriptionApplyView) — there is no patient-facing
    way to create one from scratch anymore."""

    def test_patient_post_returns_405(self):
        res = self.client.post(MEDICATIONS_URL, {'name': 'Ibuprofen'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(Medication.objects.count(), 0)

    def test_doctor_post_returns_403(self):
        self.as_doctor()
        res = self.client.post(MEDICATIONS_URL, {'name': 'Ibuprofen'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_post_returns_401(self):
        self.as_anonymous()
        res = self.client.post(MEDICATIONS_URL, {'name': 'Ibuprofen'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class MedicationListDetailTests(MedicationTestBase):
    def setUp(self):
        super().setUp()
        self.active = Medication.objects.create(patient=self._patient(), name='Active Med')
        self.inactive = Medication.objects.create(
            patient=self._patient(), name='Archived Med', is_active=False,
        )

    def _patient(self):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.get(email='patient@test.com')

    def test_list_orders_active_first(self):
        res = self.client.get(MEDICATIONS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        names = [m['name'] for m in res.data]
        self.assertEqual(names[0], 'Active Med')
        self.assertIn('Archived Med', names)

    def test_get_detail_returns_200(self):
        res = self.client.get(_detail_url(self.active.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['name'], 'Active Med')

    def test_other_patients_medication_returns_404(self):
        other_token = _register_and_login(self.client, patient_payload(email='other@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.get(_detail_url(self.active.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_patch_cannot_change_identity_fields(self):
        # name/dosage/form/notes only ever come from the doctor's
        # prescription — PATCH silently ignores them rather than erroring,
        # same as any other unrecognized field.
        res = self.client.patch(
            _detail_url(self.active.id),
            {'name': 'Renamed', 'dosage': '999mg', 'form': 'liquid', 'notes': 'tampered'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.active.refresh_from_db()
        self.assertEqual(self.active.name, 'Active Med')
        self.assertEqual(self.active.dosage, '')
        self.assertEqual(self.active.notes, '')

    def test_patch_replaces_schedules_when_provided(self):
        MedicationSchedule.objects.create(medication=self.active, times=['08:00'], days_of_week=[])
        res = self.client.patch(
            _detail_url(self.active.id),
            {'schedules': [{'times': ['09:00'], 'days_of_week': [1]}]},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.active.refresh_from_db()
        self.assertEqual(self.active.schedules.count(), 1)
        self.assertEqual(self.active.schedules.first().times, ['09:00'])

    def test_patch_invalid_time_format_returns_400(self):
        res = self.client.patch(
            _detail_url(self.active.id),
            {'schedules': [{'times': ['8:00'], 'days_of_week': []}]},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_without_schedules_leaves_existing_schedule_untouched(self):
        MedicationSchedule.objects.create(medication=self.active, times=['08:00'], days_of_week=[])
        res = self.client.patch(_detail_url(self.active.id), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.active.refresh_from_db()
        self.assertEqual(self.active.schedules.count(), 1)
        self.assertEqual(self.active.schedules.first().times, ['08:00'])

    def test_patch_ignores_dependent_id(self):
        dependent = Dependent.objects.create(
            managed_by=self._patient(), first_name='Kid', last_name='Doe', relationship='child',
        )
        res = self.client.patch(
            _detail_url(self.active.id), {'dependent_id': str(dependent.id)}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.active.refresh_from_db()
        self.assertIsNone(self.active.dependent_id)

    def test_patch_other_patients_medication_returns_404(self):
        other_token = _register_and_login(self.client, patient_payload(email='other-patch@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.patch(
            _detail_url(self.active.id), {'schedules': []}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_soft_deletes_not_hard_deletes(self):
        res = self.client.delete(_detail_url(self.active.id))
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.active.refresh_from_db()
        self.assertFalse(self.active.is_active)
        self.assertTrue(Medication.objects.filter(pk=self.active.pk).exists())

    def test_delete_other_patients_medication_returns_404(self):
        other_token = _register_and_login(self.client, patient_payload(email='other2@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.delete(_detail_url(self.active.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class DoseLogTests(MedicationTestBase):
    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        self.patient = get_user_model().objects.get(email='patient@test.com')
        self.medication = Medication.objects.create(patient=self.patient, name='Metformin')
        self.schedule = MedicationSchedule.objects.create(
            medication=self.medication, times=['08:00'], days_of_week=[],
        )
        self.scheduled_at = timezone.now().replace(microsecond=0)

    def _log_payload(self, status_value='taken'):
        return {
            'schedule': str(self.schedule.id),
            'scheduled_at': self.scheduled_at.isoformat(),
            'status': status_value,
        }

    def test_create_dose_log_returns_201(self):
        res = self.client.post(DOSE_LOGS_URL, self._log_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(DoseLog.objects.count(), 1)

    def test_repeat_post_is_idempotent_updates_status_not_duplicate_row(self):
        res1 = self.client.post(DOSE_LOGS_URL, self._log_payload('taken'), format='json')
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        res2 = self.client.post(DOSE_LOGS_URL, self._log_payload('skipped'), format='json')
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)

        self.assertEqual(DoseLog.objects.count(), 1)
        log = DoseLog.objects.get()
        self.assertEqual(log.status, 'skipped')

    def test_dose_log_for_other_patients_schedule_returns_404(self):
        other_token = _register_and_login(self.client, patient_payload(email='dose-other@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.post(DOSE_LOGS_URL, self._log_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(DoseLog.objects.count(), 0)

    def test_list_defaults_to_today(self):
        self.client.post(DOSE_LOGS_URL, self._log_payload(), format='json')
        res = self.client.get(DOSE_LOGS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

    def test_list_filters_by_explicit_date_excludes_other_days(self):
        self.client.post(DOSE_LOGS_URL, self._log_payload(), format='json')
        past_date = (self.scheduled_at - timezone.timedelta(days=5)).date().isoformat()
        res = self.client.get(f'{DOSE_LOGS_URL}?date={past_date}')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 0)

    def test_list_invalid_date_returns_400(self):
        res = self.client.get(f'{DOSE_LOGS_URL}?date=not-a-date')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_dose_log_invalid_status_returns_400(self):
        res = self.client.post(DOSE_LOGS_URL, self._log_payload('bogus'), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
