import uuid

from django.contrib.auth import get_user_model
from rest_framework import status

from apps.appointments.models import Favorite
from .test_appointments import AppointmentTestBase, _register_and_login, patient_payload

User = get_user_model()

FAVORITES_URL = '/api/favorites/'


class FavoriteTests(AppointmentTestBase):
    def _add(self, doctor_id=None):
        return self.client.post(
            FAVORITES_URL, {'doctor_id': str(doctor_id or self.doctor.pk)}, format='json'
        )

    def test_add_favorite_returns_201(self):
        self.as_patient()
        res = self._add()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['doctor_id'], str(self.doctor.pk))
        self.assertEqual(Favorite.objects.count(), 1)

    def test_add_favorite_is_idempotent(self):
        self.as_patient()
        first = self._add()
        second = self._add()
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data['id'], first.data['id'])
        self.assertEqual(Favorite.objects.count(), 1)

    def test_add_favorite_missing_doctor_id_returns_400(self):
        self.as_patient()
        res = self.client.post(FAVORITES_URL, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_add_favorite_unknown_doctor_returns_404(self):
        self.as_patient()
        res = self._add(doctor_id=uuid.uuid4())
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_add_favorite_unverified_doctor_returns_404(self):
        self.doctor.doctor_profile.is_verified = False
        self.doctor.doctor_profile.save(update_fields=['is_verified'])
        self.as_patient()
        res = self._add()
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_returns_full_doctor_cards(self):
        self.as_patient()
        self._add()
        res = self.client.get(FAVORITES_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        card = res.data[0]
        self.assertEqual(card['id'], str(self.doctor.pk))
        self.assertEqual(card['first_name'], 'John')
        for field in ('specialization', 'primary_workplace', 'average_rating', 'review_count'):
            self.assertIn(field, card)

    def test_remove_favorite(self):
        self.as_patient()
        self._add()
        res = self.client.delete(f'{FAVORITES_URL}{self.doctor.pk}/')
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(Favorite.objects.count(), 0)
        self.assertEqual(self.client.get(FAVORITES_URL).data, [])

    def test_remove_missing_favorite_returns_404(self):
        self.as_patient()
        res = self.client.delete(f'{FAVORITES_URL}{self.doctor.pk}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_favorites_are_isolated_per_patient(self):
        self.as_patient()
        self._add()

        other_token = _register_and_login(
            self.client, patient_payload(email='patient2@test.com')
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.get(FAVORITES_URL)
        self.assertEqual(res.data, [])
        res = self.client.delete(f'{FAVORITES_URL}{self.doctor.pk}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_doctor_cannot_access_favorites(self):
        self.as_doctor()
        self.assertEqual(self.client.get(FAVORITES_URL).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self._add().status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.client.delete(f'{FAVORITES_URL}{self.doctor.pk}/').status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_favorites_require_auth(self):
        res = self.client.get(FAVORITES_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
