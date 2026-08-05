import logging
import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

logger = logging.getLogger(__name__)


class RecordAccessLog(models.Model):
    """Passive audit trail of who viewed a patient's medical record or
    prescription, and when. Lives in apps.core (not apps.records or
    apps.prescriptions) because it's genuinely cross-cutting: those two apps
    don't otherwise depend on each other, and duplicating a near-identical
    log model in each would fragment one audit trail into two unrelated,
    separately-queried ones. A GenericForeignKey (content_type + object_id)
    is used instead of two nullable FKs so this model doesn't have to import
    from either app and doesn't need a new field the next time a third kind
    of record needs the same treatment.

    Write-only from the rest of the app's perspective — created by the
    detail-view GETs in apps.records.views.MedicalRecordDetailView and
    apps.prescriptions.views.PrescriptionDetailView /
    AppointmentPrescriptionView on successful single-object retrieval only
    (never on list endpoints). No user-facing UI reads this yet; it exists
    for future support/compliance use, hence the read-only admin
    registration in apps.core.admin.
    """

    ACTION_VIEW = 'view'
    ACTION_CHOICES = [
        (ACTION_VIEW, 'View'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    accessed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='record_access_logs',
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    content_object = GenericForeignKey('content_type', 'object_id')
    action = models.CharField(max_length=10, choices=ACTION_CHOICES, default=ACTION_VIEW)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
        ]

    def __str__(self):
        return f'{self.accessed_by} {self.action} {self.content_type}:{self.object_id} at {self.created_at}'


def log_record_access(user, obj, action=RecordAccessLog.ACTION_VIEW):
    """Best-effort audit write — failures here must never break the request
    that triggered them (same try/except-and-log-only convention as the
    notification dispatches throughout this codebase, e.g.
    apps.doctors.services.notify_verification_reset)."""
    try:
        RecordAccessLog.objects.create(
            accessed_by=user,
            content_type=ContentType.objects.get_for_model(obj),
            object_id=obj.pk,
            action=action,
        )
    except Exception:
        logger.exception(
            'Failed to write RecordAccessLog for %s:%s', obj.__class__.__name__, obj.pk
        )
