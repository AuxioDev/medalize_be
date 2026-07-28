"""Shared fixtures for apps.hospitals' test suite. Reuses
apps.appointments.tests.test_appointments' registration/login helpers
(_register_and_login, doctor_payload, cache.clear()-around-auth-calls
pattern) rather than reinventing them — see that module for why cache is
cleared around each register/login call (the register/login throttle
scopes are shared process-global cache state across tests otherwise).
"""
import datetime

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.appointments.tests.test_appointments import _register_and_login, doctor_payload
from apps.hospitals.models import Hospital
from apps.subscriptions.models import Subscription
from apps.subscriptions.plans import PLAN_HOSPITAL_BASIC
from apps.users.models import User

REGISTER_URL = '/api/auth/register/'
LOGIN_URL = '/api/auth/login/'
ME_URL = '/api/auth/me/'

HOSPITALS_URL = '/api/hospitals/'
HOSPITAL_PROFILE_URL = '/api/hospital/profile/'
HOSPITAL_STATUS_URL = '/api/hospital/status/'
HOSPITAL_SUBSCRIPTION_URL = '/api/hospital/subscription/'
HOSPITAL_PLANS_URL = '/api/hospital/subscription/plans/'
HOSPITAL_CHECKOUT_URL = '/api/hospital/subscription/checkout/'
HOSPITAL_LINKS_URL = '/api/hospital/doctors/links/'
HOSPITAL_DOCTOR_SEARCH_URL = '/api/hospital/doctors/search/'
HOSPITAL_DOCTOR_INVITE_URL = '/api/hospital/doctors/invite/'
HOSPITAL_APPOINTMENTS_URL = '/api/hospital/appointments/'
DOCTOR_HOSPITAL_LINKS_URL = '/api/doctor/hospital-links/'
WORKPLACES_URL = '/api/doctor/workplaces/'


def link_approve_url(link_id):
    return f'/api/hospital/doctors/links/{link_id}/approve/'


def link_reject_url(link_id):
    return f'/api/hospital/doctors/links/{link_id}/reject/'


def link_remove_url(link_id):
    return f'/api/hospital/doctors/links/{link_id}/remove/'


def workplace_hours_url(workplace_id):
    return f'/api/hospital/workplaces/{workplace_id}/hours/'


def doctor_workplaces_url(doctor_id):
    return f'/api/hospital/doctors/{doctor_id}/workplaces/'


def doctor_link_accept_url(link_id):
    return f'/api/doctor/hospital-links/{link_id}/accept/'


def doctor_link_decline_url(link_id):
    return f'/api/doctor/hospital-links/{link_id}/decline/'


def hospital_payload(**kwargs):
    data = {
        'email': 'hospital@test.com', 'password': 'Pass1234', 'password_confirm': 'Pass1234',
        'role': 'hospital', 'first_name': 'Admin', 'last_name': 'User', 'privacy_consent': True,
        'hospital_name': 'City Central Hospital', 'hospital_city': 'baku',
    }
    data.update(kwargs)
    return data


def approve_hospital(hospital):
    """Mirrors what apps.hospitals.admin.HospitalAdmin.approve_claims does
    — used by tests that need an approved (but not yet subscribed)
    hospital without going through the admin UI."""
    hospital.claim_status = Hospital.CLAIM_APPROVED
    if hospital.status == Hospital.STATUS_PENDING_REVIEW:
        hospital.status = Hospital.STATUS_CONFIRMED
    hospital.save(update_fields=['claim_status', 'status', 'updated_at'])
    hospital.refresh_from_db()
    return hospital


def activate_hospital_subscription(user, plan=PLAN_HOSPITAL_BASIC):
    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.plan = plan
    sub.status = Subscription.STATUS_ACTIVE
    sub.current_period_end = timezone.now() + datetime.timedelta(days=30)
    sub.save()
    return sub


class HospitalTestBase(APITestCase):
    """A registered, but not-yet-approved, hospital account with a token."""

    def setUp(self):
        self.hospital_token = _register_and_login(self.client, hospital_payload())
        self.hospital_user = User.objects.get(email='hospital@test.com')
        self.hospital = Hospital.objects.get(owner=self.hospital_user)

    def as_hospital(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.hospital_token}')


class HospitalDashboardTestBase(HospitalTestBase):
    """An approved + subscribed hospital, plus one verified doctor with a
    token — the common fixture for link/dashboard/authorization tests."""

    def setUp(self):
        super().setUp()
        approve_hospital(self.hospital)
        activate_hospital_subscription(self.hospital_user)

        self.doctor_token = _register_and_login(self.client, doctor_payload())
        self.doctor = User.objects.get(email='doctor@test.com')
        self.doctor.doctor_profile.is_verified = True
        self.doctor.doctor_profile.save(update_fields=['is_verified'])

    def as_doctor(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.doctor_token}')
