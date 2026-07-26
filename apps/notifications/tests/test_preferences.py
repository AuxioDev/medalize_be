from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from apps.notifications.models import NotificationPreference
from apps.notifications.tasks import _send_email, _send_push

User = get_user_model()

REGISTER_URL = '/api/auth/register/'
LOGIN_URL = '/api/auth/login/'
PREFERENCES_URL = '/api/notifications/preferences/'


def patient_payload(**kwargs):
    data = {'email': 'patient@test.com', 'password': 'Pass1234', 'password_confirm': 'Pass1234',
            'role': 'patient', 'first_name': 'Jane', 'last_name': 'Doe', 'privacy_consent': True}
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


class NotificationPreferenceAPITests(NotificationAuthTestCase):
    def test_get_creates_default_row_enabled(self):
        res = self.client.get(PREFERENCES_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['push_enabled'])
        self.assertTrue(res.data['email_enabled'])
        self.assertTrue(NotificationPreference.objects.filter(user=self.user).exists())

    def test_get_without_auth_returns_401(self):
        self.client.credentials()
        res = self.client.get(PREFERENCES_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_patch_disables_push_only(self):
        res = self.client.patch(PREFERENCES_URL, {'push_enabled': False}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data['push_enabled'])
        self.assertTrue(res.data['email_enabled'])
        prefs = NotificationPreference.objects.get(user=self.user)
        self.assertFalse(prefs.push_enabled)
        self.assertTrue(prefs.email_enabled)

    def test_patch_persists_across_get(self):
        self.client.patch(PREFERENCES_URL, {'email_enabled': False}, format='json')
        res = self.client.get(PREFERENCES_URL)
        self.assertFalse(res.data['email_enabled'])


class NotificationGatingTests(APITestCase):
    """Verifies the send helpers actually respect stored preferences."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='gated@test.com', password='Pass1234', role='patient',
            first_name='G', last_name='T',
        )

    def test_send_email_skipped_when_disabled(self):
        NotificationPreference.objects.create(user=self.user, email_enabled=False)
        _send_email('Subject', 'Body', self.user)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_email_sent_when_enabled(self):
        NotificationPreference.objects.create(user=self.user, email_enabled=True)
        _send_email('Subject', 'Body', self.user)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])

    def test_send_email_defaults_to_enabled_without_existing_row(self):
        # No NotificationPreference row yet — _send_email must create one via
        # get_or_create rather than crash, defaulting to enabled.
        _send_email('Subject', 'Body', self.user)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(NotificationPreference.objects.get(user=self.user).email_enabled)

    def test_send_push_skipped_when_disabled(self):
        # push_enabled=False must short-circuit before any Firebase call is
        # attempted, so this must not raise even without FIREBASE_CREDENTIALS_JSON.
        NotificationPreference.objects.create(user=self.user, push_enabled=False)
        _send_push(self.user, 'Title', 'Body')
        # No exception is the assertion here; nothing observable to check since
        # push has no local side effect in tests.
