from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    return Response({'status': 'ok'})


urlpatterns = [
    path('', RedirectView.as_view(url='/api/docs/', permanent=False)),
    path('admin/', admin.site.urls),
    path('api/auth/', include('apps.users.urls')),
    path('api/', include('apps.core.urls')),
    path('api/doctor/', include('apps.doctors.urls')),
    path('api/appointments/', include('apps.appointments.urls')),
    path('api/notifications/', include('apps.notifications.urls')),
    path('api/health/', health_check, name='health-check'),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]
