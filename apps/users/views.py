import logging
import secrets
from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)
from PIL import Image

from apps.core.uploads import randomize_upload_filename
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from .permissions import IsPatient
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.notifications.i18n import recipient_language, render_template

from .models import EmailChangeRequest, PasswordResetOTP, PatientProfile, SocialAccount, UserDevice
from .serializers import (
    AccountDeactivateSerializer,
    CustomTokenObtainPairSerializer,
    EmailChangeConfirmSerializer,
    EmailChangeRequestSerializer,
    MeSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PatientProfileSerializer,
    RegisterSerializer,
    SocialLoginSerializer,
    UserDeviceSerializer,
    build_login_payload,
)
from .social import SocialTokenError, verify_id_token
from .throttles import (
    EmailChangeRateThrottle,
    LoginRateThrottle,
    PasswordResetRateThrottle,
    RegisterRateThrottle,
    SocialLoginRateThrottle,
)

User = get_user_model()

_OTP_LIFETIME = timedelta(minutes=10)


def _revoke_all_sessions(user, keep_jti=None):
    """Blacklist every outstanding refresh token for a user.

    Called on password change/reset so that a credential change terminates all
    other sessions (OWASP ASVS V3.3). Access tokens are short-lived (15 min) and
    expire on their own; blacklisting refresh tokens stops them being renewed.
    ``keep_jti`` spares one refresh token (the calling device's session).
    """
    from rest_framework_simplejwt.token_blacklist.models import (
        BlacklistedToken,
        OutstandingToken,
    )

    tokens = OutstandingToken.objects.filter(user=user)
    if keep_jti:
        tokens = tokens.exclude(jti=keep_jti)
    for token in tokens:
        BlacklistedToken.objects.get_or_create(token=token)


def _blacklist_jti(user, jti):
    from rest_framework_simplejwt.token_blacklist.models import (
        BlacklistedToken,
        OutstandingToken,
    )

    if not jti:
        return
    for token in OutstandingToken.objects.filter(user=user, jti=jti):
        BlacklistedToken.objects.get_or_create(token=token)


def _detect_refresh_reuse(raw_refresh_token):
    """Reusing a refresh token that was already rotated away means it was
    leaked/stolen — rotation alone only rejects this one request, leaving any
    newer token an attacker already derived from it valid. Revoke every
    outstanding session for the user so that chain is cut too.

    Matches against UserDevice.previous_jti rather than just checking
    blacklist status: a token can be blacklisted for reasons that are
    perfectly legitimate and not reuse at all (e.g. explicit single-device
    revocation), and that looks identical to real reuse from blacklist state
    alone. previous_jti is only ever set by our own rotation bookkeeping
    (_upsert_device), so a match here is an unambiguous signal.

    Decodes with verify=False purely to read the `jti` claim without a full
    signature/expiry check — the match against our own DB is what actually
    confirms reuse, not anything from the unverified decode itself.
    """
    if not raw_refresh_token:
        return
    try:
        jti = RefreshToken(raw_refresh_token, verify=False).payload.get('jti')
    except TokenError:
        return
    if not jti:
        return
    device = UserDevice.objects.filter(previous_jti=jti).select_related('user').first()
    if device is None:
        return
    logger.warning('Refresh token reuse detected for user %s — revoking all sessions', device.user_id)
    _revoke_all_sessions(device.user)


def _request_device_id(request):
    return str(request.headers.get('X-Device-Id') or request.data.get('device_id') or '').strip()


def _upsert_device(user, refresh_token, data):
    """Track the device a refresh token was issued to.

    Upserts by (user, device_id) so each device holds exactly one live jti,
    and emails the account owner the first time a new device appears.
    """
    device_id = str(data.get('device_id') or '').strip()[:255]
    if not device_id:
        return
    new_jti = str(refresh_token['jti'])
    defaults = {'jti': new_jti, 'last_seen_at': timezone.now()}
    device_name = str(data.get('device_name') or '').strip()[:255]
    platform = str(data.get('platform') or '').strip()
    if device_name:
        defaults['device_name'] = device_name
    if platform in (UserDevice.PLATFORM_IOS, UserDevice.PLATFORM_ANDROID):
        defaults['platform'] = platform

    existing_jti = (
        UserDevice.objects.filter(user=user, device_id=device_id)
        .values_list('jti', flat=True)
        .first()
    )
    if existing_jti and existing_jti != new_jti:
        # Rotated, not just re-seen — record what it rotated away from so
        # _detect_refresh_reuse can recognize a replay of it later.
        defaults['previous_jti'] = existing_jti

    device, created = UserDevice.objects.update_or_create(
        user=user, device_id=device_id, defaults=defaults
    )
    if created:
        try:
            tpl = render_template(
                'new_device_login', recipient_language(user),
                device_name=device.device_name or 'Unknown device',
                platform=device.get_platform_display() or 'Unknown',
                time=timezone.now().strftime('%d.%m.%Y %H:%M'),
            )
            from apps.notifications.tasks import send_transactional_email
            send_transactional_email.delay(tpl['subject'], tpl['body'], user.email)
        except Exception:
            logger.exception('Failed to enqueue new device alert email for user %s', user.pk)


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

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            from rest_framework_simplejwt.exceptions import InvalidToken

            raise InvalidToken(e.args[0])
        _upsert_device(serializer.user, serializer.refresh_token, request.data)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class CustomTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        _detect_refresh_reuse(request.data.get('refresh'))
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
            # Rotation issued a new refresh token — re-point the device record
            # at its jti so per-device revocation always hits the live token.
            try:
                if request.data.get('device_id') and response.data.get('refresh'):
                    new_refresh = RefreshToken(response.data['refresh'])
                    user = User.objects.get(pk=new_refresh['user_id'])
                    _upsert_device(user, new_refresh, request.data)
            except Exception:
                logger.exception('Failed to update device record on token refresh')
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


class SocialLoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [SocialLoginRateThrottle]

    def post(self, request, provider):
        if provider not in dict(SocialAccount.PROVIDER_CHOICES):
            return Response(
                {'code': 'not_found', 'message': 'Unsupported social provider.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = SocialLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            claims = verify_id_token(provider, serializer.validated_data['id_token'])
        except SocialTokenError as exc:
            return Response(
                {'code': 'social_token_invalid', 'message': str(exc)},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        with transaction.atomic():
            account = (
                SocialAccount.objects
                .select_related('user')
                .filter(provider=provider, provider_uid=claims['provider_uid'])
                .first()
            )
            if account is not None:
                user = account.user
            else:
                email = claims['email']
                if not email:
                    return Response(
                        {
                            'code': 'social_email_missing',
                            'message': 'The provider did not share an email address for this account.',
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                user = User.objects.filter(email=email).first()
                if user is not None and not claims['email_verified']:
                    # Auto-linking on an unverified email would let anyone who
                    # claims this address at the provider take over the account.
                    return Response(
                        {
                            'code': 'social_email_unverified',
                            'message': (
                                'An account with this email already exists, but the provider '
                                'has not verified the email address. Please sign in with your password.'
                            ),
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )
                if user is None:
                    user = User.objects.create_user(
                        email=email,
                        password=None,
                        role=serializer.validated_data['role'],
                        first_name=claims['first_name'],
                        last_name=claims['last_name'],
                    )
                SocialAccount.objects.create(
                    user=user,
                    provider=provider,
                    provider_uid=claims['provider_uid'],
                    email=email,
                )

        if not user.is_active:
            return Response(
                {'code': 'invalid_credentials', 'message': 'Invalid email or password.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        data, refresh = build_login_payload(
            user, remember_me=serializer.validated_data['remember_me']
        )
        _upsert_device(user, refresh, serializer.validated_data)
        return Response(data)


class DeviceListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserDeviceSerializer(
            request.user.devices.all(),
            many=True,
            context={'current_device_id': _request_device_id(request)},
        )
        return Response(serializer.data)


class DeviceDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        try:
            device = request.user.devices.get(pk=pk)
        except UserDevice.DoesNotExist:
            return Response(
                {'code': 'not_found', 'message': 'Device not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        _blacklist_jti(request.user, device.jti)
        device.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DeviceRevokeAllView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        current_device_id = _request_device_id(request)
        keep_jti = None
        if current_device_id:
            current = request.user.devices.filter(device_id=current_device_id).first()
            if current is not None:
                keep_jti = current.jti
        _revoke_all_sessions(request.user, keep_jti=keep_jti)
        devices = request.user.devices.all()
        if current_device_id:
            devices = devices.exclude(device_id=current_device_id)
        devices.delete()
        return Response({'message': 'All other sessions have been revoked.'})


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
    permission_classes = [IsPatient]

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

        # Revoke every outstanding session, not just the token in this request,
        # so a password change fully logs out other devices.
        _revoke_all_sessions(request.user)

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

            # Cooldown: skip creation if an OTP already exists that was issued
            # less than 60 s ago (expires_at > now + 9 min ⟺ created_at > now - 1 min).
            if not user.password_reset_otps.filter(
                used=False,
                expires_at__gt=timezone.now() + timedelta(minutes=9),
            ).exists():
                # Invalidate any previous unused OTPs for this user
                user.password_reset_otps.filter(used=False).update(used=True)

                otp_code = f'{secrets.randbelow(1_000_000):06d}'
                PasswordResetOTP.objects.create(
                    user=user,
                    code_hash=make_password(otp_code),
                    expires_at=timezone.now() + _OTP_LIFETIME,
                )
                try:
                    tpl = render_template(
                        'password_reset_code', recipient_language(user), code=otp_code
                    )
                    from apps.notifications.tasks import send_transactional_email
                    send_transactional_email.delay(tpl['subject'], tpl['body'], user.email)
                except Exception:
                    logger.exception('Failed to enqueue password reset email')
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
            # A reset is the account-recovery path — terminate any sessions an
            # attacker may already hold.
            _revoke_all_sessions(user)

        return Response({'message': 'Password reset successful.'})


class AccountDeactivateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AccountDeactivateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        request.user.is_active = False
        request.user.save(update_fields=['is_active'])
        # Deactivation terminates everything, including the current session —
        # no keep_jti. Reactivation is manual (Django admin).
        _revoke_all_sessions(request.user)
        return Response({'message': 'Account deactivated.'})


class EmailChangeRequestView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [EmailChangeRateThrottle]

    def post(self, request):
        serializer = EmailChangeRequestSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        new_email = serializer.validated_data['new_email']

        # Cooldown: skip creation if a code already exists that was issued
        # less than 60 s ago (expires_at > now + 9 min ⟺ created_at > now - 1 min).
        if not request.user.email_change_requests.filter(
            used=False,
            expires_at__gt=timezone.now() + timedelta(minutes=9),
        ).exists():
            # Invalidate any previous unused codes for this user
            request.user.email_change_requests.filter(used=False).update(used=True)

            code = f'{secrets.randbelow(1_000_000):06d}'
            EmailChangeRequest.objects.create(
                user=request.user,
                new_email=new_email,
                code_hash=make_password(code),
                expires_at=timezone.now() + _OTP_LIFETIME,
            )
            try:
                # The code goes to the new address — receiving it proves
                # ownership of that address. Same user, so their current
                # language preference applies.
                tpl = render_template(
                    'email_change_code', recipient_language(request.user), code=code
                )
                from apps.notifications.tasks import send_transactional_email
                send_transactional_email.delay(tpl['subject'], tpl['body'], new_email)
            except Exception:
                logger.exception('Failed to enqueue email change code')

        return Response({'message': 'A confirmation code has been sent to the new email address.'})


class EmailChangeConfirmView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [EmailChangeRateThrottle]

    def post(self, request):
        serializer = EmailChangeConfirmSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        change_request = serializer.validated_data['change_request']

        old_email = request.user.email
        with transaction.atomic():
            # Re-fetch with a row lock to prevent two concurrent requests from
            # both consuming the same code (TOCTOU race condition).
            try:
                locked = (
                    EmailChangeRequest.objects
                    .select_for_update()
                    .get(pk=change_request.pk, used=False, expires_at__gt=timezone.now())
                )
            except EmailChangeRequest.DoesNotExist:
                return Response(
                    {'code': 'validation_error', 'errors': {'code': ['Invalid or expired code.']}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            locked.used = True
            locked.save(update_fields=['used'])
            # The address may have been claimed by another account between
            # request and confirm — re-check before hitting the unique constraint.
            if User.objects.exclude(pk=request.user.pk).filter(email=locked.new_email).exists():
                return Response(
                    {
                        'code': 'validation_error',
                        'errors': {'new_email': ['A user with this email already exists.']},
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            request.user.email = locked.new_email
            request.user.save(update_fields=['email'])
            # An email change is a credential change — terminate all sessions;
            # the user signs in again with the new address.
            _revoke_all_sessions(request.user)

        try:
            # Alert goes to the old address, but it is the same account owner —
            # their language preference is unchanged by the email change.
            tpl = render_template(
                'email_changed', recipient_language(request.user), new_email=locked.new_email
            )
            from apps.notifications.tasks import send_transactional_email
            send_transactional_email.delay(tpl['subject'], tpl['body'], old_email)
        except Exception:
            logger.exception('Failed to enqueue email change notification for user %s', request.user.pk)

        return Response({'message': 'Email changed successfully.', 'email': request.user.email})


class AvatarUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]
    throttle_scope = 'file_upload'

    def post(self, request):
        file = request.FILES.get('avatar')
        if not file:
            raise ValidationError({'avatar': 'No file provided.'})
        if file.size > 5 * 1024 * 1024:
            raise ValidationError({'avatar': 'File size must not exceed 5 MB.'})
        # Validate actual file bytes — content_type is user-controlled and can be spoofed.
        try:
            img = Image.open(BytesIO(file.read()))
            if img.format not in ('JPEG', 'PNG'):
                raise ValidationError({'avatar': 'Only JPEG or PNG files are allowed.'})
            # Force full pixel decode — verify() only checks the header and
            # would pass specially crafted files that fail on actual decode.
            img.load()
            extension = 'jpg' if img.format == 'JPEG' else 'png'
        except ValidationError:
            raise
        except Exception:
            raise ValidationError({'avatar': 'Only JPEG or PNG files are allowed.'})
        finally:
            file.seek(0)

        # Discard the client-supplied filename/extension now that the real
        # type is known — same fix as diploma uploads.
        randomize_upload_filename(file, extension)

        # Remove the previous avatar so replaced files don't accumulate in storage.
        old_avatar = request.user.avatar
        request.user.avatar = file
        request.user.save(update_fields=['avatar'])
        if old_avatar and old_avatar.name and old_avatar.name != request.user.avatar.name:
            try:
                old_avatar.delete(save=False)
            except Exception:
                logger.warning('Failed to delete replaced avatar %s', old_avatar.name)

        if request.user.avatar:
            url = request.user.avatar.url
            if not url.startswith(('http://', 'https://')):
                url = request.build_absolute_uri(url)
            avatar_url = url
        else:
            avatar_url = None
        return Response({'avatar_url': avatar_url})
