from django.db import migrations

from apps.core.i18n import city_coordinates, city_region, city_label, resolve_city_key

_DEFAULT_LANG = 'en'


def backfill(apps, schema_editor):
    Workplace = apps.get_model('doctors', 'Workplace')
    unresolved = []
    for wp in Workplace.objects.all():
        key = resolve_city_key(wp.city)
        if not key:
            unresolved.append((str(wp.pk), wp.name, wp.city))
            continue
        wp.city = key
        wp.region = city_region(key) or ''
        if wp.latitude is None or wp.longitude is None:
            coords = city_coordinates(key)
            if coords:
                wp.latitude, wp.longitude = coords
        wp.save(update_fields=['city', 'region', 'latitude', 'longitude'])

    if unresolved:
        print(
            f'\nWARNING: {len(unresolved)} workplace(s) have a city value that '
            "does not match the Azerbaijan locations registry and were left "
            "unchanged. Fix these in the admin (they will fail validation "
            "against Workplace.city choices until corrected):"
        )
        for pk, name, raw_city in unresolved:
            print(f'  workplace {pk} ({name!r}): city={raw_city!r}')


def unbackfill(apps, schema_editor):
    Workplace = apps.get_model('doctors', 'Workplace')
    for wp in Workplace.objects.all():
        wp.city = city_label(wp.city, _DEFAULT_LANG)
        wp.region = ''
        wp.save(update_fields=['city', 'region'])


class Migration(migrations.Migration):

    dependencies = [
        ('doctors', '0004_remove_workplace_workplace_city_trgm_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
