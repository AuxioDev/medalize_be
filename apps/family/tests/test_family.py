from rest_framework import status
from rest_framework.test import APITestCase

from apps.appointments.tests.test_appointments import _register_and_login, doctor_payload, patient_payload
from apps.family.models import Dependent

DEPENDENTS_URL = '/api/dependents/'


def _detail_url(pk):
    return f'{DEPENDENTS_URL}{pk}/'


def _dependent_payload(**kwargs):
    data = {
        'first_name': 'Alice', 'last_name': 'Doe', 'relationship': 'child',
        'date_of_birth': '2020-01-01', 'blood_type': 'O+',
        'allergies': 'Peanuts', 'chronic_conditions': '', 'medications': '',
    }
    data.update(kwargs)
    return data


class DependentTestBase(APITestCase):
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

    def _patient(self):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.get(email='patient@test.com')


class DependentCreateTests(DependentTestBase):
    def test_create_dependent_returns_201(self):
        res = self.client.post(DEPENDENTS_URL, _dependent_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['first_name'], 'Alice')
        self.assertEqual(res.data['relationship'], 'child')
        self.assertTrue(res.data['is_active'])
        dependent = Dependent.objects.get(pk=res.data['id'])
        self.assertEqual(dependent.managed_by, self._patient())

    def test_create_dependent_requires_first_name(self):
        payload = _dependent_payload()
        del payload['first_name']
        res = self.client.post(DEPENDENTS_URL, payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_dependent_requires_valid_relationship(self):
        res = self.client.post(DEPENDENTS_URL, _dependent_payload(relationship='not-a-real-one'), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_last_name_is_optional(self):
        payload = _dependent_payload()
        del payload['last_name']
        res = self.client.post(DEPENDENTS_URL, payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_doctor_cannot_create_dependent(self):
        self.as_doctor()
        res = self.client.post(DEPENDENTS_URL, _dependent_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_create_dependent(self):
        self.as_anonymous()
        res = self.client.post(DEPENDENTS_URL, _dependent_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class DependentListDetailTests(DependentTestBase):
    def setUp(self):
        super().setUp()
        self.active = Dependent.objects.create(
            managed_by=self._patient(), first_name='Bob', relationship='spouse',
        )
        self.inactive = Dependent.objects.create(
            managed_by=self._patient(), first_name='Zed', relationship='parent', is_active=False,
        )

    def test_list_returns_only_active_ordered_by_first_name(self):
        Dependent.objects.create(managed_by=self._patient(), first_name='Amy', relationship='sibling')
        res = self.client.get(DEPENDENTS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        names = [d['first_name'] for d in res.data]
        self.assertNotIn('Zed', names)
        self.assertEqual(names, sorted(names))

    def test_list_returns_only_own_dependents(self):
        other_token = _register_and_login(self.client, patient_payload(email='other@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.get(DEPENDENTS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 0)

    def test_get_detail_returns_200(self):
        res = self.client.get(_detail_url(self.active.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['first_name'], 'Bob')

    def test_get_detail_allows_inactive_owner_lookup(self):
        # Detail (unlike list) isn't filtered to is_active=True — a patient
        # editing/viewing a just-deleted profile shouldn't 404 immediately.
        res = self.client.get(_detail_url(self.inactive.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_other_patients_dependent_returns_404(self):
        other_token = _register_and_login(self.client, patient_payload(email='other2@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.get(_detail_url(self.active.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_patch_updates_fields(self):
        res = self.client.patch(_detail_url(self.active.id), {'allergies': 'Shellfish'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.active.refresh_from_db()
        self.assertEqual(self.active.allergies, 'Shellfish')

    def test_patch_other_patients_dependent_returns_404(self):
        other_token = _register_and_login(self.client, patient_payload(email='other3@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.patch(_detail_url(self.active.id), {'allergies': 'Nope'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_soft_deletes_not_hard_deletes(self):
        res = self.client.delete(_detail_url(self.active.id))
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.active.refresh_from_db()
        self.assertFalse(self.active.is_active)
        self.assertTrue(Dependent.objects.filter(pk=self.active.pk).exists())

    def test_deleted_dependent_disappears_from_list(self):
        self.client.delete(_detail_url(self.active.id))
        res = self.client.get(DEPENDENTS_URL)
        names = [d['first_name'] for d in res.data]
        self.assertNotIn('Bob', names)

    def test_delete_other_patients_dependent_returns_404(self):
        other_token = _register_and_login(self.client, patient_payload(email='other4@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.delete(_detail_url(self.active.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.active.refresh_from_db()
        self.assertTrue(self.active.is_active)

    def test_doctor_cannot_access_dependents(self):
        self.as_doctor()
        res = self.client.get(DEPENDENTS_URL)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_access_dependents(self):
        self.as_anonymous()
        res = self.client.get(DEPENDENTS_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
