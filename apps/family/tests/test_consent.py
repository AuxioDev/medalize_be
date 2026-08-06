import re
from datetime import date, timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from apps.appointments.tests.test_appointments import _register_and_login, patient_payload
from apps.family.models import Dependent
from apps.family.throttles import DependentConsentRateThrottle

DEPENDENTS_URL = '/api/dependents/'


def _detail_url(pk):
    return f'{DEPENDENTS_URL}{pk}/'


def _reject_url(pk):
    return f'{DEPENDENTS_URL}{pk}/consent/reject/'


def _today_minus_years(years, extra_days=0):
    """A date `years` years before today (minus a few extra days so the
    birthday has definitely already occurred this year, keeping computed
    age exact and test data deterministic)."""
    t = date.today()
    try:
        return t.replace(year=t.year - years) - timedelta(days=extra_days + 1)
    except ValueError:
        # Feb 29 birthdays on a non-leap target year.
        return t.replace(year=t.year - years, day=28) - timedelta(days=extra_days + 1)


def _child_dob():
    return _today_minus_years(6).isoformat()


def _adult_dob():
    return _today_minus_years(30).isoformat()


def _extract_token(body):
    m = re.search(r'token=([^&\s]+)', body)
    assert m, f'no token found in email body: {body!r}'
    return m.group(1)


def _dependent_payload(**kwargs):
    data = {
        'first_name': 'Alice', 'last_name': 'Doe', 'relationship': 'child',
        'date_of_birth': _child_dob(), 'blood_type': 'O+',
        'allergies': '', 'chronic_conditions': '', 'medications': '',
    }
    data.update(kwargs)
    return data


class ConsentTestBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.patient_token = _register_and_login(self.client, patient_payload())
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.patient_token}')
        mail.outbox.clear()

    def _patient(self):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.get(email='patient@test.com')


class DateOfBirthRequiredTests(ConsentTestBase):
    def test_create_without_date_of_birth_returns_400(self):
        payload = _dependent_payload()
        del payload['date_of_birth']
        res = self.client.post(DEPENDENTS_URL, payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('date_of_birth', res.data['errors'])

    def test_create_with_null_date_of_birth_returns_400(self):
        res = self.client.post(DEPENDENTS_URL, _dependent_payload(date_of_birth=None), format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('date_of_birth', res.data['errors'])

    def test_create_with_date_of_birth_succeeds(self):
        res = self.client.post(DEPENDENTS_URL, _dependent_payload(), format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_existing_dependent_without_dob_is_unaffected_by_unrelated_edit(self):
        # Simulates a pre-existing row from before date_of_birth was
        # required — created directly via the ORM (bypassing the
        # serializer), same as production data would look like.
        dependent = Dependent.objects.create(
            managed_by=self._patient(), first_name='Bob', relationship='spouse',
        )
        res = self.client.patch(_detail_url(dependent.id), {'allergies': 'Peanuts'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        dependent.refresh_from_db()
        self.assertIsNone(dependent.date_of_birth)
        self.assertEqual(dependent.allergies, 'Peanuts')

    def test_edit_cannot_null_out_an_existing_date_of_birth(self):
        res = self.client.post(DEPENDENTS_URL, _dependent_payload(), format='json')
        dependent_id = res.data['id']
        res = self.client.patch(_detail_url(dependent_id), {'date_of_birth': None}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('date_of_birth', res.data['errors'])

    def test_edit_that_omits_date_of_birth_leaves_it_untouched(self):
        res = self.client.post(DEPENDENTS_URL, _dependent_payload(), format='json')
        dependent_id = res.data['id']
        res = self.client.patch(_detail_url(dependent_id), {'allergies': 'Shellfish'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['date_of_birth'], _child_dob())


class AdultConsentNoticeTests(ConsentTestBase):
    def test_minor_dependent_does_not_require_contact_email(self):
        res = self.client.post(DEPENDENTS_URL, _dependent_payload(date_of_birth=_child_dob()), format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIsNone(res.data['consent_notice_sent_at'])

    def test_adult_dependent_without_contact_email_returns_400(self):
        res = self.client.post(
            DEPENDENTS_URL,
            _dependent_payload(relationship='spouse', date_of_birth=_adult_dob()),
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('contact_email', res.data['errors'])

    def test_adult_dependent_with_only_contact_phone_returns_400(self):
        # Phone alone is deliberately not enough — see Dependent's docstring
        # and DependentCreateSerializer.validate: there is no SMS delivery
        # path in this codebase, only email, so the notice can only be
        # guaranteed to reach the dependent via contact_email.
        res = self.client.post(
            DEPENDENTS_URL,
            _dependent_payload(
                relationship='spouse', date_of_birth=_adult_dob(), contact_phone='+994501234567',
            ),
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('contact_email', res.data['errors'])

    def test_adult_dependent_with_contact_email_sends_notice(self):
        res = self.client.post(
            DEPENDENTS_URL,
            _dependent_payload(
                relationship='spouse', date_of_birth=_adult_dob(), contact_email='spouse@test.com',
            ),
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIsNotNone(res.data['consent_notice_sent_at'])

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['spouse@test.com'])
        self.assertIn('Jane Doe', mail.outbox[0].body)
        self.assertIn('reject', mail.outbox[0].body)

        dependent = Dependent.objects.get(pk=res.data['id'])
        self.assertTrue(dependent.consent_token_hash)
        self.assertIsNotNone(dependent.consent_token_expires_at)

    def test_editing_a_minor_to_adult_without_contact_email_returns_400(self):
        res = self.client.post(DEPENDENTS_URL, _dependent_payload(), format='json')
        dependent_id = res.data['id']
        res = self.client.patch(
            _detail_url(dependent_id), {'date_of_birth': _adult_dob()}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('contact_email', res.data['errors'])

    def test_editing_a_minor_to_adult_with_contact_email_sends_notice(self):
        res = self.client.post(DEPENDENTS_URL, _dependent_payload(), format='json')
        dependent_id = res.data['id']
        mail.outbox.clear()
        res = self.client.patch(
            _detail_url(dependent_id),
            {'date_of_birth': _adult_dob(), 'contact_email': 'grownup@test.com'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['grownup@test.com'])

    def test_unrelated_edit_of_already_adult_dependent_does_not_resend_notice(self):
        res = self.client.post(
            DEPENDENTS_URL,
            _dependent_payload(
                relationship='spouse', date_of_birth=_adult_dob(), contact_email='spouse@test.com',
            ),
            format='json',
        )
        dependent_id = res.data['id']
        mail.outbox.clear()
        res = self.client.patch(_detail_url(dependent_id), {'allergies': 'Shellfish'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)

    def test_changing_contact_email_of_adult_dependent_resends_notice_to_new_address(self):
        res = self.client.post(
            DEPENDENTS_URL,
            _dependent_payload(
                relationship='spouse', date_of_birth=_adult_dob(), contact_email='old@test.com',
            ),
            format='json',
        )
        dependent_id = res.data['id']
        mail.outbox.clear()
        res = self.client.patch(
            _detail_url(dependent_id), {'contact_email': 'new@test.com'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['new@test.com'])

    def test_clearing_contact_email_of_adult_dependent_returns_400(self):
        res = self.client.post(
            DEPENDENTS_URL,
            _dependent_payload(
                relationship='spouse', date_of_birth=_adult_dob(), contact_email='spouse@test.com',
            ),
            format='json',
        )
        dependent_id = res.data['id']
        res = self.client.patch(_detail_url(dependent_id), {'contact_email': ''}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('contact_email', res.data['errors'])


class SweepDependentConsentNoticesTests(ConsentTestBase):
    """apps.family.tasks.sweep_dependent_consent_notices — the daily sweep
    for a dependent who passively crosses 18 with no create/update in
    between (AdultConsentNoticeTests above only covers the create/edit
    triggers)."""

    def test_dependent_who_ages_into_18_without_an_edit_gets_notified(self):
        res = self.client.post(
            DEPENDENTS_URL,
            _dependent_payload(
                relationship='child', date_of_birth=_child_dob(), contact_email='future-adult@test.com',
            ),
            format='json',
        )
        dependent_id = res.data['id']
        self.assertIsNone(res.data['consent_notice_sent_at'])
        mail.outbox.clear()

        # Simulate the birthday passing with no profile edit in between —
        # a direct DB write, bypassing the serializer entirely, same as
        # DateOfBirthRequiredTests.test_existing_dependent_without_dob_is_
        # unaffected_by_unrelated_edit does above for the null-DOB case.
        Dependent.objects.filter(pk=dependent_id).update(date_of_birth=_adult_dob())

        from apps.family.tasks import sweep_dependent_consent_notices
        sweep_dependent_consent_notices()

        dependent = Dependent.objects.get(pk=dependent_id)
        self.assertIsNotNone(dependent.consent_notice_sent_at)
        self.assertTrue(dependent.consent_token_hash)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['future-adult@test.com'])

    def test_already_notified_adult_dependent_is_not_renotified(self):
        res = self.client.post(
            DEPENDENTS_URL,
            _dependent_payload(
                relationship='spouse', date_of_birth=_adult_dob(), contact_email='spouse@test.com',
            ),
            format='json',
        )
        dependent_id = res.data['id']
        self.assertIsNotNone(res.data['consent_notice_sent_at'])
        mail.outbox.clear()

        from apps.family.tasks import sweep_dependent_consent_notices
        sweep_dependent_consent_notices()

        self.assertEqual(len(mail.outbox), 0)

    def test_minor_dependent_is_untouched_by_sweep(self):
        res = self.client.post(DEPENDENTS_URL, _dependent_payload(date_of_birth=_child_dob()), format='json')
        dependent_id = res.data['id']
        mail.outbox.clear()

        from apps.family.tasks import sweep_dependent_consent_notices
        sweep_dependent_consent_notices()

        dependent = Dependent.objects.get(pk=dependent_id)
        self.assertIsNone(dependent.consent_notice_sent_at)
        self.assertEqual(len(mail.outbox), 0)

    def test_adult_dependent_without_contact_email_is_skipped_without_error(self):
        # A legacy row a real serializer path could never produce (adult
        # DOB with no contact_email) — created directly via the ORM, same
        # as DateOfBirthRequiredTests' null-DOB fixture. The sweep must
        # skip it, not crash issue_consent_notice against a blank email.
        Dependent.objects.create(
            managed_by=self._patient(), first_name='Bob', relationship='spouse',
            date_of_birth=_adult_dob(),
        )
        mail.outbox.clear()

        from apps.family.tasks import sweep_dependent_consent_notices
        sweep_dependent_consent_notices()

        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_dependent_is_not_notified(self):
        res = self.client.post(
            DEPENDENTS_URL,
            _dependent_payload(
                relationship='child', date_of_birth=_child_dob(), contact_email='ignored@test.com',
            ),
            format='json',
        )
        dependent_id = res.data['id']
        Dependent.objects.filter(pk=dependent_id).update(
            date_of_birth=_adult_dob(), is_active=False,
        )
        mail.outbox.clear()

        from apps.family.tasks import sweep_dependent_consent_notices
        sweep_dependent_consent_notices()

        self.assertEqual(len(mail.outbox), 0)


class DependentConsentRejectViewTests(ConsentTestBase):
    def _create_adult_dependent(self, contact_email='spouse@test.com'):
        res = self.client.post(
            DEPENDENTS_URL,
            _dependent_payload(
                relationship='spouse', date_of_birth=_adult_dob(), contact_email=contact_email,
            ),
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        token = _extract_token(mail.outbox[0].body)
        self.client.credentials()  # the reject link is used with no auth at all
        return res.data['id'], token

    def test_get_with_valid_token_returns_confirm_page_without_changing_state(self):
        dependent_id, token = self._create_adult_dependent()
        res = self.client.get(_reject_url(dependent_id), {'token': token})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn(b'<form method="post">', res.content)

        dependent = Dependent.objects.get(pk=dependent_id)
        self.assertTrue(dependent.is_active)
        self.assertIsNone(dependent.consent_objected_at)

    def test_get_with_wrong_token_returns_400(self):
        dependent_id, _token = self._create_adult_dependent()
        res = self.client.get(_reject_url(dependent_id), {'token': 'not-the-real-token'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_get_for_unknown_dependent_returns_400_not_500(self):
        res = self.client.get(_reject_url('00000000-0000-0000-0000-000000000000'), {'token': 'x'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_post_with_valid_token_deactivates_dependent(self):
        dependent_id, token = self._create_adult_dependent()
        res = self.client.post(_reject_url(dependent_id), {'token': token})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        dependent = Dependent.objects.get(pk=dependent_id)
        self.assertFalse(dependent.is_active)
        self.assertIsNotNone(dependent.consent_objected_at)
        self.assertEqual(dependent.consent_token_hash, '')

    def test_post_reuses_the_soft_delete_mechanism_dependent_disappears_from_active_list(self):
        dependent_id, token = self._create_adult_dependent()
        self.client.post(_reject_url(dependent_id), {'token': token})

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.patient_token}')
        res = self.client.get(DEPENDENTS_URL)
        ids = [d['id'] for d in res.data]
        self.assertNotIn(dependent_id, ids)
        # Still exists (soft delete, not a hard delete) — same guarantee as
        # an ordinary account-holder-initiated delete.
        self.assertTrue(Dependent.objects.filter(pk=dependent_id).exists())

    def test_token_cannot_be_reused(self):
        dependent_id, token = self._create_adult_dependent()
        first = self.client.post(_reject_url(dependent_id), {'token': token})
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        second = self.client.post(_reject_url(dependent_id), {'token': token})
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_post_with_missing_token_returns_400(self):
        dependent_id, _token = self._create_adult_dependent()
        res = self.client.post(_reject_url(dependent_id), {})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class DependentConsentRejectViewThrottleTests(ConsentTestBase):
    def test_repeated_requests_are_rate_limited(self):
        dependent_id, token = self._create_adult_dependent()

        # DRF's SimpleRateThrottle subclasses bind THROTTLE_RATES from
        # api_settings at import time, so @override_settings(REST_FRAMEWORK=
        # ...) does not retroactively refresh an already-imported throttle
        # class (a well-known DRF testing gotcha) — patch get_rate() on our
        # throttle class directly instead, which is what actually determines
        # the request budget per instance.
        with mock.patch.object(DependentConsentRateThrottle, 'get_rate', return_value='3/hour'):
            statuses = []
            for _ in range(4):
                res = self.client.get(_reject_url(dependent_id), {'token': 'irrelevant'})
                statuses.append(res.status_code)

        self.assertIn(status.HTTP_429_TOO_MANY_REQUESTS, statuses)

    def _create_adult_dependent(self, contact_email='spouse@test.com'):
        res = self.client.post(
            DEPENDENTS_URL,
            _dependent_payload(
                relationship='spouse', date_of_birth=_adult_dob(), contact_email=contact_email,
            ),
            format='json',
        )
        token = _extract_token(mail.outbox[0].body)
        self.client.credentials()
        return res.data['id'], token
