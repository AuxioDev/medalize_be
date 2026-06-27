from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0007_patientprofile_medical_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='doctorprofile',
            name='consultation_fee',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
        ),
    ]
