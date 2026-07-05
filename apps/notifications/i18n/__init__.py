"""Notification/email template registry.

Templates live in ``templates.json`` next to this module: 18 keys, each with
per-language variants (en/ru/az/tr/fr/zh) containing ``subject``/``title``/
``body`` (push+email templates) or just ``subject``/``body`` (email-only
templates). A plain JSON registry is used instead of Django's gettext
machinery so the server/Docker image does not need the system ``gettext``
package.
"""
import json
from pathlib import Path

_TEMPLATES_PATH = Path(__file__).parent / 'templates.json'
_TEMPLATES = json.loads(_TEMPLATES_PATH.read_text(encoding='utf-8'))
_DEFAULT_LANG = 'en'


def render_template(key, lang, **kwargs):
    """Look up a notification template by key + language and interpolate the
    named placeholders. Falls back to English if the language variant is
    missing (should not happen for the 6 supported languages, but keeps this
    defensive against a stale/blank ``language`` value)."""
    variants = _TEMPLATES.get(key)
    if not variants:
        raise KeyError(f'Unknown notification template: {key!r}')
    data = variants.get(lang) or variants[_DEFAULT_LANG]
    return {field: text.format(**kwargs) for field, text in data.items()}


def recipient_language(user):
    """The language notifications/emails to ``user`` should be rendered in.
    Defensive against missing/blank values (e.g. AnonymousUser in edge cases)."""
    return getattr(user, 'language', '') or _DEFAULT_LANG
