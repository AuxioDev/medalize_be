from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.core.views import health_check

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('apps.users.urls')),
    path('api/', include('apps.core.urls')),
    path('api/doctor/', include('apps.doctors.urls')),
    path('api/appointments/', include('apps.appointments.urls')),
    path('api/notifications/', include('apps.notifications.urls')),
    path('api/health/', health_check, name='health-check'),
]

if settings.DEBUG:
    from django.views.generic import RedirectView
    from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

    urlpatterns += [
        path('', RedirectView.as_view(url='/api/docs/', permanent=False)),
        path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
        path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    ]
