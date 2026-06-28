import re
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenObtainSerializer
from rest_framework_simplejwt.settings import api_settings
from django.contrib.auth.models import update_last_login

from .models import DoctorProfile, PatientProfile, PasswordResetOTP
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


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    remember_me = serializers.BooleanField(default=False, write_only=True)

    def validate(self, attrs):
        remember_me = attrs.pop('remember_me', False)
        # Authenticate via grandparent only — skips TokenObtainPairSerializer.get_token,
        # so exactly one outstanding token is created below instead of two.
        data = super(TokenObtainPairSerializer, self).validate(attrs)

        refresh = MedalizeRefreshToken.for_user(self.user, remember_me=remember_me)
        data['refresh'] = str(refresh)
        data['access'] = str(refresh.access_token)

        if api_settings.UPDATE_LAST_LOGIN:
            update_last_login(None, self.user)

        data['role'] = self.user.role
        data['user_id'] = str(self.user.id)
        data['email'] = self.user.email
        data['first_name'] = self.user.first_name
        data['last_name'] = self.user.last_name

        if self.user.role == User.ROLE_DOCTOR:
            try:
                profile = self.user.doctor_profile
                data['onboarding_complete'] = profile.onboarding_complete
                data['is_verified'] = profile.is_verified
            except DoctorProfile.DoesNotExist:
                data['onboarding_complete'] = False
                data['is_verified'] = False
        else:
            data['onboarding_complete'] = True
            data['is_verified'] = None

        return data


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=255)
    password = serializers.CharField(write_only=True, max_length=128)
    password_confirm = serializers.CharField(write_only=True, max_length=128)
    role = serializers.ChoiceField(choices=User.ROLE_CHOICES)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')

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
        password = validated_data.pop('password')
        return User.objects.create_user(password=password, **validated_data)


class PatientProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PatientProfile
        fields = ['date_of_birth', 'blood_type', 'address', 'allergies', 'chronic_conditions', 'medications']


class DoctorProfileSerializer(serializers.ModelSerializer):
    specialization = serializers.ChoiceField(
        choices=DoctorProfile.SPECIALIZATION_CHOICES,
        allow_blank=False,
    )
    specialization_display = serializers.CharField(
        source='get_specialization_display', read_only=True
    )

    class Meta:
        model = DoctorProfile
        fields = [
            'specialization', 'specialization_display', 'license_number', 'bio',
            'slot_duration_min', 'consultation_fee', 'cancellation_window_hours',
        ]


class MeSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source='id', read_only=True)

    class Meta:
        model = User
        fields = ['user_id', 'email', 'role', 'first_name', 'last_name', 'phone']
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
                data['profile'] = DoctorProfileSerializer(profile).data
            except DoctorProfile.DoesNotExist:
                data['is_verified'] = False
                data['onboarding_step'] = 1
                data['onboarding_complete'] = False
                data['profile'] = {}
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


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=255)


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
        if otp is None or not check_password(attrs['code'], code_hash):
            raise serializers.ValidationError({'code': 'Invalid or expired code.'})

        attrs['user'] = user
        attrs['otp'] = otp
        return attrs
