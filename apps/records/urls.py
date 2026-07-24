from django.urls import path

from . import views

urlpatterns = [
    path('records/', views.MedicalRecordListCreateView.as_view(), name='record-list-create'),
    path('records/<uuid:pk>/', views.MedicalRecordDetailView.as_view(), name='record-detail'),
]
