from rest_framework import serializers

from .models import Dependent


class DependentBriefSerializer(serializers.Serializer):
    """Short nested representation — same pattern as DoctorBriefSerializer/
    PatientBriefSerializer (apps/appointments/serializers.py), reused
    wherever another app's serializer needs to show "for whom" a record is
    (appointments, medications, records, prescriptions, payments)."""

    id = serializers.UUIDField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    relationship = serializers.CharField()
    date_of_birth = serializers.DateField(allow_null=True)


class DependentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dependent
        fields = [
            'id', 'managed_by', 'first_name', 'last_name', 'relationship',
            'date_of_birth', 'blood_type', 'allergies', 'chronic_conditions',
            'medications', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'managed_by', 'is_active', 'created_at', 'updated_at']


class DependentCreateSerializer(serializers.ModelSerializer):
    """Used for both POST (create) and PATCH (update)."""

    class Meta:
        model = Dependent
        fields = [
            'first_name', 'last_name', 'relationship', 'date_of_birth',
            'blood_type', 'allergies', 'chronic_conditions', 'medications',
        ]

    def create(self, validated_data):
        return Dependent.objects.create(
            managed_by=self.context['request'].user, **validated_data
        )
