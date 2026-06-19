from django.urls import path

from . import views

urlpatterns = [
    path('workplaces/', views.WorkplaceListCreateView.as_view(), name='workplace-list-create'),
    path('workplaces/<uuid:pk>/', views.WorkplaceDetailView.as_view(), name='workplace-detail'),
    path('workplaces/<uuid:pk>/set-primary/', views.WorkplaceSetPrimaryView.as_view(), name='workplace-set-primary'),
    path('workplaces/<uuid:pk>/hours/', views.WorkingHoursView.as_view(), name='working-hours'),
    path('workplaces/<uuid:pk>/hours/<int:weekday>/', views.WorkingHoursDayView.as_view(), name='working-hours-day'),
    path('blocked-periods/', views.BlockedPeriodListCreateView.as_view(), name='blocked-period-list-create'),
    path('blocked-periods/<uuid:pk>/', views.BlockedPeriodDetailView.as_view(), name='blocked-period-detail'),
]
