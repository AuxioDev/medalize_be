import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from apps.doctors.models import BlockedPeriod, Workplace, WorkingHours

User = get_user_model()

REGISTER_URL = '/api/auth/register/'
LOGIN_URL = '/api/auth/login/'
WORKPLACES_URL = '/api/doctor/workplaces/'
BLOCKED_PERIODS_URL = '/api/doctor/blocked-periods/'


def doctor_payload(**kwargs):
    data = {
        'email': 'doctor@test.com',
        'password': 'Pass1234',
        'password_confirm': 'Pass1234',
        'role': 'doctor',
        'first_name': 'John',
        'last_name': 'Smith',
    }
    data.update(kwargs)
    return data


def patient_payload(**kwargs):
    data = {
        'email': 'patient@test.com',
        'password': 'Pass1234',
        'password_confirm': 'Pass1234',
        'role': 'patient',
        'first_name': 'Jane',
        'last_name': 'Doe',
    }
    data.update(kwargs)
    return data


def workplace_payload(**kwargs):
    data = {
        'name': 'Test Clinic',
        'address': '123 Main St',
        'city': 'Baku',
        'type': 'clinic',
    }
    data.update(kwargs)
    return data


class DoctorAuthTestCase(APITestCase):
    def setUp(self):
        cache.clear()
        self.client.post(REGISTER_URL, doctor_payload(), format='json')
        cache.clear()
        res = self.client.post(LOGIN_URL, {'email': 'doctor@test.com', 'password': 'Pass1234'}, format='json')
        cache.clear()
        self.doctor_token = res.data['access']
        self.doctor = User.objects.get(email='doctor@test.com')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.doctor_token}')


class WorkplaceTests(DoctorAuthTestCase):
    def test_create_workplace_returns_201(self):
        res = self.client.post(WORKPLACES_URL, workplace_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['name'], 'Test Clinic')
        self.assertFalse(res.data['is_primary'])
        self.assertIn('working_hours', res.data)

    def test_list_workplaces_returns_200(self):
        Workplace.objects.create(doctor=self.doctor, **workplace_payload())
        res = self.client.get(WORKPLACES_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

    def test_list_only_returns_own_workplaces(self):
        cache.clear()
        self.client.credentials()
        self.client.post(REGISTER_URL, doctor_payload(email='other@test.com'), format='json')
        cache.clear()
        other = User.objects.get(email='other@test.com')
        Workplace.objects.create(doctor=other, **workplace_payload(name='Other Clinic'))
        Workplace.objects.create(doctor=self.doctor, **workplace_payload(name='Mine'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.doctor_token}')
        res = self.client.get(WORKPLACES_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]['name'], 'Mine')

    def test_patch_workplace_returns_200(self):
        wp = Workplace.objects.create(doctor=self.doctor, **workplace_payload())
        res = self.client.patch(f'{WORKPLACES_URL}{wp.pk}/', {'name': 'Updated Clinic'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['name'], 'Updated Clinic')

    def test_patch_invalid_type_returns_400(self):
        wp = Workplace.objects.create(doctor=self.doctor, **workplace_payload())
        res = self.client.patch(f'{WORKPLACES_URL}{wp.pk}/', {'type': 'spa'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_workplace_returns_204(self):
        wp = Workplace.objects.create(doctor=self.doctor, **workplace_payload())
        res = self.client.delete(f'{WORKPLACES_URL}{wp.pk}/')
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Workplace.objects.filter(pk=wp.pk).exists())

    def test_delete_nonexistent_workplace_returns_404(self):
        res = self.client.delete(f'{WORKPLACES_URL}{uuid.uuid4()}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_set_primary_sets_flag(self):
        wp = Workplace.objects.create(doctor=self.doctor, **workplace_payload())
        res = self.client.patch(f'{WORKPLACES_URL}{wp.pk}/set-primary/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['is_primary'])

    def test_set_primary_clears_other_primaries(self):
        wp1 = Workplace.objects.create(doctor=self.doctor, **workplace_payload(name='A'), is_primary=True)
        wp2 = Workplace.objects.create(doctor=self.doctor, **workplace_payload(name='B'))
        self.client.patch(f'{WORKPLACES_URL}{wp2.pk}/set-primary/')
        wp1.refresh_from_db()
        wp2.refresh_from_db()
        self.assertFalse(wp1.is_primary)
        self.assertTrue(wp2.is_primary)

    def test_patient_cannot_list_workplaces(self):
        cache.clear()
        self.client.post(REGISTER_URL, patient_payload(), format='json')
        cache.clear()
        res = self.client.post(LOGIN_URL, {'email': 'patient@test.com', 'password': 'Pass1234'}, format='json')
        cache.clear()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {res.data["access"]}')
        res = self.client.get(WORKPLACES_URL)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_cannot_list_workplaces(self):
        self.client.credentials()
        res = self.client.get(WORKPLACES_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_doctor_cannot_patch_other_doctors_workplace(self):
        cache.clear()
        self.client.credentials()
        self.client.post(REGISTER_URL, doctor_payload(email='other@test.com'), format='json')
        cache.clear()
        other = User.objects.get(email='other@test.com')
        wp = Workplace.objects.create(doctor=other, **workplace_payload())
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.doctor_token}')
        res = self.client.patch(f'{WORKPLACES_URL}{wp.pk}/', {'name': 'Hacked'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class WorkingHoursTests(DoctorAuthTestCase):
    def setUp(self):
        super().setUp()
        self.wp = Workplace.objects.create(doctor=self.doctor, **workplace_payload())
        self.hours_url = f'{WORKPLACES_URL}{self.wp.pk}/hours/'

    def test_get_hours_returns_7_rows(self):
        res = self.client.get(self.hours_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 7)

    def test_get_hours_defaults_to_inactive(self):
        res = self.client.get(self.hours_url)
        self.assertTrue(all(not row['is_active'] for row in res.data))

    def test_put_hours_replaces_full_schedule(self):
        schedule = [
            {'weekday': i, 'start_time': '09:00:00', 'end_time': '17:00:00', 'is_active': True}
            for i in range(5)
        ]
        res = self.client.put(self.hours_url, schedule, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 7)
        monday = next(r for r in res.data if r['weekday'] == 0)
        self.assertTrue(monday['is_active'])
        saturday = next(r for r in res.data if r['weekday'] == 5)
        self.assertFalse(saturday['is_active'])

    def test_put_hours_stores_all_7_in_db(self):
        schedule = [
            {'weekday': i, 'start_time': '09:00:00', 'end_time': '17:00:00', 'is_active': i < 5}
            for i in range(7)
        ]
        self.client.put(self.hours_url, schedule, format='json')
        self.assertEqual(WorkingHours.objects.filter(workplace=self.wp).count(), 7)

    def test_put_hours_rejects_duplicate_weekdays(self):
        schedule = [
            {'weekday': 0, 'start_time': '09:00', 'end_time': '17:00', 'is_active': True},
            {'weekday': 0, 'start_time': '10:00', 'end_time': '18:00', 'is_active': True},
        ]
        res = self.client.put(self.hours_url, schedule, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_put_hours_rejects_non_list_input(self):
        res = self.client.put(self.hours_url, {'weekday': 0}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_put_hours_rejects_invalid_time_order(self):
        schedule = [{'weekday': 0, 'start_time': '17:00', 'end_time': '09:00', 'is_active': True}]
        res = self.client.put(self.hours_url, schedule, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_single_day_creates_row(self):
        res = self.client.patch(
            f'{self.hours_url}1/',
            {'is_active': True, 'start_time': '08:00', 'end_time': '16:00'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['is_active'])
        self.assertEqual(res.data['weekday'], 1)
        self.assertTrue(WorkingHours.objects.filter(workplace=self.wp, weekday=1).exists())

    def test_patch_single_day_updates_existing_row(self):
        WorkingHours.objects.create(
            workplace=self.wp, weekday=2, start_time='09:00', end_time='17:00', is_active=True
        )
        res = self.client.patch(f'{self.hours_url}2/', {'is_active': False}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data['is_active'])

    def test_patch_invalid_weekday_returns_404(self):
        res = self.client.patch(f'{self.hours_url}7/', {'is_active': True}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_get_hours_nonexistent_workplace_returns_404(self):
        res = self.client.get(f'{WORKPLACES_URL}{uuid.uuid4()}/hours/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class BlockedPeriodTests(DoctorAuthTestCase):
    def setUp(self):
        super().setUp()
        self.wp = Workplace.objects.create(doctor=self.doctor, **workplace_payload())

    def _make_payload(self, **kwargs):
        data = {
            'starts_at': '2026-07-01T09:00:00Z',
            'ends_at': '2026-07-03T17:00:00Z',
            'reason': 'Vacation',
        }
        data.update(kwargs)
        return data

    def test_create_blocked_period_returns_201(self):
        res = self.client.post(BLOCKED_PERIODS_URL, self._make_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['reason'], 'Vacation')
        self.assertIsNone(res.data['workplace'])

    def test_create_blocked_period_with_workplace(self):
        res = self.client.post(
            BLOCKED_PERIODS_URL,
            self._make_payload(workplace=str(self.wp.pk)),
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIsNotNone(res.data['workplace'])

    def test_create_blocked_period_invalid_dates_returns_400(self):
        res = self.client.post(
            BLOCKED_PERIODS_URL,
            self._make_payload(starts_at='2026-07-05T09:00:00Z', ends_at='2026-07-01T09:00:00Z'),
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_blocked_period_with_other_doctors_workplace_returns_400(self):
        cache.clear()
        self.client.credentials()
        self.client.post(REGISTER_URL, doctor_payload(email='other@test.com'), format='json')
        cache.clear()
        other = User.objects.get(email='other@test.com')
        other_wp = Workplace.objects.create(doctor=other, **workplace_payload())
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.doctor_token}')
        res = self.client.post(
            BLOCKED_PERIODS_URL,
            self._make_payload(workplace=str(other_wp.pk)),
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_blocked_periods_returns_only_own(self):
        BlockedPeriod.objects.create(
            doctor=self.doctor,
            starts_at='2026-07-01T09:00:00Z',
            ends_at='2026-07-03T17:00:00Z',
        )
        res = self.client.get(BLOCKED_PERIODS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

    def test_list_blocked_periods_from_filter(self):
        BlockedPeriod.objects.create(
            doctor=self.doctor, starts_at='2026-06-01T09:00:00Z', ends_at='2026-06-05T17:00:00Z'
        )
        BlockedPeriod.objects.create(
            doctor=self.doctor, starts_at='2026-08-01T09:00:00Z', ends_at='2026-08-05T17:00:00Z'
        )
        # ?from=2026-07-01 → ends_at >= 2026-07-01 → only August period matches
        res = self.client.get(f'{BLOCKED_PERIODS_URL}?from=2026-07-01')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertIn('2026-08', res.data[0]['starts_at'])

    def test_list_blocked_periods_to_filter(self):
        BlockedPeriod.objects.create(
            doctor=self.doctor, starts_at='2026-06-01T09:00:00Z', ends_at='2026-06-05T17:00:00Z'
        )
        BlockedPeriod.objects.create(
            doctor=self.doctor, starts_at='2026-08-01T09:00:00Z', ends_at='2026-08-05T17:00:00Z'
        )
        # ?to=2026-06-30 → starts_at <= 2026-06-30 → only June period matches
        res = self.client.get(f'{BLOCKED_PERIODS_URL}?to=2026-06-30')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertIn('2026-06', res.data[0]['starts_at'])

    def test_patch_blocked_period_updates_reason(self):
        period = BlockedPeriod.objects.create(
            doctor=self.doctor,
            starts_at='2026-07-01T09:00:00Z',
            ends_at='2026-07-03T17:00:00Z',
        )
        res = self.client.patch(
            f'{BLOCKED_PERIODS_URL}{period.pk}/',
            {'reason': 'Conference'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['reason'], 'Conference')

    def test_delete_blocked_period_returns_204(self):
        period = BlockedPeriod.objects.create(
            doctor=self.doctor,
            starts_at='2026-07-01T09:00:00Z',
            ends_at='2026-07-03T17:00:00Z',
        )
        res = self.client.delete(f'{BLOCKED_PERIODS_URL}{period.pk}/')
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(BlockedPeriod.objects.filter(pk=period.pk).exists())

    def test_delete_nonexistent_period_returns_404(self):
        res = self.client.delete(f'{BLOCKED_PERIODS_URL}{uuid.uuid4()}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_doctor_cannot_patch_other_doctors_period(self):
        cache.clear()
        self.client.credentials()
        self.client.post(REGISTER_URL, doctor_payload(email='other@test.com'), format='json')
        cache.clear()
        other = User.objects.get(email='other@test.com')
        period = BlockedPeriod.objects.create(
            doctor=other,
            starts_at='2026-07-01T09:00:00Z',
            ends_at='2026-07-03T17:00:00Z',
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.doctor_token}')
        res = self.client.patch(
            f'{BLOCKED_PERIODS_URL}{period.pk}/',
            {'reason': 'Hacked'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_invalid_from_date_returns_400(self):
        res = self.client.get(f'{BLOCKED_PERIODS_URL}?from=not-a-date')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['code'], 'validation_error')

    def test_invalid_to_date_returns_400(self):
        res = self.client.get(f'{BLOCKED_PERIODS_URL}?to=99-99-99')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['code'], 'validation_error')
