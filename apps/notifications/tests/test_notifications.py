from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from apps.notifications.models import FCMToken, Notification
from apps.notifications.serializers import NotificationSerializer

User = get_user_model()

REGISTER_URL = '/api/auth/register/'
LOGIN_URL = '/api/auth/login/'
FCM_URL = '/api/notifications/fcm/'
NOTIFICATIONS_URL = '/api/notifications/'


def patient_payload(**kwargs):
    data = {'email': 'patient@test.com', 'password': 'Pass1234', 'password_confirm': 'Pass1234',
            'role': 'patient', 'first_name': 'Jane', 'last_name': 'Doe'}
    data.update(kwargs)
    return data


class NotificationAuthTestCase(APITestCase):
    """Registers and logs in a patient, leaving an authenticated client."""

    def setUp(self):
        cache.clear()
        self.client.post(REGISTER_URL, patient_payload(), format='json')
        cache.clear()
        res = self.client.post(
            LOGIN_URL, {'email': 'patient@test.com', 'password': 'Pass1234'}, format='json'
        )
        cache.clear()
        self.token = res.data['access']
        self.user = User.objects.get(email='patient@test.com')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.token}')


class SerializerSanityTests(APITestCase):
    """Guards against the redundant-source regression that 500'd every list/read call."""

    def test_serializer_fields_bind_without_error(self):
        # Instantiating .fields triggers DRF's source validation.
        fields = NotificationSerializer().fields
        self.assertIn('appointment_id', fields)

    def test_serializer_serializes_instance(self):
        user = User.objects.create_user(
            email='s@test.com', password='Pass1234', role='patient',
            first_name='S', last_name='T',
        )
        notif = Notification.objects.create(
            user=user, type=Notification.TYPE_GENERAL, title='Hi', message='Body',
        )
        data = NotificationSerializer(notif).data
        self.assertEqual(data['title'], 'Hi')
        self.assertIsNone(data['appointment_id'])
        self.assertFalse(data['is_read'])


class FCMTokenTests(NotificationAuthTestCase):
    def test_register_token_returns_201(self):
        res = self.client.post(FCM_URL, {'token': 'fcm-token-abc'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(FCMToken.objects.filter(user=self.user, token='fcm-token-abc').exists())

    def test_register_same_token_twice_returns_200(self):
        self.client.post(FCM_URL, {'token': 'fcm-token-abc'}, format='json')
        res = self.client.post(FCM_URL, {'token': 'fcm-token-abc'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(FCMToken.objects.filter(user=self.user).count(), 1)

    def test_register_token_missing_field_returns_400(self):
        res = self.client.post(FCM_URL, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_token_without_auth_returns_401(self):
        self.client.credentials()
        res = self.client.post(FCM_URL, {'token': 'x'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class NotificationListTests(NotificationAuthTestCase):
    def test_list_returns_200_and_only_own(self):
        other = User.objects.create_user(
            email='other@test.com', password='Pass1234', role='patient',
            first_name='O', last_name='T',
        )
        Notification.objects.create(user=self.user, title='Mine', message='m')
        Notification.objects.create(user=other, title='Theirs', message='m')
        res = self.client.get(NOTIFICATIONS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['title'], 'Mine')

    def test_list_without_auth_returns_401(self):
        self.client.credentials()
        res = self.client.get(NOTIFICATIONS_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_empty_returns_zero_count(self):
        res = self.client.get(NOTIFICATIONS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 0)


class NotificationReadTests(NotificationAuthTestCase):
    def test_mark_read_returns_200_and_flips_flag(self):
        notif = Notification.objects.create(user=self.user, title='T', message='m')
        res = self.client.patch(f'{NOTIFICATIONS_URL}{notif.pk}/read/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['is_read'])
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    def test_mark_read_nonexistent_returns_404(self):
        import uuid
        res = self.client.patch(f'{NOTIFICATIONS_URL}{uuid.uuid4()}/read/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_mark_other_users_notification_read(self):
        other = User.objects.create_user(
            email='other@test.com', password='Pass1234', role='patient',
            first_name='O', last_name='T',
        )
        notif = Notification.objects.create(user=other, title='T', message='m')
        res = self.client.patch(f'{NOTIFICATIONS_URL}{notif.pk}/read/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
