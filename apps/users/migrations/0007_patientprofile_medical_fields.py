from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0006_passwordresetotp_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='patientprofile',
            name='allergies',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='patientprofile',
            name='chronic_conditions',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='patientprofile',
            name='medications',
            field=models.TextField(blank=True),
        ),
    ]
