from rest_framework import serializers

from .models import BlockedPeriod, Workplace, WorkingHours


class WorkingHoursSerializer(serializers.ModelSerializer):
    weekday_display = serializers.CharField(source='get_weekday_display', read_only=True)

    class Meta:
        model = WorkingHours
        fields = ['id', 'weekday', 'weekday_display', 'start_time', 'end_time', 'is_active']
        read_only_fields = ['id', 'weekday', 'weekday_display']


class WorkplaceSerializer(serializers.ModelSerializer):
    working_hours = WorkingHoursSerializer(many=True, read_only=True)
    type_display = serializers.CharField(source='get_type_display', read_only=True)

    class Meta:
        model = Workplace
        fields = ['id', 'name', 'address', 'city', 'type', 'type_display', 'is_primary', 'working_hours']
        read_only_fields = ['id', 'is_primary']


class WorkingHoursReplaceItemSerializer(serializers.Serializer):
    weekday = serializers.IntegerField(min_value=0, max_value=6)
    start_time = serializers.TimeField(default='09:00:00')
    end_time = serializers.TimeField(default='17:00:00')
    is_active = serializers.BooleanField(default=True)

    def validate(self, attrs):
        if attrs.get('is_active') and attrs['start_time'] >= attrs['end_time']:
            raise serializers.ValidationError(
                {'end_time': 'end_time must be after start_time when the day is active.'}
            )
        return attrs


class WorkingHoursPatchSerializer(serializers.Serializer):
    start_time = serializers.TimeField(required=False)
    end_time = serializers.TimeField(required=False)
    is_active = serializers.BooleanField(required=False)

    def validate(self, attrs):
        start = attrs.get('start_time')
        end = attrs.get('end_time')
        if start and end and start >= end:
            raise serializers.ValidationError(
                {'end_time': 'end_time must be after start_time.'}
            )
        return attrs


class BlockedPeriodSerializer(serializers.ModelSerializer):
    notify_patients = serializers.BooleanField(default=False, write_only=True)

    class Meta:
        model = BlockedPeriod
        fields = ['id', 'workplace', 'starts_at', 'ends_at', 'reason', 'notify_patients']
        read_only_fields = ['id']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        doctor = self.context.get('doctor')
        if doctor:
            self.fields['workplace'].queryset = Workplace.objects.filter(doctor=doctor)

    def validate(self, attrs):
        starts = attrs.get('starts_at')
        ends = attrs.get('ends_at')
        if starts and ends and starts >= ends:
            raise serializers.ValidationError(
                {'ends_at': 'ends_at must be after starts_at.'}
            )
        return attrs
