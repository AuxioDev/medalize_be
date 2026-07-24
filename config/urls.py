from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.core.views import health_check

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/auth/', include('apps.users.urls')),
    path('api/', include('apps.core.urls')),
    path('api/', include('apps.appointments.urls')),
    path('api/', include('apps.medications.urls')),
    path('api/', include('apps.prescriptions.urls')),
    path('api/doctor/', include('apps.doctors.urls')),
    path('api/notifications/', include('apps.notifications.urls')),
    path('api/assistant/', include('apps.assistant.urls')),
    path('api/health/', health_check, name='health-check'),
]

if settings.DEBUG:
    from django.conf.urls.static import static
    from django.views.generic import RedirectView
    from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

    urlpatterns += [
        path('', RedirectView.as_view(url='/api/docs/', permanent=False)),
        path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
        path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    ]
    # Dev-only: serves uploaded avatars/diplomas from local disk (MEDIA_ROOT).
    # Production never hits this branch — nginx serves /media/ directly from
    # the shared volume (see docker-compose.yml + nginx/default.conf), or
    # Cloudinary serves it from its CDN when USE_CLOUDINARY is configured.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
