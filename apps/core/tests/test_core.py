from unittest.mock import patch

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from apps.core.geocoding import reverse_geocode

User = get_user_model()

HEALTH_URL = '/api/health/'
REVERSE_GEOCODE_URL = '/api/geocode/reverse/'


class HealthCheckTests(APITestCase):
    def test_health_returns_200_when_db_ok(self):
        res = self.client.get(HEALTH_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'ok')

    def test_health_response_contains_required_keys(self):
        res = self.client.get(HEALTH_URL)
        self.assertIn('status', res.data)
        self.assertIn('version', res.data)
        self.assertIn('checks', res.data)

    def test_health_does_not_disclose_environment(self):
        # The public, unauthenticated health endpoint must not reveal whether the
        # deployment is running in debug/production (recon hardening).
        res = self.client.get(HEALTH_URL)
        self.assertNotIn('environment', res.data)

    def test_health_db_check_is_ok(self):
        res = self.client.get(HEALTH_URL)
        self.assertEqual(res.data['checks']['db'], 'ok')

    def test_health_version_matches_settings(self):
        res = self.client.get(HEALTH_URL)
        self.assertEqual(res.data['version'], settings.SPECTACULAR_SETTINGS['VERSION'])

    def test_health_requires_no_auth(self):
        # No credentials set — must still return 200
        res = self.client.get(HEALTH_URL)
        self.assertNotEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_health_returns_503_when_db_fails(self):
        with patch('apps.core.views.connection') as mock_conn:
            mock_conn.cursor.side_effect = Exception('DB unreachable')
            res = self.client.get(HEALTH_URL)
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(res.data['status'], 'degraded')
        self.assertEqual(res.data['checks']['db'], 'error')


class ReverseGeocodeHelperTests(SimpleTestCase):
    def test_returns_display_name_on_success(self):
        mock_response = type('R', (), {
            'raise_for_status': lambda self: None,
            'json': lambda self: {'display_name': '123 Test St, Baku'},
        })()
        with patch('apps.core.geocoding.requests.get', return_value=mock_response):
            self.assertEqual(reverse_geocode(40.4, 49.8), '123 Test St, Baku')

    def test_timeout_returns_none(self):
        with patch('apps.core.geocoding.requests.get', side_effect=requests.Timeout):
            self.assertIsNone(reverse_geocode(40.4, 49.8))

    def test_http_error_returns_none(self):
        with patch('apps.core.geocoding.requests.get', side_effect=requests.HTTPError):
            self.assertIsNone(reverse_geocode(40.4, 49.8))

    def test_malformed_json_returns_none(self):
        mock_response = type('R', (), {
            'raise_for_status': lambda self: None,
            'json': lambda self: (_ for _ in ()).throw(ValueError('bad json')),
        })()
        with patch('apps.core.geocoding.requests.get', return_value=mock_response):
            self.assertIsNone(reverse_geocode(40.4, 49.8))


class ReverseGeocodeTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='doc@test.com', password='Pass1234', role='doctor',
        )

    def test_requires_auth(self):
        res = self.client.get(f'{REVERSE_GEOCODE_URL}?lat=40.4&lng=49.8')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_params_returns_400(self):
        self.client.force_authenticate(self.user)
        res = self.client.get(REVERSE_GEOCODE_URL)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_numeric_params_returns_400(self):
        self.client.force_authenticate(self.user)
        res = self.client.get(f'{REVERSE_GEOCODE_URL}?lat=abc&lng=49.8')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_returns_address_from_geocoder(self):
        self.client.force_authenticate(self.user)
        with patch('apps.core.views.reverse_geocode', return_value='Neftçilər Prospekti, Baku'):
            res = self.client.get(f'{REVERSE_GEOCODE_URL}?lat=40.4093&lng=49.8671')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['address'], 'Neftçilər Prospekti, Baku')

    def test_geocoder_failure_returns_null_address_not_500(self):
        self.client.force_authenticate(self.user)
        with patch('apps.core.views.reverse_geocode', return_value=None):
            res = self.client.get(f'{REVERSE_GEOCODE_URL}?lat=40.4093&lng=49.8671')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNone(res.data['address'])


class DocsVisibilityTests(TestCase):
    def test_schema_url_exists_in_debug(self):
        if not settings.DEBUG:
            self.skipTest('Schema is only available in DEBUG mode')
        res = self.client.get('/api/schema/')
        self.assertNotEqual(res.status_code, 404)

    def test_docs_url_exists_in_debug(self):
        if not settings.DEBUG:
            self.skipTest('Docs are only available in DEBUG mode')
        res = self.client.get('/api/docs/')
        self.assertNotEqual(res.status_code, 404)

    def test_schema_url_absent_in_production(self):
        if settings.DEBUG:
            self.skipTest('Only relevant in production mode')
        res = self.client.get('/api/schema/')
        self.assertEqual(res.status_code, 404)

    def test_docs_url_absent_in_production(self):
        if settings.DEBUG:
            self.skipTest('Only relevant in production mode')
        res = self.client.get('/api/docs/')
        self.assertEqual(res.status_code, 404)
