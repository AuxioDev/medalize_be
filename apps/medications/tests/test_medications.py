import uuid

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


class MedicationCreateTests(MedicationTestBase):
    def test_create_medication_without_schedule_returns_201(self):
        res = self.client.post(
            MEDICATIONS_URL,
            {'name': 'Ibuprofen', 'dosage': '200mg', 'form': 'pill', 'notes': 'After meals'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['name'], 'Ibuprofen')
        self.assertEqual(res.data['source'], Medication.SOURCE_MANUAL)
        self.assertTrue(res.data['is_active'])
        self.assertEqual(res.data['schedules'], [])

    def test_create_medication_with_nested_schedules_creates_schedule_rows(self):
        res = self.client.post(
            MEDICATIONS_URL,
            {
                'name': 'Amoxicillin',
                'dosage': '500mg',
                'schedules': [
                    {'times': ['08:00', '20:00'], 'days_of_week': [], 'start_date': '2026-01-01'},
                ],
            },
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        medication = Medication.objects.get(pk=res.data['id'])
        self.assertEqual(medication.schedules.count(), 1)
        schedule = medication.schedules.first()
        self.assertEqual(schedule.times, ['08:00', '20:00'])
        self.assertEqual(len(res.data['schedules']), 1)

    def test_create_medication_invalid_time_format_returns_400(self):
        res = self.client.post(
            MEDICATIONS_URL,
            {'name': 'Bad', 'schedules': [{'times': ['8:00'], 'days_of_week': []}]},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(MedicationSchedule.objects.count(), 0)

    def test_create_medication_invalid_weekday_returns_400(self):
        res = self.client.post(
            MEDICATIONS_URL,
            {'name': 'Bad', 'schedules': [{'times': ['08:00'], 'days_of_week': [7]}]},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_medication_requires_name(self):
        res = self.client.post(MEDICATIONS_URL, {'dosage': '10mg'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_doctor_cannot_create_medication(self):
        self.as_doctor()
        res = self.client.post(MEDICATIONS_URL, {'name': 'X'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_create_medication(self):
        self.as_anonymous()
        res = self.client.post(MEDICATIONS_URL, {'name': 'X'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class DependentMedicationTests(MedicationTestBase):
    """dependent_id validation on MedicationCreateSerializer — same shared
    resolve_dependent() rule as booking/records."""

    def _patient(self):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.get(email='patient@test.com')

    def setUp(self):
        super().setUp()
        self.dependent = Dependent.objects.create(
            managed_by=self._patient(), first_name='Kid', last_name='Doe', relationship='child',
        )

    def test_create_with_own_active_dependent_returns_201(self):
        res = self.client.post(
            MEDICATIONS_URL,
            {'name': 'Amoxicillin', 'dependent_id': str(self.dependent.id)},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['dependent']['id'], str(self.dependent.id))
        medication = Medication.objects.get(pk=res.data['id'])
        self.assertEqual(medication.dependent_id, self.dependent.id)
        self.assertEqual(medication.patient_id, self._patient().id)

    def test_create_without_dependent_id_leaves_dependent_null(self):
        res = self.client.post(MEDICATIONS_URL, {'name': 'Ibuprofen'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(res.data['dependent'])

    def test_create_with_someone_elses_dependent_returns_400(self):
        other_token = _register_and_login(self.client, patient_payload(email='dep-other@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.post(
            MEDICATIONS_URL,
            {'name': 'Amoxicillin', 'dependent_id': str(self.dependent.id)},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('dependent_id', res.data['errors'])

    def test_create_with_nonexistent_dependent_returns_400(self):
        res = self.client.post(
            MEDICATIONS_URL,
            {'name': 'Amoxicillin', 'dependent_id': str(uuid.uuid4())},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('dependent_id', res.data['errors'])

    def test_create_with_inactive_dependent_returns_400(self):
        self.dependent.is_active = False
        self.dependent.save(update_fields=['is_active'])
        res = self.client.post(
            MEDICATIONS_URL,
            {'name': 'Amoxicillin', 'dependent_id': str(self.dependent.id)},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('dependent_id', res.data['errors'])

    def test_patch_can_set_dependent(self):
        medication = Medication.objects.create(patient=self._patient(), name='Metformin')
        res = self.client.patch(
            _detail_url(medication.id), {'dependent_id': str(self.dependent.id)}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        medication.refresh_from_db()
        self.assertEqual(medication.dependent_id, self.dependent.id)


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

    def test_patch_updates_fields(self):
        res = self.client.patch(
            _detail_url(self.active.id), {'dosage': '50mg'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.active.refresh_from_db()
        self.assertEqual(self.active.dosage, '50mg')

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
