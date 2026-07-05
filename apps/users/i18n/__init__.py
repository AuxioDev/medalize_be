"""Doctor specialization labels, localized.

Labels live in ``specializations.json`` next to this module: one entry per
specialization code, each with en/ru/az/tr/fr/zh variants. A plain JSON
registry is used instead of Django's gettext machinery so the server/Docker
image does not need the system ``gettext`` package.
"""
import json
from pathlib import Path

_SPECIALIZATIONS_PATH = Path(__file__).parent / 'specializations.json'
_SPECIALIZATIONS = json.loads(_SPECIALIZATIONS_PATH.read_text(encoding='utf-8'))
_DEFAULT_LANG = 'en'


def specialization_label(code, lang):
    """Human-readable label for a specialization code in ``lang``. Falls back
    to English for unknown languages, and to the raw code for unknown codes."""
    entry = _SPECIALIZATIONS.get(code)
    if not entry:
        return code
    return entry.get(lang) or entry.get(_DEFAULT_LANG) or code


def viewer_language(context):
    """Language of the user viewing a serialized response. Defensive against
    a missing ``request`` in the serializer context and AnonymousUser."""
    request = context.get('request') if context else None
    user = getattr(request, 'user', None)
    return getattr(user, 'language', '') or _DEFAULT_LANG
