from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone
from rest_framework import serializers

from apps.doctors.models import Workplace
from .models import Appointment, CANCELLATION_WINDOW_HOURS, Review

User = get_user_model()


class DoctorBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    specialization = serializers.SerializerMethodField()
    specialization_display = serializers.SerializerMethodField()

    def get_specialization(self, obj):
        try:
            return obj.doctor_profile.specialization
        except Exception:
            return ''

    def get_specialization_display(self, obj):
        try:
            return obj.doctor_profile.get_specialization_display()
        except Exception:
            return ''


class PatientBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class WorkplaceBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()


class AppointmentSerializer(serializers.ModelSerializer):
    doctor = DoctorBriefSerializer(read_only=True)
    patient = PatientBriefSerializer(read_only=True)
    workplace = WorkplaceBriefSerializer(read_only=True)
    # Server-computed so the client doesn't hardcode the cancellation rule.
    can_cancel = serializers.SerializerMethodField()
    can_reschedule = serializers.SerializerMethodField()

    class Meta:
        model = Appointment
        fields = [
            'id', 'doctor', 'patient', 'workplace',
            'starts_at', 'ends_at', 'status', 'reason', 'notes', 'created_at',
            'can_cancel', 'can_reschedule',
        ]

    def _beyond_window(self, obj):
        try:
            hours = obj.doctor.doctor_profile.cancellation_window_hours
        except Exception:
            hours = CANCELLATION_WINDOW_HOURS
        return obj.starts_at > timezone.now() + timedelta(hours=hours)

    def get_can_cancel(self, obj):
        return (
            obj.status in (Appointment.STATUS_PENDING, Appointment.STATUS_CONFIRMED)
            and self._beyond_window(obj)
        )

    def get_can_reschedule(self, obj):
        # The doctor explicitly asked for a new time → always reschedulable.
        if obj.status == Appointment.STATUS_REQUIRES_RESCHEDULING:
            return True
        return (
            obj.status in (Appointment.STATUS_PENDING, Appointment.STATUS_CONFIRMED)
            and self._beyond_window(obj)
        )


class BookingSerializer(serializers.Serializer):
    doctor_id = serializers.UUIDField()
    workplace_id = serializers.UUIDField()
    starts_at = serializers.DateTimeField()
    reason = serializers.CharField(allow_blank=True, default='')

    def validate_starts_at(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError('Appointment must be in the future.')
        return value

    def validate(self, attrs):
        try:
            doctor = User.objects.select_related('doctor_profile').get(
                pk=attrs['doctor_id'], role='doctor'
            )
        except User.DoesNotExist:
            raise serializers.ValidationError({'doctor_id': 'Doctor not found.'})

        try:
            workplace = Workplace.objects.get(pk=attrs['workplace_id'], doctor=doctor)
        except Workplace.DoesNotExist:
            raise serializers.ValidationError({'workplace_id': 'Workplace not found for this doctor.'})

        try:
            slot_duration = doctor.doctor_profile.slot_duration_min
        except Exception:
            slot_duration = 30

        ends_at = attrs['starts_at'] + timedelta(minutes=slot_duration)

        overlap = Appointment.objects.filter(
            doctor=doctor,
            starts_at__lt=ends_at,
            ends_at__gt=attrs['starts_at'],
        ).exclude(status__in=[Appointment.STATUS_CANCELLED, Appointment.STATUS_DECLINED])

        if overlap.exists():
            raise serializers.ValidationError({'starts_at': 'This slot is no longer available.'})

        attrs['_doctor'] = doctor
        attrs['_workplace'] = workplace
        attrs['_ends_at'] = ends_at
        return attrs

    def create(self, validated_data):
        return Appointment.objects.create(
            doctor=validated_data['_doctor'],
            patient=self.context['request'].user,
            workplace=validated_data['_workplace'],
            starts_at=validated_data['starts_at'],
            ends_at=validated_data['_ends_at'],
            reason=validated_data.get('reason', ''),
        )


class AppointmentStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[
        Appointment.STATUS_CONFIRMED,
        Appointment.STATUS_DECLINED,
        Appointment.STATUS_COMPLETED,
        Appointment.STATUS_REQUIRES_RESCHEDULING,
        Appointment.STATUS_NO_SHOW,
    ])


class DoctorNotesSerializer(serializers.Serializer):
    notes = serializers.CharField(allow_blank=True)


class RescheduleSerializer(serializers.Serializer):
    starts_at = serializers.DateTimeField()

    def validate_starts_at(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError('New appointment time must be in the future.')
        return value


class ReviewSerializer(serializers.ModelSerializer):
    patient_name = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = ['id', 'appointment', 'rating', 'comment', 'patient_name', 'created_at']
        read_only_fields = ['id', 'appointment', 'patient_name', 'created_at']

    def get_patient_name(self, obj):
        return f'{obj.patient.first_name} {obj.patient.last_name}'.strip() or obj.patient.email


class ReviewCreateSerializer(serializers.Serializer):
    rating = serializers.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = serializers.CharField(allow_blank=True, default='')

    def validate(self, attrs):
        appointment = self.context['appointment']
        if appointment.status != Appointment.STATUS_COMPLETED:
            raise serializers.ValidationError('Reviews can only be left for completed appointments.')
        if appointment.patient != self.context['request'].user:
            raise serializers.ValidationError('You can only review your own appointments.')
        if hasattr(appointment, 'review'):
            raise serializers.ValidationError('You have already reviewed this appointment.')
        return attrs

    def create(self, validated_data):
        appointment = self.context['appointment']
        return Review.objects.create(
            appointment=appointment,
            doctor=appointment.doctor,
            patient=appointment.patient,
            rating=validated_data['rating'],
            comment=validated_data.get('comment', ''),
        )


class DoctorPublicSerializer(serializers.ModelSerializer):
    specialization = serializers.SerializerMethodField()
    specialization_display = serializers.SerializerMethodField()
    slot_duration_min = serializers.SerializerMethodField()
    primary_workplace = serializers.SerializerMethodField()
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    consultation_fee = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'first_name', 'last_name',
            'specialization', 'specialization_display',
            'slot_duration_min', 'consultation_fee', 'primary_workplace',
            'average_rating', 'review_count', 'avatar_url',
        ]

    def get_specialization(self, obj):
        try:
            return obj.doctor_profile.specialization
        except Exception:
            return ''

    def get_specialization_display(self, obj):
        try:
            return obj.doctor_profile.get_specialization_display()
        except Exception:
            return ''

    def get_slot_duration_min(self, obj):
        try:
            return obj.doctor_profile.slot_duration_min
        except Exception:
            return 30

    def get_primary_workplace(self, obj):
        wp = obj.workplaces.filter(is_primary=True).first()
        if not wp:
            wp = obj.workplaces.first()
        if not wp:
            return None
        return {'id': str(wp.id), 'name': wp.name, 'city': wp.city, 'address': wp.address}

    def get_average_rating(self, obj):
        if hasattr(obj, 'avg_rating'):
            result = obj.avg_rating
            return round(result, 1) if result is not None else None
        from django.db.models import Avg
        result = obj.doctor_reviews.aggregate(avg=Avg('rating'))['avg']
        return round(result, 1) if result is not None else None

    def get_review_count(self, obj):
        if hasattr(obj, 'total_reviews'):
            return obj.total_reviews
        return obj.doctor_reviews.count()

    def get_consultation_fee(self, obj):
        try:
            fee = obj.doctor_profile.consultation_fee
            return str(fee) if fee is not None else None
        except Exception:
            return None

    def get_avatar_url(self, obj):
        if not obj.avatar:
            return None
        url = obj.avatar.url
        request = self.context.get('request')
        if request and not url.startswith(('http://', 'https://')):
            url = request.build_absolute_uri(url)
        return url


class DoctorDetailSerializer(DoctorPublicSerializer):
    bio = serializers.SerializerMethodField()
    workplaces = serializers.SerializerMethodField()

    class Meta(DoctorPublicSerializer.Meta):
        fields = DoctorPublicSerializer.Meta.fields + ['bio', 'workplaces']

    def get_bio(self, obj):
        try:
            return obj.doctor_profile.bio
        except Exception:
            return ''

    def get_workplaces(self, obj):
        from apps.doctors.serializers import WorkplaceSerializer
        return WorkplaceSerializer(
            obj.workplaces.prefetch_related('working_hours'), many=True
        ).data
