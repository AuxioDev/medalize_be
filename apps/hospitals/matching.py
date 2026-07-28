"""Duplicate-detection for the hospital registry.

Reuses apps.core.i18n.normalize_text's diacritic-folding — the same reason
'Bakı' / 'BAKI' / 'Baku' collapse to one city key applies verbatim to a
doctor typing 'Bakı Klinikası' where 'Baki Klinikasi' already exists.

``normalized_key`` has no dependency on this app's models (only on
apps.core.i18n) so apps.hospitals.models.Hospital.save() can import it at
module load time without a circular import; ``find_duplicates`` needs the
Hospital model and imports it lazily inside the function body instead.
"""
import re

from apps.core.i18n import normalize_text

# Generic business-entity words that carry no identifying information once
# stripped — 'Baki Klinikasi' and 'Klinika Baki' should collapse to the same
# key. Already run through normalize_text's diacritic folding (e.g.
# 'xəstəxana' -> 'xestexana', 'mərkəzi' -> 'merkezi') since that's the form
# tokens are compared in below.
_NOISE_TOKENS = {
    'klinika', 'clinic', 'hospital', 'xestexana', 'xestexanasi',
    'merkezi', 'medical', 'tibb', 'mmc', 'llc', 'center', 'centre',
}


def normalized_key(name):
    """Order-independent, noise-stripped normalized form of a hospital name,
    used both as the DB-level dedupe key (Hospital.normalized_name, unique
    with city) and as the first pass in find_duplicates."""
    normalized = normalize_text(name or '')
    tokens = sorted(t for t in re.split(r'\W+', normalized) if t and t not in _NOISE_TOKENS)
    return ' '.join(tokens)


def find_duplicates(name, city, exclude_pk=None):
    """Existing registry entries likely to be the same real-world hospital
    as ``name`` in ``city``. Two passes: exact match on the normalized key
    first (catches 'Bakı Klinikası' vs 'Baki Klinikasi'), then trigram
    similarity within the same city for names that normalize differently
    but read as the same place to a human. Excludes merged/rejected entries
    — those shouldn't attract new duplicates pointing at them (see
    apps.hospitals.models.Hospital.STATUS_MERGED/STATUS_REJECTED)."""
    from django.contrib.postgres.search import TrigramSimilarity

    from .models import Hospital

    visible = Hospital.objects.filter(city=city).exclude(
        status__in=(Hospital.STATUS_MERGED, Hospital.STATUS_REJECTED),
    )
    if exclude_pk:
        visible = visible.exclude(pk=exclude_pk)

    key = normalized_key(name)
    if key:
        exact = list(visible.filter(normalized_name=key))
        if exact:
            return exact

    return list(
        visible
        .annotate(similarity=TrigramSimilarity('name', name or ''))
        .filter(similarity__gte=0.4)
        .order_by('-similarity')
    )
