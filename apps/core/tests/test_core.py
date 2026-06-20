from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

HEALTH_URL = '/api/health/'


class HealthCheckTests(APITestCase):
    def test_health_returns_200_when_db_ok(self):
        res = self.client.get(HEALTH_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'ok')

    def test_health_response_contains_required_keys(self):
        res = self.client.get(HEALTH_URL)
        self.assertIn('status', res.data)
        self.assertIn('version', res.data)
        self.assertIn('environment', res.data)
        self.assertIn('checks', res.data)

    def test_health_db_check_is_ok(self):
        res = self.client.get(HEALTH_URL)
        self.assertEqual(res.data['checks']['db'], 'ok')

    def test_health_version_matches_settings(self):
        res = self.client.get(HEALTH_URL)
        self.assertEqual(res.data['version'], settings.SPECTACULAR_SETTINGS['VERSION'])

    def test_health_environment_reflects_debug(self):
        res = self.client.get(HEALTH_URL)
        expected = 'development' if settings.DEBUG else 'production'
        self.assertEqual(res.data['environment'], expected)

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
