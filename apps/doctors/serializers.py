from rest_framework import serializers

from apps.core.i18n import city_coordinates, city_label, city_region, region_label
from apps.hospitals.models import Hospital
from apps.hospitals.services import sync_link_after_workplace_hospital_removed, sync_workplace_hospital_link
from apps.users.i18n import specialization_label, viewer_language
from apps.users.models import DoctorProfile
from .models import BlockedPeriod, Workplace, WorkingHours

_SLOT_DURATIONS = [15, 20, 30, 45, 60]
_CANCELLATION_WINDOWS = [1, 2, 6, 12, 24]


class DoctorProfileWriteSerializer(serializers.ModelSerializer):
    specialization = serializers.ChoiceField(
        choices=DoctorProfile.SPECIALIZATION_CHOICES, required=False
    )

    class Meta:
        model = DoctorProfile
        fields = [
            'specialization', 'license_number', 'bio', 'slot_duration_min',
            'consultation_fee', 'cancellation_window_hours',
        ]

    def validate_slot_duration_min(self, value):
        if value not in _SLOT_DURATIONS:
            raise serializers.ValidationError(
                'Slot duration must be one of 15, 20, 30, 45 or 60 minutes.'
            )
        return value

    def validate_cancellation_window_hours(self, value):
        if value not in _CANCELLATION_WINDOWS:
            raise serializers.ValidationError(
                'Cancellation window must be one of 1, 2, 6, 12 or 24 hours.'
            )
        return value


class DoctorProfileReadSerializer(serializers.ModelSerializer):
    specialization_display = serializers.SerializerMethodField()
    has_diploma = serializers.SerializerMethodField()

    class Meta:
        model = DoctorProfile
        fields = [
            'specialization', 'specialization_display', 'license_number', 'bio',
            'slot_duration_min', 'consultation_fee', 'cancellation_window_hours',
            'is_verified', 'onboarding_step', 'onboarding_complete', 'has_diploma',
        ]
        read_only_fields = fields

    def get_specialization_display(self, obj):
        return specialization_label(obj.specialization, viewer_language(self.context))

    def get_has_diploma(self, obj):
        return bool(obj.diploma_file)


class WorkingHoursSerializer(serializers.ModelSerializer):
    weekday_display = serializers.CharField(source='get_weekday_display', read_only=True)

    class Meta:
        model = WorkingHours
        fields = ['id', 'weekday', 'weekday_display', 'start_time', 'end_time', 'is_active']
        read_only_fields = ['id', 'weekday', 'weekday_display']


class WorkplaceSerializer(serializers.ModelSerializer):
    working_hours = WorkingHoursSerializer(many=True, read_only=True)
    type_display = serializers.CharField(source='get_type_display', read_only=True)
    city_display = serializers.SerializerMethodField()
    region_display = serializers.SerializerMethodField()
    hospital_name = serializers.SerializerMethodField()
    hospital_link_status = serializers.SerializerMethodField()
    # Not required: a private-practice workplace has no hospital at all.
    # Restricted to registry entries a doctor should ever be offered — a
    # merged/rejected id can't be assigned (see validate_hospital).
    hospital = serializers.PrimaryKeyRelatedField(
        queryset=Hospital.objects.all(), required=False, allow_null=True,
    )
    # Required only when `hospital` isn't set — see validate(). The model
    # field itself has no blank=True (a private practice still needs a
    # name), but when a hospital is picked the name can default to the
    # hospital's own name, so the serializer can't just inherit the model's
    # required=True.
    name = serializers.CharField(max_length=200, required=False, allow_blank=True)

    class Meta:
        model = Workplace
        fields = [
            'id', 'name', 'address', 'city', 'city_display', 'region', 'region_display',
            'type', 'type_display', 'is_primary', 'latitude', 'longitude', 'working_hours',
            'hospital', 'hospital_name', 'hospital_link_status',
        ]
        read_only_fields = ['id', 'is_primary', 'region']
        extra_kwargs = {
            'latitude': {'required': False},
            'longitude': {'required': False},
        }

    def get_city_display(self, obj):
        return city_label(obj.city, viewer_language(self.context))

    def get_region_display(self, obj):
        return region_label(obj.region, viewer_language(self.context)) if obj.region else ''

    def get_hospital_name(self, obj):
        return obj.hospital.name if obj.hospital_id else ''

    def get_hospital_link_status(self, obj):
        """The doctor's own affiliation status with `hospital` (pending
        review by the hospital / confirmed / etc.) — lets the mobile app
        show a "pending confirmation" chip on the workplace form without a
        second request. None when there's no hospital link at all (private
        practice, or the hospital hasn't been contacted about this yet)."""
        if not obj.hospital_id:
            return None
        link = obj.hospital.doctor_links.filter(doctor_id=obj.doctor_id).first()
        return link.status if link else None

    def validate_hospital(self, value):
        if value is not None and value.status not in Hospital.VISIBLE_STATUSES:
            raise serializers.ValidationError('This hospital is no longer available.')
        return value

    def validate(self, attrs):
        has_lat = attrs.get('latitude') is not None
        has_lng = attrs.get('longitude') is not None
        if has_lat != has_lng:
            raise serializers.ValidationError(
                {'latitude': 'latitude and longitude must be provided together.'}
            )

        # `name` is required unless a hospital is (or already was) set —
        # covers create (no instance yet, so `hospital` must be in attrs)
        # and update (falls back to the existing instance's hospital when
        # the client doesn't touch that field in this request).
        hospital = attrs.get('hospital', getattr(self.instance, 'hospital', None) if self.instance else None)
        name = attrs.get('name', getattr(self.instance, 'name', '') if self.instance else '')
        if not name and not hospital:
            raise serializers.ValidationError(
                {'name': 'Name is required when no hospital is selected.'}
            )
        return attrs

    def _apply_city(self, validated_data):
        """`region` is derived from `city` — see apps.core.i18n.city_region.
        Coordinates are the doctor's own map pin when provided (see
        create/update below); only fall back to the city centroid when
        they aren't."""
        city = validated_data.get('city')
        if city:
            validated_data['region'] = city_region(city) or ''
        return validated_data

    def _apply_hospital_name(self, validated_data):
        """Defaults `name` to the hospital's own name when the client picked
        a hospital and left name blank — see validate() for why `name` can
        be blank at all in that case."""
        hospital = validated_data.get('hospital')
        if hospital is not None and not validated_data.get('name'):
            validated_data['name'] = hospital.name
        return validated_data

    def create(self, validated_data):
        has_explicit_coords = (
            'latitude' in validated_data and 'longitude' in validated_data
        )
        validated_data = self._apply_city(validated_data)
        validated_data = self._apply_hospital_name(validated_data)
        workplace = super().create(validated_data)
        if not has_explicit_coords:
            coords = city_coordinates(workplace.city)
            if coords:
                workplace.latitude, workplace.longitude = coords
                workplace.save(update_fields=['latitude', 'longitude'])
        if workplace.hospital_id:
            sync_workplace_hospital_link(workplace.hospital, workplace.doctor)
        return workplace

    def update(self, instance, validated_data):
        has_explicit_coords = (
            'latitude' in validated_data and 'longitude' in validated_data
        )
        city_changed = 'city' in validated_data and validated_data['city'] != instance.city
        hospital_changed = 'hospital' in validated_data and validated_data['hospital'] != instance.hospital
        old_hospital = instance.hospital
        validated_data = self._apply_city(validated_data)
        validated_data = self._apply_hospital_name(validated_data)
        workplace = super().update(instance, validated_data)
        if city_changed and not has_explicit_coords:
            coords = city_coordinates(workplace.city)
            if coords:
                workplace.latitude, workplace.longitude = coords
                workplace.save(update_fields=['latitude', 'longitude'])
        if hospital_changed:
            # hospital_changed already guarantees the new value differs from
            # old_hospital, so no extra comparison is needed here.
            if workplace.hospital_id:
                sync_workplace_hospital_link(workplace.hospital, workplace.doctor)
            if old_hospital is not None:
                sync_link_after_workplace_hospital_removed(old_hospital, workplace.doctor)
        return workplace


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
