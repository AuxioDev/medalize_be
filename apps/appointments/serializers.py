import datetime
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers

from apps.doctors.models import BlockedPeriod, WorkingHours, Workplace
from apps.family.serializers import DependentBriefSerializer
from apps.family.services import resolve_dependent
from apps.users.i18n import specialization_label, viewer_language
from .models import Appointment, CANCELLATION_WINDOW_HOURS, REVIEW_EDIT_WINDOW_DAYS, Review

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
            return specialization_label(
                obj.doctor_profile.specialization, viewer_language(self.context)
            )
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
    """`patient` is always the account owner — the authorized `User` who
    booked, is billed, and receives notifications for this appointment; that
    never changes. `dependent`, when not null, is the actual clinical
    subject of the visit — a managed family member, not the account owner.
    Both are exposed explicitly and unambiguously so a doctor-facing client
    never renders `patient`'s name as "who this visit is for" when a
    `dependent` is set — that would be a patient-safety issue, not just a
    cosmetic one."""

    doctor = DoctorBriefSerializer(read_only=True)
    patient = PatientBriefSerializer(read_only=True)
    dependent = DependentBriefSerializer(read_only=True)
    workplace = WorkplaceBriefSerializer(read_only=True)
    # Server-computed so the client doesn't hardcode the cancellation rule.
    can_cancel = serializers.SerializerMethodField()
    can_reschedule = serializers.SerializerMethodField()
    has_review = serializers.SerializerMethodField()
    review = serializers.SerializerMethodField()
    can_edit_review = serializers.SerializerMethodField()

    class Meta:
        model = Appointment
        fields = [
            'id', 'doctor', 'patient', 'dependent', 'workplace',
            'starts_at', 'ends_at', 'status', 'reason', 'notes', 'created_at',
            'can_cancel', 'can_reschedule', 'has_review', 'review', 'can_edit_review',
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

    def get_has_review(self, obj):
        try:
            return obj.review is not None
        except Exception:
            return False

    def get_review(self, obj):
        # Nested so the client reads its own review together with the
        # appointment instead of needing a separate GET round-trip.
        try:
            return ReviewSerializer(obj.review).data
        except Review.DoesNotExist:
            return None

    def get_can_edit_review(self, obj):
        # Server-computed so the client doesn't hardcode the edit-window rule.
        try:
            review = obj.review
        except Review.DoesNotExist:
            return False
        return timezone.now() - review.created_at <= timedelta(days=REVIEW_EDIT_WINDOW_DAYS)


class BookingSerializer(serializers.Serializer):
    doctor_id = serializers.UUIDField()
    workplace_id = serializers.UUIDField()
    starts_at = serializers.DateTimeField()
    reason = serializers.CharField(allow_blank=True, default='')
    # Optional: books this appointment for one of the patient's managed
    # family members instead of the patient themself. Resolved to the actual
    # Dependent instance by validate_dependent_id, or None if omitted.
    dependent_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_starts_at(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError('Appointment must be in the future.')
        return value

    def validate_dependent_id(self, value):
        return resolve_dependent(self.context['request'].user, value)

    def validate(self, attrs):
        try:
            doctor = User.objects.select_related('doctor_profile').get(
                pk=attrs['doctor_id'], role='doctor', is_active=True, doctor_profile__is_verified=True
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

        starts_at = attrs['starts_at']
        ends_at = starts_at + timedelta(minutes=slot_duration)

        # Validate against the doctor's working hours for this weekday/workplace.
        local_starts = timezone.localtime(starts_at)
        weekday = local_starts.weekday()
        try:
            wh = WorkingHours.objects.get(workplace=workplace, weekday=weekday, is_active=True)
        except WorkingHours.DoesNotExist:
            raise serializers.ValidationError(
                {'starts_at': 'The doctor does not work on this day at this location.'}
            )
        day_start = timezone.make_aware(
            datetime.datetime.combine(local_starts.date(), wh.start_time)
        )
        day_end = timezone.make_aware(
            datetime.datetime.combine(local_starts.date(), wh.end_time)
        )
        if not (day_start <= starts_at and ends_at <= day_end):
            raise serializers.ValidationError(
                {'starts_at': "This slot is outside the doctor's working hours."}
            )

        # Require the start to land on the doctor's slot grid so clients can't
        # book off-grid times (e.g. 09:07) that straddle two published slots.
        offset_minutes = (starts_at - day_start).total_seconds() / 60
        if offset_minutes < 0 or offset_minutes % slot_duration != 0:
            raise serializers.ValidationError(
                {'starts_at': 'Selected time is not a valid appointment slot.'}
            )

        # Validate against blocked periods.
        if BlockedPeriod.objects.filter(
            doctor=doctor,
            starts_at__lt=ends_at,
            ends_at__gt=starts_at,
        ).filter(Q(workplace=workplace) | Q(workplace__isnull=True)).exists():
            raise serializers.ValidationError({'starts_at': 'This slot is not available.'})

        overlap = Appointment.objects.filter(
            doctor=doctor,
            starts_at__lt=ends_at,
            ends_at__gt=starts_at,
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
            dependent=validated_data.get('dependent_id'),
            workplace=validated_data['_workplace'],
            starts_at=validated_data['starts_at'],
            ends_at=validated_data['_ends_at'],
            reason=validated_data.get('reason', ''),
        )


class AppointmentStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[
        Appointment.STATUS_CONFIRMED,
        Appointment.STATUS_DECLINED,
        Appointment.STATUS_CANCELLED,
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
        fields = ['id', 'appointment', 'rating', 'comment', 'patient_name', 'created_at', 'updated_at']
        read_only_fields = ['id', 'appointment', 'patient_name', 'created_at', 'updated_at']

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
        # hasattr on a OneToOne reverse accessor is unreliable because
        # RelatedObjectDoesNotExist may or may not subclass AttributeError
        # depending on the Django version. Use explicit try/except instead.
        try:
            appointment.review  # type: ignore[attr-defined]
            raise serializers.ValidationError('You have already reviewed this appointment.')
        except Review.DoesNotExist:
            pass
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


class ReviewUpdateSerializer(serializers.Serializer):
    rating = serializers.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = serializers.CharField(allow_blank=True, default='')

    def validate(self, attrs):
        review = self.context['review']
        # Redundant with the view's patient-scoped lookup, but kept as a
        # defensive check in line with the rest of the project.
        if review.patient != self.context['request'].user:
            raise serializers.ValidationError('You can only edit your own reviews.')
        if timezone.now() - review.created_at > timedelta(days=REVIEW_EDIT_WINDOW_DAYS):
            raise serializers.ValidationError('The edit window for this review has expired.')
        return attrs


class DoctorPublicSerializer(serializers.ModelSerializer):
    specialization = serializers.SerializerMethodField()
    specialization_display = serializers.SerializerMethodField()
    slot_duration_min = serializers.SerializerMethodField()
    primary_workplace = serializers.SerializerMethodField()
    average_rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    consultation_fee = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()
    next_slot_at = serializers.SerializerMethodField()
    distance_km = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'first_name', 'last_name',
            'specialization', 'specialization_display',
            'slot_duration_min', 'consultation_fee', 'primary_workplace',
            'average_rating', 'review_count', 'avatar_url',
            'next_slot_at', 'distance_km',
        ]

    def get_specialization(self, obj):
        try:
            return obj.doctor_profile.specialization
        except Exception:
            return ''

    def get_specialization_display(self, obj):
        try:
            return specialization_label(
                obj.doctor_profile.specialization, viewer_language(self.context)
            )
        except Exception:
            return ''

    def get_slot_duration_min(self, obj):
        try:
            return obj.doctor_profile.slot_duration_min
        except Exception:
            return 30

    def get_primary_workplace(self, obj):
        # obj.workplaces.all() reuses the prefetch_related('workplaces') cache
        # from the queryset (DoctorListView/FavoriteListCreateView) — filtering
        # via .filter()/.first() instead would issue a fresh query per doctor,
        # defeating the prefetch and reintroducing N+1.
        workplaces = list(obj.workplaces.all())
        if not workplaces:
            return None
        wp = next((w for w in workplaces if w.is_primary), workplaces[0])
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

    def get_next_slot_at(self, obj):
        # Only populated by DoctorListView for ordering=next_slot.
        value = getattr(obj, 'next_slot_at', None)
        return value.isoformat() if value else None

    def get_distance_km(self, obj):
        # Only annotated when lat/lng were sent with the request.
        value = getattr(obj, 'distance_km', None)
        return round(float(value), 1) if value is not None else None


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
