import re

from rest_framework import serializers

from .models import DoseLog, Medication, MedicationSchedule

_TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')


class MedicationScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = MedicationSchedule
        fields = [
            'id', 'times', 'days_of_week', 'start_date', 'end_date',
            'timezone', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_times(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('times must be a list of "HH:MM" strings.')
        for item in value:
            if not isinstance(item, str) or not _TIME_RE.match(item):
                raise serializers.ValidationError(
                    f'Invalid time "{item}" — expected 24h format "HH:MM".'
                )
        return value

    def validate_days_of_week(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('days_of_week must be a list of integers 0-6.')
        for item in value:
            if not isinstance(item, int) or isinstance(item, bool) or item < 0 or item > 6:
                raise serializers.ValidationError(
                    f'Invalid weekday "{item}" — expected an integer 0-6 (Mon-Sun).'
                )
        return value


class MedicationSerializer(serializers.ModelSerializer):
    schedules = MedicationScheduleSerializer(many=True, read_only=True)

    class Meta:
        model = Medication
        fields = [
            'id', 'name', 'dosage', 'form', 'notes', 'source', 'is_active',
            'schedules', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'source', 'is_active', 'created_at', 'updated_at']


class MedicationCreateSerializer(serializers.ModelSerializer):
    """Used for both POST (create) and PATCH (update) — a writable-nested
    ``schedules`` list. On update, providing ``schedules`` replaces the full
    set for that medication (delete + recreate), matching how the mobile
    add/edit screen re-submits the whole schedule builder at once."""

    schedules = MedicationScheduleSerializer(many=True, required=False)

    class Meta:
        model = Medication
        fields = ['name', 'dosage', 'form', 'notes', 'schedules']

    def create(self, validated_data):
        schedules_data = validated_data.pop('schedules', [])
        medication = Medication.objects.create(
            patient=self.context['request'].user, **validated_data
        )
        for schedule_data in schedules_data:
            MedicationSchedule.objects.create(medication=medication, **schedule_data)
        return medication

    def update(self, instance, validated_data):
        schedules_data = validated_data.pop('schedules', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if schedules_data is not None:
            instance.schedules.all().delete()
            for schedule_data in schedules_data:
                MedicationSchedule.objects.create(medication=instance, **schedule_data)
        return instance


class DoseLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = DoseLog
        fields = ['id', 'schedule', 'scheduled_at', 'status', 'created_at']
        read_only_fields = ['id', 'created_at']
        # DRF auto-generates a UniqueTogetherValidator from the model's
        # UniqueConstraint(schedule, scheduled_at), which would reject a
        # repeat POST for the same dose before the view's update_or_create
        # ever runs. That constraint exists to keep the *table* free of
        # accidental duplicate rows, not to make a repeat tap/retry an
        # error — the view is deliberately idempotent instead.
        validators = []
