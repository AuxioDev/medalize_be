from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

User = get_user_model()

REGISTER_URL = '/api/auth/register/'
LOGIN_URL = '/api/auth/login/'
REFRESH_URL = '/api/auth/token/refresh/'
LOGOUT_URL = '/api/auth/logout/'
ME_URL = '/api/auth/me/'


def patient_payload(**kwargs):
    data = {'email': 'patient@test.com', 'password': 'Pass1234', 'password_confirm': 'Pass1234',
            'role': 'patient', 'first_name': 'Jane', 'last_name': 'Doe'}
    data.update(kwargs)
    return data


def doctor_payload(**kwargs):
    data = {'email': 'doctor@test.com', 'password': 'Pass1234', 'password_confirm': 'Pass1234',
            'role': 'doctor', 'first_name': 'John', 'last_name': 'Smith'}
    data.update(kwargs)
    return data


class AuthTestCase(APITestCase):
    """Base class that clears throttle cache before every test."""
    def setUp(self):
        cache.clear()


class RegisterTests(AuthTestCase):
    def test_register_patient_returns_201_no_tokens(self):
        res = self.client.post(REGISTER_URL, patient_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn('user_id', res.data)
        self.assertEqual(res.data['role'], 'patient')
        self.assertNotIn('access', res.data)
        self.assertNotIn('refresh', res.data)

    def test_register_doctor_returns_201_no_tokens(self):
        res = self.client.post(REGISTER_URL, doctor_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['role'], 'doctor')
        self.assertNotIn('access', res.data)

    def test_register_mismatched_passwords_returns_400(self):
        res = self.client.post(REGISTER_URL, patient_payload(password_confirm='wrong'), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_duplicate_email_returns_400(self):
        self.client.post(REGISTER_URL, patient_payload(), format='json')
        cache.clear()
        res = self.client.post(REGISTER_URL, patient_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class LoginTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.client.post(REGISTER_URL, patient_payload(), format='json')
        cache.clear()
        self.client.post(REGISTER_URL, doctor_payload(), format='json')
        cache.clear()

    def _login(self, email='patient@test.com', password='Pass1234', remember_me=False):
        return self.client.post(LOGIN_URL, {
            'email': email, 'password': password, 'remember_me': remember_me,
        }, format='json')

    def test_login_patient_returns_200_with_tokens_and_role(self):
        res = self._login()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('access', res.data)
        self.assertIn('refresh', res.data)
        self.assertEqual(res.data['role'], 'patient')

    def test_login_doctor_returns_onboarding_complete_false(self):
        res = self._login('doctor@test.com', 'Pass1234')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data['onboarding_complete'])

    def test_login_wrong_password_returns_401_invalid_credentials(self):
        res = self._login(password='wrongpass')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(res.data['code'], 'invalid_credentials')

    def test_login_remember_me_true_refresh_lifetime_30_days(self):
        res = self._login(remember_me=True)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        from rest_framework_simplejwt.tokens import RefreshToken as RT
        token = RT(res.data['refresh'])
        lifetime_days = (token.payload['exp'] - token.payload['iat']) / 86400
        self.assertAlmostEqual(lifetime_days, 30, delta=1)

    def test_login_remember_me_false_refresh_lifetime_1_day(self):
        res = self._login(remember_me=False)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        from rest_framework_simplejwt.tokens import RefreshToken as RT
        token = RT(res.data['refresh'])
        lifetime_days = (token.payload['exp'] - token.payload['iat']) / 86400
        self.assertAlmostEqual(lifetime_days, 1, delta=0.1)


class MeViewTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.client.post(REGISTER_URL, patient_payload(), format='json')
        cache.clear()
        res = self.client.post(LOGIN_URL, {'email': 'patient@test.com', 'password': 'Pass1234'}, format='json')
        cache.clear()
        self.access_token = res.data['access']
        self.refresh_token = res.data['refresh']

    def test_me_with_valid_token_returns_200_role_aware(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access_token}')
        res = self.client.get(ME_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['role'], 'patient')
        self.assertIn('profile', res.data)

    def test_me_without_token_returns_401(self):
        res = self.client.get(ME_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(res.data['code'], 'not_authenticated')

    def test_me_with_expired_token_returns_401_token_expired(self):
        user = User.objects.get(email='patient@test.com')
        token = AccessToken.for_user(user)
        token.set_exp(lifetime=timedelta(seconds=-1))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(token)}')
        res = self.client.get(ME_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(res.data['code'], 'token_expired')


class TokenRefreshTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.client.post(REGISTER_URL, patient_payload(), format='json')
        cache.clear()
        res = self.client.post(LOGIN_URL, {'email': 'patient@test.com', 'password': 'Pass1234'}, format='json')
        cache.clear()
        self.access_token = res.data['access']
        self.refresh_token = res.data['refresh']

    def test_refresh_returns_200_new_access_and_role(self):
        res = self.client.post(REFRESH_URL, {'refresh': self.refresh_token}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('access', res.data)
        self.assertIn('role', res.data)
        self.assertEqual(res.data['role'], 'patient')

    def test_refresh_blacklisted_token_returns_401(self):
        # Rotate: first call consumes the token and issues a new one
        res1 = self.client.post(REFRESH_URL, {'refresh': self.refresh_token}, format='json')
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        # Original is now blacklisted
        res2 = self.client.post(REFRESH_URL, {'refresh': self.refresh_token}, format='json')
        self.assertEqual(res2.status_code, status.HTTP_401_UNAUTHORIZED)


class LogoutTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.client.post(REGISTER_URL, patient_payload(), format='json')
        cache.clear()
        res = self.client.post(LOGIN_URL, {'email': 'patient@test.com', 'password': 'Pass1234'}, format='json')
        cache.clear()
        self.access_token = res.data['access']
        self.refresh_token = res.data['refresh']

    def test_logout_returns_204_and_blacklists_refresh_token(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access_token}')
        res = self.client.post(LOGOUT_URL, {'refresh': self.refresh_token}, format='json')
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        # Confirm the refresh token is now blacklisted
        res2 = self.client.post(REFRESH_URL, {'refresh': self.refresh_token}, format='json')
        self.assertEqual(res2.status_code, status.HTTP_401_UNAUTHORIZED)


class RegisterValidationTests(AuthTestCase):
    def test_register_weak_password_returns_400_validation_error(self):
        res = self.client.post(REGISTER_URL, patient_payload(password='short', password_confirm='short'), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['code'], 'validation_error')
        self.assertIn('password', res.data['errors'])

    def test_register_invalid_email_returns_400_validation_error(self):
        res = self.client.post(REGISTER_URL, patient_payload(email='not-an-email'), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['code'], 'validation_error')
        self.assertIn('email', res.data['errors'])

    def test_register_missing_required_fields_returns_400_validation_error(self):
        res = self.client.post(REGISTER_URL, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['code'], 'validation_error')

    def test_register_duplicate_email_returns_400_validation_error(self):
        self.client.post(REGISTER_URL, patient_payload(), format='json')
        cache.clear()
        res = self.client.post(REGISTER_URL, patient_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['code'], 'validation_error')
        self.assertIn('email', res.data['errors'])

    def test_register_password_mismatch_returns_400_validation_error(self):
        res = self.client.post(REGISTER_URL, patient_payload(password_confirm='Different1'), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['code'], 'validation_error')


class LoginErrorTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.client.post(REGISTER_URL, patient_payload(), format='json')
        cache.clear()

    def test_login_nonexistent_email_returns_401_invalid_credentials(self):
        res = self.client.post(LOGIN_URL, {'email': 'nobody@test.com', 'password': 'Pass1234'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(res.data['code'], 'invalid_credentials')
        self.assertEqual(res.data['message'], 'Invalid email or password.')

    def test_login_wrong_password_message_does_not_leak_account_existence(self):
        res = self.client.post(LOGIN_URL, {'email': 'patient@test.com', 'password': 'WrongPass1'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(res.data['message'], 'Invalid email or password.')

    def test_login_missing_fields_returns_400_validation_error(self):
        res = self.client.post(LOGIN_URL, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['code'], 'validation_error')

    def test_login_inactive_account_returns_401_invalid_credentials(self):
        user = User.objects.get(email='patient@test.com')
        user.is_active = False
        user.save()
        res = self.client.post(LOGIN_URL, {'email': 'patient@test.com', 'password': 'Pass1234'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(res.data['code'], 'invalid_credentials')


class LogoutErrorTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.client.post(REGISTER_URL, patient_payload(), format='json')
        cache.clear()
        res = self.client.post(LOGIN_URL, {'email': 'patient@test.com', 'password': 'Pass1234'}, format='json')
        cache.clear()
        self.access_token = res.data['access']
        self.refresh_token = res.data['refresh']

    def test_logout_without_auth_returns_401(self):
        res = self.client.post(LOGOUT_URL, {'refresh': self.refresh_token}, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(res.data['code'], 'not_authenticated')

    def test_logout_missing_refresh_field_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access_token}')
        res = self.client.post(LOGOUT_URL, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['code'], 'token_invalid')

    def test_logout_invalid_refresh_token_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access_token}')
        res = self.client.post(LOGOUT_URL, {'refresh': 'not-a-real-token'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['code'], 'token_invalid')


class RolePermissionTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.client.post(REGISTER_URL, patient_payload(), format='json')
        cache.clear()

    def test_is_doctor_permission_false_for_patient(self):
        user = User.objects.get(email='patient@test.com')
        from apps.users.permissions import IsDoctor
        from unittest.mock import MagicMock
        request = MagicMock()
        request.user = user
        self.assertFalse(IsDoctor().has_permission(request, None))

    def test_is_patient_permission_true_for_patient(self):
        user = User.objects.get(email='patient@test.com')
        from apps.users.permissions import IsPatient
        from unittest.mock import MagicMock
        request = MagicMock()
        request.user = user
        self.assertTrue(IsPatient().has_permission(request, None))
