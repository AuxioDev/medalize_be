from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, connection
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.users.models import DoctorProfile


@api_view(['GET'])
@permission_classes([AllowAny])
@throttle_classes([])
def health_check(request):
    checks = {}
    degraded = False

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        checks['db'] = 'ok'
    except (DatabaseError, Exception):
        checks['db'] = 'error'
        degraded = True

    try:
        cache.set('_health', '1', timeout=5)
        val = cache.get('_health')
        checks['redis'] = 'ok' if val == '1' else 'error'
        if val != '1':
            degraded = True
    except Exception:
        checks['redis'] = 'error'
        degraded = True

    return Response(
        {
            'status': 'degraded' if degraded else 'ok',
            'version': settings.SPECTACULAR_SETTINGS.get('VERSION', '1.0.0'),
            'environment': 'development' if settings.DEBUG else 'production',
            'checks': checks,
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE if degraded else status.HTTP_200_OK,
    )


@api_view(['GET'])
@permission_classes([AllowAny])
def specializations_list(request):
    return Response([
        {'value': value, 'label': label}
        for value, label in DoctorProfile.SPECIALIZATION_CHOICES
    ])
