from django.urls import path

from . import views

urlpatterns = [
    path(
        'appointments/<uuid:pk>/prescription/',
        views.AppointmentPrescriptionView.as_view(),
        name='appointment-prescription',
    ),
    path('prescriptions/', views.PatientPrescriptionListView.as_view(), name='prescription-list'),
    path('prescriptions/<uuid:pk>/', views.PrescriptionDetailView.as_view(), name='prescription-detail'),
    path(
        'prescriptions/<uuid:pk>/apply/',
        views.PrescriptionApplyView.as_view(),
        name='prescription-apply',
    ),
]
