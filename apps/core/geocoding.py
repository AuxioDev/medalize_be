"""Reverse geocoding via Nominatim (OpenStreetMap's geocoder).

Called only from ``reverse_geocode_view`` when a doctor confirms a pin on
the workplace map picker — low volume, so no caching/queueing is needed.
Nominatim's usage policy requires a descriptive ``User-Agent`` and caps
requests at 1/sec; both are satisfied by proxying through this single
backend endpoint (see the ``reverse_geocode`` throttle scope) instead of
letting the mobile app call Nominatim directly from every device.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_NOMINATIM_URL = 'https://nominatim.openstreetmap.org/reverse'
_REQUEST_TIMEOUT = 5
_USER_AGENT = f'Medalize/1.0 ({settings.DEFAULT_FROM_EMAIL})'


def reverse_geocode(lat, lng, lang='en'):
    """Human-readable address for a coordinate, or ``None`` on any failure
    (timeout, non-200, unexpected shape) — the caller must degrade to
    leaving the address field as the doctor last typed it, never crash."""
    try:
        response = requests.get(
            _NOMINATIM_URL,
            params={
                'lat': lat,
                'lon': lng,
                'format': 'jsonv2',
                'accept-language': lang,
            },
            headers={'User-Agent': _USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return data.get('display_name')
    except (requests.RequestException, ValueError) as exc:
        logger.warning('Reverse geocoding failed for (%s, %s): %s', lat, lng, exc)
        return None
