from rest_framework import serializers

from apps.family.serializers import DependentBriefSerializer
from apps.family.services import resolve_dependent

from .models import MedicalRecord


class MedicalRecordSerializer(serializers.ModelSerializer):
    dependent = DependentBriefSerializer(read_only=True)

    class Meta:
        model = MedicalRecord
        fields = [
            'id', 'record_type', 'title', 'file', 'record_date', 'notes',
            'dependent', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class MedicalRecordCreateSerializer(serializers.ModelSerializer):
    """Metadata fields only — file bytes are validated and attached directly
    in the view (MultiPartParser + manual byte-level checks don't fit
    cleanly into a standard serializers.FileField), by the same
    DiplomaUploadView-style block.

    ``dependent_id`` is optional and write-only, submitted as an ordinary
    multipart form field (not JSON) — this whole serializer is fed from
    ``request.data`` on a multipart request. Resolved to the actual
    Dependent instance (or None) by validate_dependent_id."""

    dependent_id = serializers.UUIDField(required=False, allow_null=True)

    class Meta:
        model = MedicalRecord
        fields = ['record_type', 'title', 'record_date', 'notes', 'dependent_id']

    def validate_dependent_id(self, value):
        return resolve_dependent(self.context['request'].user, value)
