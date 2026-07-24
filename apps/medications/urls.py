from django.urls import path

from . import views

urlpatterns = [
    path('medications/', views.MedicationListCreateView.as_view(), name='medication-list-create'),
    path('medications/dose-logs/', views.DoseLogListCreateView.as_view(), name='medication-dose-log-list-create'),
    path('medications/<uuid:pk>/', views.MedicationDetailView.as_view(), name='medication-detail'),
]
