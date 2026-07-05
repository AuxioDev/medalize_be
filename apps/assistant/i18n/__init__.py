"""User-facing assistant texts, localized.

Texts live in ``messages.json`` next to this module: 3 keys (``disclaimer``,
``not_medical_refusal``, ``assistant_unavailable``), each with en/ru/az/tr/fr/zh
variants. A plain JSON registry is used instead of Django's gettext machinery
so the server/Docker image does not need the system ``gettext`` package.
"""
import json
from pathlib import Path

_MESSAGES_PATH = Path(__file__).parent / 'messages.json'
_MESSAGES = json.loads(_MESSAGES_PATH.read_text(encoding='utf-8'))
_DEFAULT_LANG = 'en'


def assistant_message(key, lang):
    variants = _MESSAGES.get(key, {})
    return variants.get(lang) or variants.get(_DEFAULT_LANG) or ''
