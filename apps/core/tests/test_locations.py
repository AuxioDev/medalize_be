from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from apps.core.i18n import (
    _LOCATIONS,
    _REGIONS,
    CITY_CHOICES,
    city_coordinates,
    city_label,
    city_region,
    locations_payload,
    region_label,
    resolve_city_key,
)

User = get_user_model()

LANGS = ['en', 'az', 'ru', 'tr', 'fr', 'zh']
LOCATIONS_URL = '/api/locations/'


class LocationRegistryTests(SimpleTestCase):
    def test_city_label_localized(self):
        self.assertEqual(city_label('baku', 'en'), 'Baku')
        self.assertEqual(city_label('baku', 'az'), 'Bakı')
        self.assertEqual(city_label('baku', 'ru'), 'Баку')

    def test_city_label_unknown_language_falls_back_to_english(self):
        self.assertEqual(city_label('baku', 'de'), 'Baku')
        self.assertEqual(city_label('baku', ''), 'Baku')

    def test_city_label_unknown_key_returns_key(self):
        self.assertEqual(city_label('atlantis', 'ru'), 'atlantis')

    def test_region_label_localized(self):
        self.assertEqual(region_label('karabakh', 'en'), 'Karabakh')
        self.assertEqual(region_label('karabakh', 'ru'), 'Карабах')

    def test_region_label_unknown_key_returns_key(self):
        self.assertEqual(region_label('nowhere', 'en'), 'nowhere')

    def test_city_region(self):
        self.assertEqual(city_region('baku'), 'baku')
        self.assertEqual(city_region('ganja'), 'ganja_dashkasan')
        self.assertIsNone(city_region('atlantis'))

    def test_city_coordinates(self):
        lat, lng = city_coordinates('baku')
        self.assertAlmostEqual(lat, 40.4093)
        self.assertAlmostEqual(lng, 49.8671)
        self.assertIsNone(city_coordinates('atlantis'))

    def test_registry_has_75_locations_in_14_regions(self):
        self.assertEqual(len(_LOCATIONS), 75)
        self.assertEqual(len(_REGIONS), 14)
        self.assertEqual(len(CITY_CHOICES), 75)

    def test_every_location_has_all_six_languages_non_blank(self):
        for key, entry in _LOCATIONS.items():
            for lang in LANGS:
                self.assertIn(lang, entry['names'], f'{key} is missing language {lang}')
                self.assertTrue(entry['names'][lang], f'{key}/{lang} is blank')

    def test_every_region_has_all_six_languages_non_blank(self):
        for key, entry in _REGIONS.items():
            for lang in LANGS:
                self.assertIn(lang, entry, f'{key} is missing language {lang}')
                self.assertTrue(entry[lang], f'{key}/{lang} is blank')

    def test_every_location_region_exists(self):
        for key, entry in _LOCATIONS.items():
            self.assertIn(entry['region'], _REGIONS, f'{key} references unknown region')

    def test_resolve_city_key_canonical(self):
        self.assertEqual(resolve_city_key('baku'), 'baku')
        self.assertEqual(resolve_city_key('BAKU'), 'baku')

    def test_resolve_city_key_english_name(self):
        self.assertEqual(resolve_city_key('Baku'), 'baku')
        self.assertEqual(resolve_city_key('Ganja'), 'ganja')

    def test_resolve_city_key_azerbaijani_spelling_with_diacritics(self):
        self.assertEqual(resolve_city_key('Bakı'), 'baku')
        self.assertEqual(resolve_city_key('Gəncə'), 'ganja')
        self.assertEqual(resolve_city_key('Sumqayıt'), 'sumgait')

    def test_resolve_city_key_alias(self):
        self.assertEqual(resolve_city_key('Sheki'), 'shaki')
        self.assertEqual(resolve_city_key('Gabala'), 'qabala')

    def test_resolve_city_key_unresolvable_returns_none(self):
        self.assertIsNone(resolve_city_key('Atlantis'))
        self.assertIsNone(resolve_city_key(''))
        self.assertIsNone(resolve_city_key(None))

    def test_locations_payload_structure(self):
        payload = locations_payload('en')
        self.assertEqual(len(payload), 14)
        total_cities = sum(len(region['cities']) for region in payload)
        self.assertEqual(total_cities, 75)
        baku_region = next(r for r in payload if r['key'] == 'baku')
        self.assertEqual(baku_region['label'], 'Baku')
        self.assertEqual(baku_region['cities'][0]['label'], 'Baku')

    def test_locations_payload_localized(self):
        payload_ru = locations_payload('ru')
        karabakh = next(r for r in payload_ru if r['key'] == 'karabakh')
        self.assertEqual(karabakh['label'], 'Карабах')


class LocationsEndpointTests(APITestCase):
    def test_locations_endpoint_requires_no_auth(self):
        res = self.client.get(LOCATIONS_URL)
        self.assertEqual(res.status_code, 200)

    def test_locations_endpoint_returns_all_regions(self):
        res = self.client.get(LOCATIONS_URL)
        self.assertEqual(len(res.data), 14)
        total_cities = sum(len(region['cities']) for region in res.data)
        self.assertEqual(total_cities, 75)

    def test_locations_endpoint_localizes_by_viewer_language(self):
        user = User.objects.create_user(
            email='ru-speaker@test.com', password='Pass1234', role='patient', language='ru',
        )
        self.client.force_authenticate(user)
        res = self.client.get(LOCATIONS_URL)
        karabakh = next(r for r in res.data if r['key'] == 'karabakh')
        self.assertEqual(karabakh['label'], 'Карабах')

    def test_locations_endpoint_defaults_to_english_when_anonymous(self):
        res = self.client.get(LOCATIONS_URL)
        baku_region = next(r for r in res.data if r['key'] == 'baku')
        self.assertEqual(baku_region['label'], 'Baku')
