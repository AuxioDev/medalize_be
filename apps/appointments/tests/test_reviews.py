import datetime

from django.utils import timezone
from rest_framework import status

from apps.appointments.models import Appointment, REVIEW_EDIT_WINDOW_DAYS, Review
from .test_appointments import AppointmentTestBase, _register_and_login, patient_payload


def _review_url(appointment_id):
    return f'/api/appointments/{appointment_id}/review/'


def _detail_url(appointment_id):
    return f'/api/appointments/{appointment_id}/'


class ReviewEditDeleteTests(AppointmentTestBase):
    """PATCH/DELETE on /appointments/<pk>/review/ plus the nested
    review / can_edit_review fields on AppointmentSerializer."""

    def setUp(self):
        super().setUp()
        self.appointment = self._make_appointment(status=Appointment.STATUS_COMPLETED)
        self.as_patient()

    def _create_review(self, rating=4, comment='Good doctor'):
        res = self.client.post(
            _review_url(self.appointment.id),
            {'rating': rating, 'comment': comment},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        return Review.objects.get(appointment=self.appointment)

    def _expire_edit_window(self, review):
        Review.objects.filter(pk=review.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=REVIEW_EDIT_WINDOW_DAYS, hours=1)
        )
        review.refresh_from_db()

    # ---- PATCH ----

    def test_patch_within_window_updates_review(self):
        review = self._create_review()
        original_updated_at = review.updated_at
        res = self.client.patch(
            _review_url(self.appointment.id),
            {'rating': 2, 'comment': 'Changed my mind'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['rating'], 2)
        self.assertEqual(res.data['comment'], 'Changed my mind')
        review.refresh_from_db()
        self.assertEqual(review.rating, 2)
        self.assertEqual(review.comment, 'Changed my mind')
        self.assertGreater(review.updated_at, original_updated_at)

    def test_patch_after_window_returns_400_and_leaves_review_unchanged(self):
        review = self._create_review()
        self._expire_edit_window(review)
        res = self.client.patch(
            _review_url(self.appointment.id),
            {'rating': 1, 'comment': 'Too late'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        review.refresh_from_db()
        self.assertEqual(review.rating, 4)
        self.assertEqual(review.comment, 'Good doctor')

    def test_patch_someone_elses_review_returns_404(self):
        self._create_review()
        other_token = _register_and_login(
            self.client, patient_payload(email='other@test.com')
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.patch(
            _review_url(self.appointment.id),
            {'rating': 5, 'comment': 'Not mine'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_patch_without_existing_review_returns_404(self):
        res = self.client.patch(
            _review_url(self.appointment.id), {'rating': 3}, format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_patch_invalid_rating_returns_400(self):
        self._create_review()
        res = self.client.patch(
            _review_url(self.appointment.id), {'rating': 6}, format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # ---- DELETE ----

    def test_delete_review_returns_204_and_resets_appointment_state(self):
        review = self._create_review()
        res = self.client.delete(_review_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Review.objects.filter(pk=review.pk).exists())

        detail = self.client.get(_detail_url(self.appointment.id))
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertFalse(detail.data['has_review'])
        self.assertIsNone(detail.data['review'])
        self.assertFalse(detail.data['can_edit_review'])

    def test_delete_is_allowed_after_edit_window_expired(self):
        review = self._create_review()
        self._expire_edit_window(review)
        res = self.client.delete(_review_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Review.objects.filter(pk=review.pk).exists())

    def test_delete_someone_elses_review_returns_404(self):
        review = self._create_review()
        other_token = _register_and_login(
            self.client, patient_payload(email='other@test.com')
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.delete(_review_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Review.objects.filter(pk=review.pk).exists())

    def test_delete_without_existing_review_returns_404(self):
        res = self.client.delete(_review_url(self.appointment.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # ---- AppointmentSerializer fields ----

    def test_appointment_without_review_has_null_review_fields(self):
        detail = self.client.get(_detail_url(self.appointment.id))
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertFalse(detail.data['has_review'])
        self.assertIsNone(detail.data['review'])
        self.assertFalse(detail.data['can_edit_review'])

    def test_appointment_with_fresh_review_exposes_nested_review_and_edit_flag(self):
        self._create_review()
        detail = self.client.get(_detail_url(self.appointment.id))
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertTrue(detail.data['has_review'])
        self.assertTrue(detail.data['can_edit_review'])
        nested = detail.data['review']
        self.assertEqual(nested['rating'], 4)
        self.assertEqual(nested['comment'], 'Good doctor')
        self.assertIn('updated_at', nested)

    def test_appointment_with_expired_review_is_not_editable_but_still_nested(self):
        review = self._create_review()
        self._expire_edit_window(review)
        detail = self.client.get(_detail_url(self.appointment.id))
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertTrue(detail.data['has_review'])
        self.assertFalse(detail.data['can_edit_review'])
        self.assertIsNotNone(detail.data['review'])
