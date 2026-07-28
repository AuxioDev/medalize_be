# Hand-written (not autodetected): Django's makemigrations does not detect a
# model rename here — it proposes DeleteModel(DoctorSubscription) +
# CreateModel(Subscription), which would drop every doctor's real billing
# history. RenameModel is a plain `ALTER TABLE ... RENAME` that preserves
# every row (and every FK pointing at it) instead.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0002_backfill_existing_doctors'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='DoctorSubscription',
            new_name='Subscription',
        ),
        migrations.AlterField(
            model_name='subscription',
            name='plan',
            # 20, not 10: 'hospital_basic' is 14 characters. New choices add
            # the two hospital plan codes.
            field=models.CharField(
                blank=True,
                choices=[
                    ('', 'None'),
                    ('basic', 'Başlanğıc'),
                    ('pro', 'Peşəkar'),
                    ('hospital_basic', 'Klinika'),
                    ('hospital_pro', 'Klinika Plus'),
                ],
                default='',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='subscriptionpayment',
            name='plan',
            field=models.CharField(
                choices=[
                    ('', 'None'),
                    ('basic', 'Başlanğıc'),
                    ('pro', 'Peşəkar'),
                    ('hospital_basic', 'Klinika'),
                    ('hospital_pro', 'Klinika Plus'),
                ],
                max_length=20,
            ),
        ),
    ]
