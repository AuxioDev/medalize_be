from rest_framework import serializers

from .models import MedicalRecord


class MedicalRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = MedicalRecord
        fields = [
            'id', 'record_type', 'title', 'file', 'record_date', 'notes',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class MedicalRecordCreateSerializer(serializers.ModelSerializer):
    """Metadata fields only — file bytes are validated and attached directly
    in the view (MultiPartParser + manual byte-level checks don't fit
    cleanly into a standard serializers.FileField), by the same
    DiplomaUploadView-style block."""

    class Meta:
        model = MedicalRecord
        fields = ['record_type', 'title', 'record_date', 'notes']
