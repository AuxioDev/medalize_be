import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core import mail
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.users.models import EmailChangeRequest

User = get_user_model()

REGISTER_URL = '/api/auth/register/'
LOGIN_URL = '/api/auth/login/'
REFRESH_URL = '/api/auth/token/refresh/'
DEACTIVATE_URL = '/api/auth/deactivate/'
EMAIL_CHANGE_URL = '/api/auth/email/change/'
EMAIL_CHANGE_CONFIRM_URL = '/api/auth/email/change/confirm/'


class AccountSecurityTestCase(APITestCase):
    """Base class that registers a patient and clears throttle cache."""

    def setUp(self):
        cache.clear()
        self.client.post(REGISTER_URL, {
            'email': 'patient@test.com', 'password': 'Pass1234', 'password_confirm': 'Pass1234',
            'role': 'patient', 'first_name': 'Jane', 'last_name': 'Doe', 'privacy_consent': True,
        }, format='json')
        cache.clear()
        self.user = User.objects.get(email='patient@test.com')

    def _login(self, email='patient@test.com', password='Pass1234'):
        cache.clear()
        res = self.client.post(LOGIN_URL, {'email': email, 'password': password}, format='json')
        cache.clear()
        return res

    def _authenticate(self, access):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')


class AccountDeactivateTests(AccountSecurityTestCase):
    def test_deactivate_wrong_password_returns_400_and_stays_active(self):
        session = self._login().data
        self._authenticate(session['access'])
        res = self.client.post(DEACTIVATE_URL, {'password': 'WrongPass1'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_deactivate_missing_password_returns_400(self):
        session = self._login().data
        self._authenticate(session['access'])
        res = self.client.post(DEACTIVATE_URL, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_deactivate_without_auth_returns_401(self):
        res = self.client.post(DEACTIVATE_URL, {'password': 'Pass1234'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_deactivate_sets_inactive_and_revokes_all_sessions(self):
        session1 = self._login().data
        session2 = self._login().data

        self._authenticate(session1['access'])
        res = self.client.post(DEACTIVATE_URL, {'password': 'Pass1234'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

        # Every refresh token — including the calling session's — is blacklisted.
        cache.clear()
        self.client.credentials()
        for session in (session1, session2):
            refresh_res = self.client.post(REFRESH_URL, {'refresh': session['refresh']}, format='json')
            self.assertEqual(refresh_res.status_code, status.HTTP_401_UNAUTHORIZED)

        # Login is rejected while the account is deactivated.
        login_res = self._login()
        self.assertEqual(login_res.status_code, status.HTTP_401_UNAUTHORIZED)


class EmailChangeRequestTests(AccountSecurityTestCase):
    def setUp(self):
        super().setUp()
        session = self._login().data
        self._authenticate(session['access'])
        mail.outbox = []

    def test_request_sends_code_to_new_email(self):
        res = self.client.post(EMAIL_CHANGE_URL, {
            'new_email': 'newaddr@test.com', 'password': 'Pass1234',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(EmailChangeRequest.objects.filter(user=self.user, used=False).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['newaddr@test.com'])
        self.assertRegex(mail.outbox[0].body, r'\d{6}')

    def test_request_wrong_password_returns_400(self):
        res = self.client.post(EMAIL_CHANGE_URL, {
            'new_email': 'newaddr@test.com', 'password': 'WrongPass1',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(EmailChangeRequest.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_request_email_taken_by_other_user_returns_400(self):
        User.objects.create_user(
            email='taken@test.com', password='Pass1234', role='patient',
            first_name='O', last_name='U',
        )
        res = self.client.post(EMAIL_CHANGE_URL, {
            'new_email': 'taken@test.com', 'password': 'Pass1234',
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(EmailChangeRequest.objects.count(), 0)

    def test_request_cooldown_skips_second_code(self):
        res1 = self.client.post(EMAIL_CHANGE_URL, {
            'new_email': 'newaddr@test.com', 'password': 'Pass1234',
        }, format='json')
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        cache.clear()
        res2 = self.client.post(EMAIL_CHANGE_URL, {
            'new_email': 'newaddr@test.com', 'password': 'Pass1234',
        }, format='json')
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(EmailChangeRequest.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_new_request_after_cooldown_invalidates_previous_code(self):
        self.client.post(EMAIL_CHANGE_URL, {
            'new_email': 'first@test.com', 'password': 'Pass1234',
        }, format='json')
        first = EmailChangeRequest.objects.get()
        # Age the first code past the 60 s cooldown window.
        EmailChangeRequest.objects.filter(pk=first.pk).update(
            expires_at=timezone.now() + timedelta(minutes=8),
        )
        cache.clear()
        self.client.post(EMAIL_CHANGE_URL, {
            'new_email': 'second@test.com', 'password': 'Pass1234',
        }, format='json')
        first.refresh_from_db()
        self.assertTrue(first.used)
        self.assertEqual(EmailChangeRequest.objects.filter(used=False).count(), 1)
        self.assertEqual(EmailChangeRequest.objects.filter(used=False).get().new_email, 'second@test.com')


class EmailChangeConfirmTests(AccountSecurityTestCase):
    def setUp(self):
        super().setUp()
        self.session1 = self._login().data
        self.session2 = self._login().data
        self._authenticate(self.session1['access'])
        mail.outbox = []

    def _request_code(self, new_email='newaddr@test.com'):
        cache.clear()
        res = self.client.post(EMAIL_CHANGE_URL, {
            'new_email': new_email, 'password': 'Pass1234',
        }, format='json')
        cache.clear()
        assert res.status_code == status.HTTP_200_OK, res.data
        code = re.search(r'code is: (\d{6})', mail.outbox[-1].body).group(1)
        return code

    def _make_request(self, code='123456', **overrides):
        fields = {
            'user': self.user,
            'new_email': 'newaddr@test.com',
            'code_hash': make_password(code),
            'expires_at': timezone.now() + timedelta(minutes=10),
        }
        fields.update(overrides)
        return EmailChangeRequest.objects.create(**fields)

    def test_full_cycle_changes_email_revokes_sessions_and_notifies_old_address(self):
        code = self._request_code()
        res = self.client.post(EMAIL_CHANGE_CONFIRM_URL, {'code': code}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['email'], 'newaddr@test.com')

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'newaddr@test.com')
        self.assertTrue(EmailChangeRequest.objects.get().used)

        # Notification about the change went to the old address.
        self.assertEqual(mail.outbox[-1].to, ['patient@test.com'])
        self.assertIn('newaddr@test.com', mail.outbox[-1].body)

        # All sessions are revoked — both refresh tokens are blacklisted.
        cache.clear()
        self.client.credentials()
        for session in (self.session1, self.session2):
            refresh_res = self.client.post(REFRESH_URL, {'refresh': session['refresh']}, format='json')
            self.assertEqual(refresh_res.status_code, status.HTTP_401_UNAUTHORIZED)

        # The old email no longer logs in; the new one does.
        old_login = self._login(email='patient@test.com')
        self.assertEqual(old_login.status_code, status.HTTP_401_UNAUTHORIZED)
        new_login = self._login(email='newaddr@test.com')
        self.assertEqual(new_login.status_code, status.HTTP_200_OK)

    def test_confirm_wrong_code_returns_400_and_counts_attempt(self):
        request = self._make_request(code='123456')
        res = self.client.post(EMAIL_CHANGE_CONFIRM_URL, {'code': '654321'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        request.refresh_from_db()
        self.assertEqual(request.attempts, 1)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'patient@test.com')

    def test_confirm_expired_code_returns_400(self):
        self._make_request(code='123456', expires_at=timezone.now() - timedelta(minutes=1))
        res = self.client.post(EMAIL_CHANGE_CONFIRM_URL, {'code': '123456'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'patient@test.com')

    def test_confirm_without_pending_request_returns_400(self):
        res = self.client.post(EMAIL_CHANGE_CONFIRM_URL, {'code': '123456'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_code_retired_after_max_attempts(self):
        request = self._make_request(code='123456')
        for _ in range(EmailChangeRequest.MAX_ATTEMPTS):
            cache.clear()
            res = self.client.post(EMAIL_CHANGE_CONFIRM_URL, {'code': '000000'}, format='json')
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        request.refresh_from_db()
        self.assertTrue(request.used)

        # Even the correct code is now rejected — the code is spent.
        cache.clear()
        res = self.client.post(EMAIL_CHANGE_CONFIRM_URL, {'code': '123456'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'patient@test.com')

    def test_confirm_email_taken_meanwhile_returns_400_and_burns_code(self):
        request = self._make_request(code='123456')
        User.objects.create_user(
            email='newaddr@test.com', password='Pass1234', role='patient',
            first_name='O', last_name='U',
        )
        res = self.client.post(EMAIL_CHANGE_CONFIRM_URL, {'code': '123456'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        request.refresh_from_db()
        self.assertTrue(request.used)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'patient@test.com')

    def test_confirm_code_scoped_to_requesting_user(self):
        other = User.objects.create_user(
            email='other@test.com', password='Pass1234', role='patient',
            first_name='O', last_name='U',
        )
        EmailChangeRequest.objects.create(
            user=other, new_email='otherswap@test.com',
            code_hash=make_password('123456'),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        # Authenticated as self.user — another user's code must not match.
        res = self.client.post(EMAIL_CHANGE_CONFIRM_URL, {'code': '123456'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
