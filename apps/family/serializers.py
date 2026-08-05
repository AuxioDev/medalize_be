from rest_framework import serializers

from .models import Dependent
from .services import is_adult, issue_consent_notice


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
            'medications', 'contact_email', 'contact_phone',
            'consent_notice_sent_at', 'consent_objected_at',
            'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'managed_by', 'consent_notice_sent_at', 'consent_objected_at',
            'is_active', 'created_at', 'updated_at',
        ]


class DependentCreateSerializer(serializers.ModelSerializer):
    """Used for both POST (create) and PATCH (update).

    `date_of_birth` is required for every new/edited dependent going
    forward — overrides the model field's `null=True`/`blank=True` (which
    stays as-is at the DB level: existing rows created before this
    requirement existed are intentionally left alone rather than forced
    through a disruptive backfill migration). Because DRF skips `required`
    checks for fields simply absent from a partial (`PATCH`) payload, this
    plays out exactly as intended: a `PATCH` that never touches
    `date_of_birth` leaves an existing null value untouched, while any
    `PATCH` that does include it (or any `POST`) must supply a real date —
    `allow_null` is deliberately left at its default `False`, so a request
    can't null an existing DOB back out either.

    A `date_of_birth` that computes to 18+ (see `apps.family.services.
    is_adult`) additionally requires a non-blank `contact_email` — see
    `validate()` — so the adult dependent can be positively notified and
    given a no-login way to object (`apps.family.services.
    issue_consent_notice`, triggered from `create()`/`update()` below).
    """

    date_of_birth = serializers.DateField(required=True)

    class Meta:
        model = Dependent
        fields = [
            'first_name', 'last_name', 'relationship', 'date_of_birth',
            'blood_type', 'allergies', 'chronic_conditions', 'medications',
            'contact_email', 'contact_phone',
        ]

    def validate(self, attrs):
        dob = attrs.get('date_of_birth', getattr(self.instance, 'date_of_birth', None))
        if dob and is_adult(dob):
            contact_email = attrs.get(
                'contact_email', getattr(self.instance, 'contact_email', '') or ''
            )
            if not contact_email:
                raise serializers.ValidationError({
                    'contact_email': (
                        'Required for a family member who is 18 or older — they '
                        'must be notified that they were added and given the '
                        'option to object. A phone number alone is not enough: '
                        'we can only deliver this notice by email.'
                    ),
                })
        return attrs

    def create(self, validated_data):
        dependent = Dependent.objects.create(
            managed_by=self.context['request'].user, **validated_data
        )
        if dependent.is_adult and dependent.contact_email:
            issue_consent_notice(dependent)
        return dependent

    def update(self, instance, validated_data):
        old_email = instance.contact_email
        dependent = super().update(instance, validated_data)
        # Send on first becoming a notice-eligible adult, and again if the
        # contact email itself changes (e.g. the account holder corrects a
        # typo) — never on an unrelated field edit that leaves both alone.
        if dependent.is_adult and dependent.contact_email:
            if not dependent.consent_notice_sent_at or dependent.contact_email != old_email:
                issue_consent_notice(dependent)
        return dependent
