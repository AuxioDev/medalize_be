from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, connection
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.geocoding import reverse_geocode
from apps.core.i18n import locations_payload
from apps.users.i18n import viewer_language
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


@api_view(['GET'])
@permission_classes([AllowAny])
def locations_list(request):
    """Every Azerbaijan city/district, grouped by economic region and
    localized to the viewer's language. See apps.core.i18n.locations_payload."""
    lang = viewer_language({'request': request})
    return Response(locations_payload(lang))


class ReverseGeocodeView(APIView):
    """Proxies a single coordinate to Nominatim so the mobile app never talks
    to a third party directly — see apps.core.geocoding for why."""
    permission_classes = [IsAuthenticated]
    throttle_scope = 'reverse_geocode'

    def get(self, request):
        try:
            lat = float(request.query_params.get('lat'))
            lng = float(request.query_params.get('lng'))
        except (TypeError, ValueError):
            return Response(
                {'lat': 'lat and lng query parameters are required and must be numbers.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        lang = viewer_language({'request': request})
        return Response({'address': reverse_geocode(lat, lng, lang)})
