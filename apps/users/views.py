import secrets
from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .models import PasswordResetOTP, PatientProfile
from .serializers import (
    CustomTokenObtainPairSerializer,
    MeSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PatientProfileSerializer,
    RegisterSerializer,
)
from .throttles import LoginRateThrottle, PasswordResetRateThrottle, RegisterRateThrottle

User = get_user_model()

_OTP_LIFETIME = timedelta(minutes=10)


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [RegisterRateThrottle]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {
                'user_id': str(user.id),
                'email': user.email,
                'role': user.role,
                'message': 'Registration successful',
            },
            status=status.HTTP_201_CREATED,
        )


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    throttle_classes = [LoginRateThrottle]


class CustomTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            from rest_framework_simplejwt.tokens import AccessToken
            try:
                decoded = AccessToken(response.data['access'])
                # role was encoded as a claim at token creation — no DB query needed
                response.data['role'] = decoded.get('role', '')
                response.data['user_id'] = str(decoded['user_id'])
            except Exception:
                pass
        return response


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response(
                {'code': 'token_invalid', 'message': 'Refresh token is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError:
            return Response(
                {'code': 'token_invalid', 'message': 'Token is invalid or already blacklisted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = MeSerializer(request.user, context={'request': request})
        return Response(serializer.data)

    def patch(self, request):
        serializer = MeSerializer(request.user, data=request.data, partial=True, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(MeSerializer(request.user, context={'request': request}).data)


class PatientProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile, _ = PatientProfile.objects.get_or_create(user=request.user)
        return Response(PatientProfileSerializer(profile).data)

    def patch(self, request):
        profile, _ = PatientProfile.objects.get_or_create(user=request.user)
        serializer = PatientProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(PatientProfileSerializer(profile).data)


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data['new_password'])
        request.user.save()

        # Blacklist the provided refresh token so all existing sessions are revoked
        refresh_token = request.data.get('refresh')
        if refresh_token:
            try:
                RefreshToken(refresh_token).blacklist()
            except TokenError:
                pass

        return Response({'message': 'Password changed successfully.'})


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetRateThrottle]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        try:
            user = User.objects.get(email=email)
            # Invalidate any previous unused OTPs for this user
            user.password_reset_otps.filter(used=False).update(used=True)

            otp_code = f'{secrets.randbelow(1_000_000):06d}'
            PasswordResetOTP.objects.create(
                user=user,
                code_hash=make_password(otp_code),
                expires_at=timezone.now() + _OTP_LIFETIME,
            )
            send_mail(
                subject='Your Medalize Password Reset Code',
                message=(
                    f'Your password reset code is: {otp_code}\n\n'
                    'This code expires in 10 minutes. '
                    'If you did not request this, you can safely ignore this email.'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )
        except User.DoesNotExist:
            pass

        return Response({'message': 'If that email exists, a reset code has been sent.'})


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetRateThrottle]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data['user']
        otp = serializer.validated_data['otp']
        new_password = serializer.validated_data['new_password']

        with transaction.atomic():
            # Re-fetch with a row lock to prevent two concurrent requests from
            # both consuming the same OTP (TOCTOU race condition).
            try:
                locked_otp = (
                    PasswordResetOTP.objects
                    .select_for_update()
                    .get(pk=otp.pk, used=False, expires_at__gt=timezone.now())
                )
            except PasswordResetOTP.DoesNotExist:
                return Response(
                    {'code': 'validation_error', 'errors': {'code': ['Invalid or expired code.']}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            locked_otp.used = True
            locked_otp.save(update_fields=['used'])
            user.set_password(new_password)
            user.save()

        return Response({'message': 'Password reset successful.'})


class AvatarUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        file = request.FILES.get('avatar')
        if not file:
            raise ValidationError({'avatar': 'No file provided.'})
        if file.size > 5 * 1024 * 1024:
            raise ValidationError({'avatar': 'File size must not exceed 5 MB.'})
        # Validate actual file bytes — content_type is user-controlled and can be spoofed.
        try:
            img = Image.open(BytesIO(file.read()))
            img.verify()
            if img.format not in ('JPEG', 'PNG'):
                raise ValidationError({'avatar': 'Only JPEG or PNG files are allowed.'})
        except ValidationError:
            raise
        except Exception:
            raise ValidationError({'avatar': 'Only JPEG or PNG files are allowed.'})
        finally:
            file.seek(0)

        request.user.avatar = file
        request.user.save(update_fields=['avatar'])

        if request.user.avatar:
            url = request.user.avatar.url
            if not url.startswith(('http://', 'https://')):
                url = request.build_absolute_uri(url)
            avatar_url = url
        else:
            avatar_url = None
        return Response({'avatar_url': avatar_url})
