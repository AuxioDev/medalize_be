from rest_framework import serializers

from apps.core.i18n import city_label, region_label
from apps.users.i18n import specialization_label, viewer_language

from .models import Hospital, HospitalDoctorLink


class HospitalRegistrySerializer(serializers.ModelSerializer):
    """Read-only shape for the doctor-facing picker (GET /api/hospitals/)
    and anywhere else a hospital needs to be displayed by id — including
    nested inside DoctorHospitalLinkSerializer below."""

    city_display = serializers.SerializerMethodField()
    region_display = serializers.SerializerMethodField()

    class Meta:
        model = Hospital
        fields = [
            'id', 'name', 'address', 'city', 'city_display', 'region',
            'region_display', 'latitude', 'longitude', 'status',
        ]
        read_only_fields = fields

    def get_city_display(self, obj):
        return city_label(obj.city, viewer_language(self.context))

    def get_region_display(self, obj):
        return region_label(obj.region, viewer_language(self.context)) if obj.region else ''


class HospitalCreateSerializer(serializers.ModelSerializer):
    """POST /api/hospitals/ — a doctor's "add your variant" when their
    hospital isn't in the registry. Always lands in STATUS_PENDING_REVIEW
    (Hospital.save's field default) regardless of who submits it: the new
    entry is immediately visible to every other doctor in the picker (see
    Hospital.VISIBLE_STATUSES) — admin review happens after the fact, not
    before. Duplicate detection (apps.hospitals.matching.find_duplicates)
    happens at the view level, before this serializer ever saves a row —
    see HospitalListCreateView.post."""

    class Meta:
        model = Hospital
        fields = ['id', 'name', 'city', 'address', 'latitude', 'longitude']
        read_only_fields = ['id']

    def validate(self, attrs):
        has_lat = attrs.get('latitude') is not None
        has_lng = attrs.get('longitude') is not None
        if has_lat != has_lng:
            raise serializers.ValidationError(
                {'latitude': 'latitude and longitude must be provided together.'}
            )
        return attrs


class HospitalProfileSerializer(serializers.ModelSerializer):
    """GET/PATCH /api/hospital/profile/ — the hospital's own editable
    account details. Deliberately excludes claim_status (only an admin
    action or the registration flow can change it) but not `status`
    (registry-review status is admin-only too, listed read-only)."""

    city_display = serializers.SerializerMethodField()
    region_display = serializers.SerializerMethodField()

    class Meta:
        model = Hospital
        fields = [
            'id', 'name', 'address', 'city', 'city_display', 'region', 'region_display',
            'latitude', 'longitude', 'phone', 'website', 'logo', 'status', 'claim_status',
        ]
        read_only_fields = ['id', 'region', 'status', 'claim_status']

    def get_city_display(self, obj):
        return city_label(obj.city, viewer_language(self.context))

    def get_region_display(self, obj):
        return region_label(obj.region, viewer_language(self.context)) if obj.region else ''


class DoctorBriefSerializer(serializers.Serializer):
    """Minimal doctor identity for hospital-facing lists — invite search,
    the confirmed-doctors dashboard list, and the appointments feed.

    Deliberately excludes email and phone: a hospital account must not be
    able to scrape verified doctors' contact details through the invite
    search endpoint (one hospital signup would otherwise buy the whole
    verified-doctor directory's contact info). Also excludes clinical
    fields (bio, license number) — a hospital administrator isn't a
    treating clinician. Takes a User instance with `doctor_profile`
    (select_related/prefetched by the caller)."""

    id = serializers.UUIDField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    specialization = serializers.SerializerMethodField()
    specialization_display = serializers.SerializerMethodField()

    def get_specialization(self, obj):
        profile = getattr(obj, 'doctor_profile', None)
        return profile.specialization if profile else ''

    def get_specialization_display(self, obj):
        profile = getattr(obj, 'doctor_profile', None)
        if not profile:
            return ''
        return specialization_label(profile.specialization, viewer_language(self.context))


class HospitalLinkSerializer(serializers.ModelSerializer):
    """Hospital-side view of an affiliation: shows the doctor (the hospital
    viewing its own dashboard already knows which hospital it is)."""

    doctor = DoctorBriefSerializer(read_only=True)

    class Meta:
        model = HospitalDoctorLink
        fields = ['id', 'doctor', 'status', 'requested_by', 'created_at', 'updated_at', 'decided_at']
        read_only_fields = fields


class HospitalBriefForDoctorSerializer(serializers.ModelSerializer):
    """Doctor-side view of a hospital, nested inside
    DoctorHospitalLinkSerializer below — identity and location only.
    Deliberately excludes `status`: that field is the registry's
    admin-review state (pending_review/confirmed/merged/rejected), an
    internal curation workflow a doctor is not the audience for. Showing it
    next to a doctor's own invitation or confirmed affiliation would read as
    if something were wrong with the hospital. Contrast with
    HospitalRegistrySerializer, which deliberately keeps `status` because
    the picker (GET /api/hospitals/) uses it to flag a not-yet-vetted entry
    *before* the doctor commits to it — a different moment with a different
    audience."""

    city_display = serializers.SerializerMethodField()
    region_display = serializers.SerializerMethodField()

    class Meta:
        model = Hospital
        fields = [
            'id', 'name', 'address', 'city', 'city_display', 'region',
            'region_display', 'latitude', 'longitude',
        ]
        read_only_fields = fields

    def get_city_display(self, obj):
        return city_label(obj.city, viewer_language(self.context))

    def get_region_display(self, obj):
        return region_label(obj.region, viewer_language(self.context)) if obj.region else ''


class DoctorHospitalLinkSerializer(serializers.ModelSerializer):
    """Doctor-side view of an affiliation: shows the hospital."""

    hospital = HospitalBriefForDoctorSerializer(read_only=True)

    class Meta:
        model = HospitalDoctorLink
        fields = ['id', 'hospital', 'status', 'requested_by', 'created_at', 'updated_at', 'decided_at']
        read_only_fields = fields


class HospitalWorkplaceBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)


class HospitalAppointmentSerializer(serializers.Serializer):
    """The hospital dashboard's appointments feed — deliberately NOT a
    subclass of apps.appointments.serializers.AppointmentSerializer. That
    serializer nests full patient/dependent identity and the doctor's
    free-text `reason`/`notes` (clinical content). A hospital administrator
    is not a treating clinician; exposing that would be special-category
    health data under the same law apps.users.models.User.
    privacy_consent_accepted_at exists for. Subclassing risks a future field
    added to the parent's Meta.fields silently leaking here — a fully
    independent field list can't inherit a leak."""

    id = serializers.UUIDField(read_only=True)
    doctor = DoctorBriefSerializer(read_only=True)
    workplace = HospitalWorkplaceBriefSerializer(read_only=True)
    starts_at = serializers.DateTimeField(read_only=True)
    ends_at = serializers.DateTimeField(read_only=True)
    status = serializers.CharField(read_only=True)
