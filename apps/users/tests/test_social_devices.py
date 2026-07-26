from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APITestCase

from apps.users.models import SocialAccount, UserDevice
from apps.users.social import SocialTokenError

User = get_user_model()

SOCIAL_URL = '/api/auth/social/{provider}/'
DEVICES_URL = '/api/auth/devices/'
REVOKE_ALL_URL = '/api/auth/devices/revoke-all/'
LOGIN_URL = '/api/auth/login/'
REFRESH_URL = '/api/auth/token/refresh/'
REGISTER_URL = '/api/auth/register/'


def google_claims(**kwargs):
    claims = {
        'provider_uid': 'google-uid-123',
        'email': 'social@test.com',
        'email_verified': True,
        'first_name': 'Sol',
        'last_name': 'User',
    }
    claims.update(kwargs)
    return claims


class SocialAuthTestCase(APITestCase):
    def setUp(self):
        cache.clear()

    def social_login(self, provider='google', **payload):
        data = {'id_token': 'fake-token', 'device_id': 'device-abc',
                'device_name': 'Pixel 9', 'platform': 'android'}
        data.update(payload)
        return self.client.post(SOCIAL_URL.format(provider=provider), data, format='json')


class SocialLoginTests(SocialAuthTestCase):
    @patch('apps.users.views.verify_id_token', return_value=google_claims())
    def test_new_user_created_with_tokens_and_social_account(self, mock_verify):
        res = self.social_login()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('access', res.data)
        self.assertIn('refresh', res.data)
        self.assertEqual(res.data['role'], 'patient')

        user = User.objects.get(email='social@test.com')
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.first_name, 'Sol')
        account = SocialAccount.objects.get(provider='google', provider_uid='google-uid-123')
        self.assertEqual(account.user, user)
        mock_verify.assert_called_once_with('google', 'fake-token')

    @patch('apps.users.views.verify_id_token', return_value=google_claims())
    def test_repeat_login_reuses_same_user(self, mock_verify):
        res1 = self.social_login()
        cache.clear()
        res2 = self.social_login()
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res1.data['user_id'], res2.data['user_id'])
        self.assertEqual(User.objects.filter(email='social@test.com').count(), 1)
        self.assertEqual(SocialAccount.objects.count(), 1)

    @patch('apps.users.views.verify_id_token', return_value=google_claims())
    def test_existing_verified_email_account_is_auto_linked(self, mock_verify):
        existing = User.objects.create_user(
            email='social@test.com', password='Pass1234', role='patient',
            first_name='Old', last_name='Name',
        )
        res = self.social_login()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['user_id'], str(existing.id))
        account = SocialAccount.objects.get(provider='google', provider_uid='google-uid-123')
        self.assertEqual(account.user, existing)
        # Linking must not clobber the existing profile
        existing.refresh_from_db()
        self.assertEqual(existing.first_name, 'Old')
        self.assertTrue(existing.has_usable_password())

    @patch('apps.users.views.verify_id_token',
           return_value=google_claims(email_verified=False))
    def test_existing_account_with_unverified_provider_email_is_rejected(self, mock_verify):
        User.objects.create_user(
            email='social@test.com', password='Pass1234', role='patient',
            first_name='Old', last_name='Name',
        )
        res = self.social_login()
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data['code'], 'social_email_unverified')
        self.assertFalse(SocialAccount.objects.exists())

    @patch('apps.users.views.verify_id_token', return_value=google_claims(email=''))
    def test_missing_email_from_provider_is_rejected(self, mock_verify):
        res = self.social_login()
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['code'], 'social_email_missing')

    @patch('apps.users.views.verify_id_token',
           side_effect=SocialTokenError('Invalid or expired Google token.'))
    def test_invalid_token_returns_401(self, mock_verify):
        res = self.social_login()
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(res.data['code'], 'social_token_invalid')

    def test_unknown_provider_returns_404(self):
        res = self.social_login(provider='facebook')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    @patch('apps.users.views.verify_id_token', return_value=google_claims())
    def test_inactive_account_cannot_social_login(self, mock_verify):
        User.objects.create_user(
            email='social@test.com', password='Pass1234', role='patient',
            first_name='Old', last_name='Name', is_active=False,
        )
        res = self.social_login()
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(res.data['code'], 'invalid_credentials')

    @patch('apps.users.views.verify_id_token', return_value=google_claims())
    def test_social_login_creates_user_device(self, mock_verify):
        res = self.social_login()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        device = UserDevice.objects.get(device_id='device-abc')
        self.assertEqual(device.user.email, 'social@test.com')
        self.assertEqual(device.platform, 'android')
        self.assertTrue(device.jti)


class UserDeviceTests(SocialAuthTestCase):
    def setUp(self):
        super().setUp()
        self.client.post(REGISTER_URL, {
            'email': 'patient@test.com', 'password': 'Pass1234', 'password_confirm': 'Pass1234',
            'role': 'patient', 'first_name': 'Jane', 'last_name': 'Doe', 'privacy_consent': True,
        }, format='json')
        cache.clear()
        mail.outbox = []

    def _login(self, device_id='device-1', device_name='iPhone 16', platform='ios'):
        cache.clear()
        res = self.client.post(LOGIN_URL, {
            'email': 'patient@test.com', 'password': 'Pass1234',
            'device_id': device_id, 'device_name': device_name, 'platform': platform,
        }, format='json')
        cache.clear()
        return res

    def test_login_with_device_id_creates_device_and_sends_alert_email(self):
        res = self._login()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        device = UserDevice.objects.get(device_id='device-1')
        self.assertEqual(device.device_name, 'iPhone 16')
        self.assertEqual(device.platform, 'ios')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('new device', mail.outbox[0].body.lower())
        self.assertIn('iPhone 16', mail.outbox[0].body)

    def test_second_login_same_device_updates_without_duplicate_or_email(self):
        self._login()
        mail.outbox = []
        res = self._login()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(UserDevice.objects.filter(device_id='device-1').count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_login_without_device_id_creates_no_device(self):
        cache.clear()
        res = self.client.post(LOGIN_URL, {'email': 'patient@test.com', 'password': 'Pass1234'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(UserDevice.objects.exists())

    def test_device_list_marks_current_device(self):
        self._login(device_id='device-1')
        session2 = self._login(device_id='device-2', device_name='Pixel 9', platform='android')

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {session2.data["access"]}')
        res = self.client.get(DEVICES_URL, HTTP_X_DEVICE_ID='device-2')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 2)
        by_id = {d['device_id']: d for d in res.data}
        self.assertTrue(by_id['device-2']['is_current'])
        self.assertFalse(by_id['device-1']['is_current'])

    def test_revoke_device_blacklists_its_refresh_token(self):
        session1 = self._login(device_id='device-1')
        session2 = self._login(device_id='device-2')
        device1 = UserDevice.objects.get(device_id='device-1')

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {session2.data["access"]}')
        res = self.client.delete(f'{DEVICES_URL}{device1.pk}/')
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(UserDevice.objects.filter(pk=device1.pk).exists())

        # The revoked device can no longer refresh; the surviving one still can.
        self.client.credentials()
        cache.clear()
        res1 = self.client.post(REFRESH_URL, {'refresh': session1.data['refresh']}, format='json')
        self.assertEqual(res1.status_code, status.HTTP_401_UNAUTHORIZED)
        res2 = self.client.post(REFRESH_URL, {'refresh': session2.data['refresh']}, format='json')
        self.assertEqual(res2.status_code, status.HTTP_200_OK)

    def test_cannot_revoke_another_users_device(self):
        self._login(device_id='device-1')
        device = UserDevice.objects.get(device_id='device-1')
        self.client.post(REGISTER_URL, {
            'email': 'other@test.com', 'password': 'Pass1234', 'password_confirm': 'Pass1234',
            'role': 'patient', 'first_name': 'Other', 'last_name': 'User', 'privacy_consent': True,
        }, format='json')
        cache.clear()
        other = self.client.post(LOGIN_URL, {'email': 'other@test.com', 'password': 'Pass1234'}, format='json')
        cache.clear()

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other.data["access"]}')
        res = self.client.delete(f'{DEVICES_URL}{device.pk}/')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(UserDevice.objects.filter(pk=device.pk).exists())

    def test_revoke_all_blacklists_other_sessions_but_keeps_current(self):
        session1 = self._login(device_id='device-1')
        session2 = self._login(device_id='device-2')

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {session2.data["access"]}')
        res = self.client.post(REVOKE_ALL_URL, HTTP_X_DEVICE_ID='device-2')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.client.credentials()
        cache.clear()
        res1 = self.client.post(REFRESH_URL, {'refresh': session1.data['refresh']}, format='json')
        self.assertEqual(res1.status_code, status.HTTP_401_UNAUTHORIZED)
        res2 = self.client.post(REFRESH_URL, {'refresh': session2.data['refresh']}, format='json')
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        # Only the current device record survives.
        self.assertEqual(list(UserDevice.objects.values_list('device_id', flat=True)), ['device-2'])

    def test_revoke_all_without_device_id_revokes_everything(self):
        session1 = self._login(device_id='device-1')
        session2 = self._login(device_id='device-2')

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {session2.data["access"]}')
        res = self.client.post(REVOKE_ALL_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.client.credentials()
        cache.clear()
        for session in (session1, session2):
            r = self.client.post(REFRESH_URL, {'refresh': session.data['refresh']}, format='json')
            self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(UserDevice.objects.exists())

    def test_token_refresh_updates_device_jti_and_last_seen(self):
        session = self._login(device_id='device-1')
        device = UserDevice.objects.get(device_id='device-1')
        old_jti = device.jti

        cache.clear()
        res = self.client.post(REFRESH_URL, {
            'refresh': session.data['refresh'], 'device_id': 'device-1',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        device.refresh_from_db()
        self.assertNotEqual(device.jti, old_jti)

    def test_devices_require_authentication(self):
        res = self.client.get(DEVICES_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        res = self.client.post(REVOKE_ALL_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class SecretHardeningTests(SimpleTestCase):
    def test_short_secret_key_raises_improperly_configured(self):
        from config.settings import _validate_secret

        with self.assertRaises(ImproperlyConfigured):
            _validate_secret('SECRET_KEY', 'too-short')

    def test_long_secret_key_passes(self):
        from config.settings import _validate_secret

        _validate_secret('SECRET_KEY', 'x' * 50)  # must not raise

    def test_identical_jwt_and_django_secrets_raise_improperly_configured(self):
        from config.settings import _validate_secrets_distinct

        secret = 'y' * 60
        with self.assertRaises(ImproperlyConfigured):
            _validate_secrets_distinct(secret, secret)

    def test_distinct_secrets_pass(self):
        from config.settings import _validate_secrets_distinct

        _validate_secrets_distinct('a' * 60, 'b' * 60)  # must not raise
