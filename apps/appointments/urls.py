from django.urls import path

from . import views

urlpatterns = [
    # Public doctor endpoints
    path('doctors/', views.DoctorListView.as_view(), name='doctor-list'),
    path('doctors/<uuid:pk>/', views.DoctorDetailView.as_view(), name='doctor-detail'),
    path('doctors/<uuid:pk>/slots/', views.SlotListView.as_view(), name='doctor-slots'),
    # Patient appointment endpoints
    path('appointments/', views.PatientAppointmentListCreateView.as_view(), name='appointment-list-create'),
    path('appointments/<uuid:pk>/', views.PatientAppointmentDetailView.as_view(), name='appointment-detail'),
    path('appointments/<uuid:pk>/reschedule/', views.PatientAppointmentRescheduleView.as_view(), name='appointment-reschedule'),
    # Review endpoints
    path('appointments/<uuid:pk>/review/', views.AppointmentReviewView.as_view(), name='appointment-review'),
    path('doctors/<uuid:pk>/reviews/', views.DoctorReviewListView.as_view(), name='doctor-reviews'),
    # Doctor appointment endpoints
    path('doctor/appointments/', views.DoctorAppointmentListView.as_view(), name='doctor-appointment-list'),
    path('doctor/appointments/<uuid:pk>/', views.DoctorAppointmentDetailView.as_view(), name='doctor-appointment-detail'),
    path('doctor/appointments/<uuid:pk>/status/', views.DoctorAppointmentStatusView.as_view(), name='doctor-appointment-status'),
    path('doctor/appointments/<uuid:pk>/notes/', views.DoctorAppointmentNotesView.as_view(), name='doctor-appointment-notes'),
]
