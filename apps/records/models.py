import uuid

from django.conf import settings
from django.db import models


def record_storage():
    """Storage for uploaded medical records (lab results, imaging, other
    documents). Uses Cloudinary's private/authenticated raw storage
    (``PrivateRawCloudinaryStorage`` — signed URLs only, same delivery type
    already used for doctor diplomas) when Cloudinary is configured, else
    falls back to local storage — identical pattern to
    ``apps.users.models.diploma_storage``, so local dev/tests work without a
    Cloudinary account. A callable (not a class instance) keeps the migration
    stable across environments regardless of which backend is active."""
    if getattr(settings, 'USE_CLOUDINARY', False):
        from apps.core.storage import PrivateRawCloudinaryStorage

        return PrivateRawCloudinaryStorage()
    from django.core.files.storage import default_storage

    return default_storage


class MedicalRecord(models.Model):
    TYPE_LAB_RESULT = 'lab_result'
    TYPE_IMAGING = 'imaging'
    TYPE_DOCUMENT = 'document'
    TYPE_OTHER = 'other'
    TYPE_CHOICES = [
        (TYPE_LAB_RESULT, 'Lab result'), (TYPE_IMAGING, 'Imaging'),
        (TYPE_DOCUMENT, 'Document'), (TYPE_OTHER, 'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='medical_records',
    )
    # Optional: which of the account owner's managed family members this
    # document is actually for. `patient` above is unchanged. Added in a
    # follow-up migration once apps.family exists (see
    # apps.appointments.models.Appointment.dependent for the full rationale).
    dependent = models.ForeignKey(
        'family.Dependent', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='medical_records',
    )
    record_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_OTHER)
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to='records/', storage=record_storage)
    record_date = models.DateField(null=True, blank=True)  # clinical date (e.g. lab date), not upload date
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-record_date', '-created_at']

    def __str__(self):
        return f'{self.title} ({self.patient})'
