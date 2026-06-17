import re
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import DoctorProfile, PatientProfile
from .tokens import MedalizeRefreshToken

User = get_user_model()

_PASSWORD_RE = re.compile(r'^(?=.*[A-Za-z])(?=.*\d).{8,}$')


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    token_class = MedalizeRefreshToken
    remember_me = serializers.BooleanField(default=False, write_only=True)

    def validate(self, attrs):
        remember_me = attrs.pop('remember_me', False)
        data = super().validate(attrs)

        # Recreate refresh token with correct lifetime based on remember_me
        refresh = MedalizeRefreshToken.for_user(self.user, remember_me=remember_me)
        data['refresh'] = str(refresh)
        data['access'] = str(refresh.access_token)

        data['role'] = self.user.role
        data['user_id'] = str(self.user.id)
        data['email'] = self.user.email

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

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

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
        fields = ['date_of_birth', 'blood_type', 'address']


class DoctorProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = DoctorProfile
        fields = ['specialization', 'bio', 'slot_duration_min']


class MeSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source='id', read_only=True)

    class Meta:
        model = User
        fields = ['user_id', 'email', 'role', 'first_name', 'last_name']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['user_id'] = str(data['user_id'])

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
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, max_length=128)

    def validate_new_password(self, value):
        if not _PASSWORD_RE.match(value):
            raise serializers.ValidationError(
                'Password must be at least 8 characters and contain at least one letter and one digit.'
            )
        return value

    def validate(self, attrs):
        from django.utils.http import urlsafe_base64_decode
        from django.utils.encoding import force_str

        try:
            uid = force_str(urlsafe_base64_decode(attrs['uid']))
            user = User.objects.get(pk=uid)
        except (User.DoesNotExist, ValueError, TypeError):
            raise serializers.ValidationError({'uid': 'Invalid reset link.'})

        generator = PasswordResetTokenGenerator()
        if not generator.check_token(user, attrs['token']):
            raise serializers.ValidationError({'token': 'Invalid or expired token.'})

        attrs['user'] = user
        return attrs
