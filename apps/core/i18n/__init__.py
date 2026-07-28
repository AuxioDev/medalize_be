"""Azerbaijan location (city/district) registry, localized.

Mirrors the ``apps.users.i18n`` specialization registry: a plain JSON file
loaded once at import time instead of Django's gettext machinery. Covers
every city of republican subordination and every district (rayon) of
Azerbaijan, grouped into the 14 economic regions defined by the 2021
reform. Three districts that share a name with a same-named city of
republican subordination (Shaki, Lankaran, Yevlakh) are represented once,
as the city entry, to avoid two identical-looking picker options.

Translation coverage: en/az/ru/tr names are distinct and verified for
every entry. fr/zh names are only hand-verified for internationally
well-known places (the 11 cities, Nakhchivan, Karabakh, and a handful of
districts that see foreign press coverage); everywhere else fr/zh fall
back to the English name rather than guess at an unverified translation
for an obscure rayon.
"""
import json
import unicodedata
from pathlib import Path

_LOCATIONS_PATH = Path(__file__).parent / 'locations.json'
_REGIONS_PATH = Path(__file__).parent / 'regions.json'
_LOCATIONS = json.loads(_LOCATIONS_PATH.read_text(encoding='utf-8'))
_REGIONS = json.loads(_REGIONS_PATH.read_text(encoding='utf-8'))
_DEFAULT_LANG = 'en'
_LANGS = ('en', 'az', 'ru', 'tr', 'fr', 'zh')

_AZ_TRANSLIT = str.maketrans({
    'ı': 'i', 'İ': 'i', 'ə': 'e', 'Ə': 'e', 'ş': 's', 'Ş': 's',
    'ğ': 'g', 'Ğ': 'g', 'ç': 'c', 'Ç': 'c', 'ö': 'o', 'Ö': 'o',
    'ü': 'u', 'Ü': 'u',
})


def normalize_text(raw):
    """Casefold + strip Azerbaijani diacritics so free-text values ('Bakı',
    'BAKI', 'Baku') all collapse to the same lookup key. Originally private
    to this module's own city lookup; made public because
    apps.hospitals.matching reuses the exact same diacritic-folding logic to
    match a doctor-typed hospital name against the registry before falling
    back to creating a new entry."""
    text = raw.translate(_AZ_TRANSLIT).casefold().strip()
    text = unicodedata.normalize('NFKD', text)
    return ''.join(ch for ch in text if not unicodedata.combining(ch))


# Kept for any pre-existing internal call sites within this module — public
# name above is the one other apps should import.
_normalize = normalize_text


# key -> set of normalized strings that should resolve to it (built once).
_LOOKUP = {}
for _key, _entry in _LOCATIONS.items():
    _variants = {_key, *_entry['names'].values(), *_entry.get('aliases', [])}
    for _variant in _variants:
        _LOOKUP[_normalize(_variant)] = _key


def city_label(key, lang):
    """Human-readable city/district name in ``lang``. Falls back to
    English for unknown languages, and to the raw key for unknown codes."""
    entry = _LOCATIONS.get(key)
    if not entry:
        return key
    names = entry['names']
    return names.get(lang) or names.get(_DEFAULT_LANG) or key


def region_label(key, lang):
    """Human-readable economic region name in ``lang``."""
    entry = _REGIONS.get(key)
    if not entry:
        return key
    return entry.get(lang) or entry.get(_DEFAULT_LANG) or key


def city_region(key):
    """Region key for a city key, or ``None`` if unknown."""
    entry = _LOCATIONS.get(key)
    return entry['region'] if entry else None


def city_coordinates(key):
    """``(lat, lng)`` centroid for a city key, or ``None`` if unknown."""
    entry = _LOCATIONS.get(key)
    if not entry:
        return None
    return entry['lat'], entry['lng']


def resolve_city_key(raw):
    """Normalize free-text city input (any of the 6 languages, an alias,
    or the canonical key itself) to the canonical key. Returns ``None``
    when nothing matches."""
    if not raw:
        return None
    return _LOOKUP.get(_normalize(raw))


CITY_CHOICES = [
    (key, entry['names'][_DEFAULT_LANG]) for key, entry in _LOCATIONS.items()
]


def locations_payload(lang):
    """Regions with nested localized cities/districts, for ``GET /locations/``."""
    by_region = {key: [] for key in _REGIONS}
    for key, entry in _LOCATIONS.items():
        by_region[entry['region']].append({
            'key': key,
            'type': entry['type'],
            'label': city_label(key, lang),
            'lat': entry['lat'],
            'lng': entry['lng'],
        })
    result = []
    for region_key, cities in by_region.items():
        cities.sort(key=lambda c: c['label'])
        result.append({
            'key': region_key,
            'label': region_label(region_key, lang),
            'cities': cities,
        })
    result.sort(key=lambda r: r['label'])
    return result
