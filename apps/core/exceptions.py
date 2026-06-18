from rest_framework import status
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler
from rest_framework_simplejwt.exceptions import InvalidToken


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if isinstance(exc, Throttled):
        retry_after = int(exc.wait) if exc.wait else 60
        return Response(
            {'code': 'rate_limit_exceeded', 'retry_after_seconds': retry_after},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    if isinstance(exc, InvalidToken):
        # simplejwt puts the specific error reason in exc.detail['messages']
        # e.g. [{'token_class': ..., 'message': 'Token is expired'}]
        combined = ''
        if hasattr(exc, 'detail') and isinstance(exc.detail, dict):
            combined = str(exc.detail).lower()
            for msg_item in exc.detail.get('messages', []):
                combined += str(msg_item.get('message', '')).lower()

        if 'expired' in combined:
            return Response(
                {'code': 'token_expired', 'message': 'Token has expired.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if 'blacklisted' in combined:
            return Response(
                {'code': 'token_blacklisted', 'message': 'Token has been blacklisted.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return Response(
            {'code': 'token_invalid', 'message': 'Token is invalid.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if isinstance(exc, AuthenticationFailed):
        detail = str(exc.detail).lower()
        if 'no active account' in detail or 'invalid' in detail or 'incorrect' in detail:
            return Response(
                {'code': 'invalid_credentials', 'message': 'Invalid email or password.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return Response(
            {'code': 'token_invalid', 'message': str(exc.detail)},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if isinstance(exc, NotAuthenticated):
        return Response(
            {'code': 'not_authenticated', 'message': 'Authentication credentials were not provided.'},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if isinstance(exc, PermissionDenied):
        if isinstance(exc.detail, dict) and 'code' in exc.detail:
            return Response(exc.detail, status=status.HTTP_403_FORBIDDEN)
        request = context.get('request')
        role = getattr(getattr(request, 'user', None), 'role', None)
        return Response(
            {'code': 'permission_denied', 'role': role},
            status=status.HTTP_403_FORBIDDEN,
        )

    if isinstance(exc, NotFound):
        return Response(
            {'code': 'not_found', 'message': str(exc.detail)},
            status=status.HTTP_404_NOT_FOUND,
        )

    if isinstance(exc, ValidationError):
        return Response(
            {'code': 'validation_error', 'errors': exc.detail},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return response
