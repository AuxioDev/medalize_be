from rest_framework import serializers

from apps.appointments.models import Appointment

from .models import Prescription, PrescriptionItem


class PrescriptionItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrescriptionItem
        fields = ['id', 'drug_name', 'dosage', 'frequency', 'duration', 'instructions']
        read_only_fields = ['id']


class PrescriptionSerializer(serializers.ModelSerializer):
    items = PrescriptionItemSerializer(many=True, read_only=True)
    doctor_name = serializers.SerializerMethodField()
    patient_name = serializers.SerializerMethodField()

    class Meta:
        model = Prescription
        fields = [
            'id', 'appointment', 'doctor', 'patient', 'doctor_name', 'patient_name',
            'notes', 'items', 'issued_at',
        ]
        read_only_fields = fields

    def get_doctor_name(self, obj):
        return f'{obj.doctor.first_name} {obj.doctor.last_name}'.strip() or obj.doctor.email

    def get_patient_name(self, obj):
        return f'{obj.patient.first_name} {obj.patient.last_name}'.strip() or obj.patient.email


class PrescriptionCreateSerializer(serializers.Serializer):
    notes = serializers.CharField(allow_blank=True, default='')
    items = PrescriptionItemSerializer(many=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError('At least one item is required.')
        return value

    def validate(self, attrs):
        appointment = self.context['appointment']
        if appointment.status != Appointment.STATUS_COMPLETED:
            raise serializers.ValidationError(
                'Prescriptions can only be issued for completed appointments.'
            )
        if appointment.doctor != self.context['request'].user:
            raise serializers.ValidationError(
                'You can only issue prescriptions for your own appointments.'
            )
        # hasattr on a OneToOne reverse accessor is unreliable (see
        # ReviewCreateSerializer.validate) — explicit try/except instead.
        try:
            appointment.prescription  # type: ignore[attr-defined]
            raise serializers.ValidationError('A prescription already exists for this appointment.')
        except Prescription.DoesNotExist:
            pass
        return attrs

    def create(self, validated_data):
        appointment = self.context['appointment']
        items_data = validated_data.pop('items')
        prescription = Prescription.objects.create(
            appointment=appointment,
            doctor=appointment.doctor,
            patient=appointment.patient,
            notes=validated_data.get('notes', ''),
        )
        PrescriptionItem.objects.bulk_create([
            PrescriptionItem(prescription=prescription, **item) for item in items_data
        ])
        return prescription
