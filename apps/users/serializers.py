import re
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db.models import F
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenObtainSerializer
from rest_framework_simplejwt.settings import api_settings
from django.contrib.auth.models import update_last_login

from apps.core.i18n import city_label, city_region, region_label
from apps.subscriptions.entitlements import subscription_summary
from apps.subscriptions.models import DoctorSubscription

from .i18n import specialization_label, viewer_language
from .models import DoctorProfile, EmailChangeRequest, PatientProfile, PasswordResetOTP, UserDevice
from .tokens import MedalizeRefreshToken

User = get_user_model()

_PASSWORD_RE = re.compile(r'^(?=.*[A-Za-z])(?=.*\d).{8,}$')
_PHONE_RE = re.compile(r'^\+?[0-9()\-\s]{7,20}$')
# Precomputed dummy hash for constant-time OTP verification (prevents timing attacks)
_DUMMY_OTP_HASH = make_password('000000')


def _validate_phone_format(value):
    if value and not _PHONE_RE.match(value):
        raise serializers.ValidationError('Enter a valid phone number (7–20 digits, optional + prefix).')
    return value


def build_login_payload(user, remember_me=False):
    """Issue a JWT pair for a user and build the standard login response body.

    Shared by password login and social login so both flows return an
    identical payload. Returns (data, refresh_token).
    """
    refresh = MedalizeRefreshToken.for_user(user, remember_me=remember_me)
    data = {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }

    if api_settings.UPDATE_LAST_LOGIN:
        update_last_login(None, user)

    data['role'] = user.role
    data['user_id'] = str(user.id)
    data['email'] = user.email
    data['first_name'] = user.first_name
    data['last_name'] = user.last_name

    if user.role == User.ROLE_DOCTOR:
        try:
            profile = user.doctor_profile
            data['onboarding_complete'] = profile.onboarding_complete
            data['is_verified'] = profile.is_verified
        except DoctorProfile.DoesNotExist:
            data['onboarding_complete'] = False
            data['is_verified'] = False
        try:
            subscription = user.subscription
        except DoctorSubscription.DoesNotExist:
            subscription = None
        data['subscription'] = subscription_summary(subscription)
    else:
        data['onboarding_complete'] = True
        data['is_verified'] = None

    return data, refresh


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    remember_me = serializers.BooleanField(default=False, write_only=True)

    def validate(self, attrs):
        remember_me = attrs.pop('remember_me', False)
        # Authenticate via grandparent only — skips TokenObtainPairSerializer.get_token,
        # so exactly one outstanding token is created below instead of two.
        super(TokenObtainPairSerializer, self).validate(attrs)
        data, refresh = build_login_payload(self.user, remember_me=remember_me)
        self.refresh_token = refresh
        return data


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=255)
    password = serializers.CharField(write_only=True, max_length=128)
    password_confirm = serializers.CharField(write_only=True, max_length=128)
    role = serializers.ChoiceField(choices=User.ROLE_CHOICES)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')
    # Must be explicitly true — this is the written consent Azerbaijan's Law
    # on Personal Data requires for special-category data (health data is
    # one), not a generic "I agree to Terms" checkbox. The mobile app's
    # registration screen disables submission until this is checked; the
    # backend re-validates it rather than trusting client-side UI state.
    privacy_consent = serializers.BooleanField(write_only=True)

    def validate_privacy_consent(self, value):
        if not value:
            raise serializers.ValidationError(
                'You must accept the Privacy Policy to create an account.'
            )
        return value

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

    def validate_phone(self, value):
        return _validate_phone_format(value)

    def validate_password(self, value):
        if not _PASSWORD_RE.match(value):
            raise serializers.ValidationError(
                'Password must be at least 8 characters and contain at least one letter and one digit.'
            )
        return value

    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({'password_confirm': 'Passwords do not match.'})
        return attrs

    def create(self, validated_data):
        validated_data.pop('password_confirm')
        validated_data.pop('privacy_consent')
        password = validated_data.pop('password')
        return User.objects.create_user(
            password=password,
            privacy_consent_accepted_at=timezone.now(),
            **validated_data,
        )


class PatientProfileSerializer(serializers.ModelSerializer):
    city_display = serializers.SerializerMethodField()
    region_display = serializers.SerializerMethodField()

    class Meta:
        model = PatientProfile
        fields = [
            'date_of_birth', 'blood_type', 'address', 'city', 'city_display',
            'region', 'region_display', 'allergies', 'chronic_conditions', 'medications',
        ]
        read_only_fields = ['region']

    def get_city_display(self, obj):
        return city_label(obj.city, viewer_language(self.context)) if obj.city else ''

    def get_region_display(self, obj):
        return region_label(obj.region, viewer_language(self.context)) if obj.region else ''

    def update(self, instance, validated_data):
        if 'city' in validated_data:
            validated_data['region'] = city_region(validated_data['city']) or ''
        return super().update(instance, validated_data)


class DoctorProfileSerializer(serializers.ModelSerializer):
    specialization = serializers.ChoiceField(
        choices=DoctorProfile.SPECIALIZATION_CHOICES,
        allow_blank=False,
    )
    specialization_display = serializers.SerializerMethodField()

    class Meta:
        model = DoctorProfile
        fields = [
            'specialization', 'specialization_display', 'license_number', 'bio',
            'slot_duration_min', 'consultation_fee', 'cancellation_window_hours',
        ]

    def get_specialization_display(self, obj):
        return specialization_label(obj.specialization, viewer_language(self.context))


class MeSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source='id', read_only=True)

    class Meta:
        model = User
        fields = ['user_id', 'email', 'role', 'first_name', 'last_name', 'phone', 'language']
        read_only_fields = ['user_id', 'email', 'role']

    def validate_phone(self, value):
        return _validate_phone_format(value)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['user_id'] = str(data['user_id'])
        request = self.context.get('request')
        if instance.avatar:
            url = instance.avatar.url
            if request and not url.startswith(('http://', 'https://')):
                url = request.build_absolute_uri(url)
            data['avatar_url'] = url
        else:
            data['avatar_url'] = None

        if instance.role == User.ROLE_DOCTOR:
            try:
                profile = instance.doctor_profile
                data['is_verified'] = profile.is_verified
                data['onboarding_step'] = profile.onboarding_step
                data['onboarding_complete'] = profile.onboarding_complete
                data['profile'] = DoctorProfileSerializer(profile, context={'request': request}).data
            except DoctorProfile.DoesNotExist:
                data['is_verified'] = False
                data['onboarding_step'] = 1
                data['onboarding_complete'] = False
                data['profile'] = {}
            try:
                subscription = instance.subscription
            except DoctorSubscription.DoesNotExist:
                subscription = None
            data['subscription'] = subscription_summary(subscription)
        else:
            try:
                profile = instance.patient_profile
                data['profile'] = PatientProfileSerializer(profile).data
            except PatientProfile.DoesNotExist:
                data['profile'] = {}

        return data


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True, max_length=128)
    new_password = serializers.CharField(write_only=True, max_length=128)

    def validate_new_password(self, value):
        if not _PASSWORD_RE.match(value):
            raise serializers.ValidationError(
                'Password must be at least 8 characters and contain at least one letter and one digit.'
            )
        return value

    def validate(self, attrs):
        user = self.context['request'].user
        if not user.check_password(attrs['old_password']):
            raise serializers.ValidationError({'old_password': 'Old password is incorrect.'})
        return attrs


class SocialLoginSerializer(serializers.Serializer):
    id_token = serializers.CharField()
    device_id = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    device_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    platform = serializers.ChoiceField(
        choices=UserDevice.PLATFORM_CHOICES, required=False, allow_blank=True, default=''
    )
    role = serializers.ChoiceField(
        choices=User.ROLE_CHOICES, required=False, default=User.ROLE_PATIENT
    )
    remember_me = serializers.BooleanField(default=False)


class UserDeviceSerializer(serializers.ModelSerializer):
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = UserDevice
        fields = ['id', 'device_id', 'device_name', 'platform', 'last_seen_at', 'created_at', 'is_current']

    def get_is_current(self, obj):
        current_device_id = self.context.get('current_device_id', '')
        return bool(current_device_id) and obj.device_id == current_device_id


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=255)


class AccountDeactivateSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True, max_length=128)

    def validate_password(self, value):
        if not self.context['request'].user.check_password(value):
            raise serializers.ValidationError('Password is incorrect.')
        return value


class EmailChangeRequestSerializer(serializers.Serializer):
    new_email = serializers.EmailField(max_length=255)
    password = serializers.CharField(write_only=True, max_length=128)

    def validate_password(self, value):
        if not self.context['request'].user.check_password(value):
            raise serializers.ValidationError('Password is incorrect.')
        return value

    def validate_new_email(self, value):
        user = self.context['request'].user
        if User.objects.exclude(pk=user.pk).filter(email=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value


class EmailChangeConfirmSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=6, min_length=6)

    def validate(self, attrs):
        user = self.context['request'].user
        change_request = (
            EmailChangeRequest.objects
            .filter(user=user, used=False, expires_at__gt=timezone.now())
            .first()
        )
        # Always call check_password (even when no request exists) so response
        # time is constant regardless of whether an active code exists —
        # prevents timing attacks.
        code_hash = change_request.code_hash if change_request is not None else _DUMMY_OTP_HASH
        valid = check_password(attrs['code'], code_hash)

        if change_request is None or not valid:
            if change_request is not None:
                # Count the failed guess and retire the code once the per-account
                # attempt cap is reached, so a leaked/guessable code can't be
                # brute-forced across rotating IPs.
                EmailChangeRequest.objects.filter(pk=change_request.pk).update(attempts=F('attempts') + 1)
                if change_request.attempts + 1 >= EmailChangeRequest.MAX_ATTEMPTS:
                    EmailChangeRequest.objects.filter(pk=change_request.pk).update(used=True)
            raise serializers.ValidationError({'code': 'Invalid or expired code.'})

        attrs['change_request'] = change_request
        return attrs


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=255)
    code = serializers.CharField(max_length=6, min_length=6)
    new_password = serializers.CharField(write_only=True, max_length=128)

    def validate_new_password(self, value):
        if not _PASSWORD_RE.match(value):
            raise serializers.ValidationError(
                'Password must be at least 8 characters and contain at least one letter and one digit.'
            )
        return value

    def validate(self, attrs):
        try:
            user = User.objects.get(email=attrs['email'])
        except User.DoesNotExist:
            # Always hash even on unknown email so response time is constant
            # regardless of whether the address exists — prevents user enumeration.
            check_password(attrs['code'], _DUMMY_OTP_HASH)
            raise serializers.ValidationError({'code': 'Invalid or expired code.'})

        otp = (
            PasswordResetOTP.objects
            .filter(user=user, used=False, expires_at__gt=timezone.now())
            .first()
        )
        # Always call check_password (even when otp is None) so response time
        # is constant regardless of whether an OTP exists — prevents timing attacks.
        code_hash = otp.code_hash if otp is not None else _DUMMY_OTP_HASH
        valid = check_password(attrs['code'], code_hash)

        if otp is None or not valid:
            if otp is not None:
                # Count the failed guess and retire the code once the per-account
                # attempt cap is reached, so a leaked/guessable code can't be
                # brute-forced across rotating IPs.
                PasswordResetOTP.objects.filter(pk=otp.pk).update(attempts=F('attempts') + 1)
                if otp.attempts + 1 >= PasswordResetOTP.MAX_ATTEMPTS:
                    PasswordResetOTP.objects.filter(pk=otp.pk).update(used=True)
            raise serializers.ValidationError({'code': 'Invalid or expired code.'})

        attrs['user'] = user
        attrs['otp'] = otp
        return attrs
