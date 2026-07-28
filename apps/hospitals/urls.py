from django.urls import path

from . import views

urlpatterns = [
    # Registry — shared by the doctor's workplace-hospital picker.
    path('hospitals/', views.HospitalListCreateView.as_view(), name='hospital-list-create'),
    path('hospitals/<uuid:pk>/', views.HospitalDetailView.as_view(), name='hospital-detail'),

    # Hospital's own account.
    path('hospital/profile/', views.HospitalProfileView.as_view(), name='hospital-profile'),
    path('hospital/status/', views.HospitalStatusView.as_view(), name='hospital-status'),

    # Hospital dashboard.
    path(
        'hospital/doctors/links/', views.HospitalDoctorLinkListView.as_view(),
        name='hospital-doctor-links',
    ),
    path(
        'hospital/doctors/links/<uuid:link_id>/approve/',
        views.HospitalDoctorLinkApproveView.as_view(), name='hospital-doctor-link-approve',
    ),
    path(
        'hospital/doctors/links/<uuid:link_id>/reject/',
        views.HospitalDoctorLinkRejectView.as_view(), name='hospital-doctor-link-reject',
    ),
    path(
        'hospital/doctors/links/<uuid:link_id>/remove/',
        views.HospitalDoctorLinkRemoveView.as_view(), name='hospital-doctor-link-remove',
    ),
    path(
        'hospital/doctors/search/', views.HospitalDoctorSearchView.as_view(),
        name='hospital-doctor-search',
    ),
    path(
        'hospital/doctors/invite/', views.HospitalDoctorInviteView.as_view(),
        name='hospital-doctor-invite',
    ),
    path(
        'hospital/doctors/<uuid:doctor_id>/workplaces/',
        views.HospitalDoctorWorkplacesView.as_view(), name='hospital-doctor-workplaces',
    ),
    path(
        'hospital/workplaces/<uuid:workplace_id>/hours/',
        views.HospitalWorkplaceHoursView.as_view(), name='hospital-workplace-hours',
    ),
    path('hospital/appointments/', views.HospitalAppointmentListView.as_view(), name='hospital-appointments'),

    # Doctor-side counterparts (accepting/declining a hospital's invite).
    path(
        'doctor/hospital-links/', views.DoctorHospitalLinkListView.as_view(),
        name='doctor-hospital-links',
    ),
    path(
        'doctor/hospital-links/<uuid:link_id>/accept/',
        views.DoctorHospitalLinkAcceptView.as_view(), name='doctor-hospital-link-accept',
    ),
    path(
        'doctor/hospital-links/<uuid:link_id>/decline/',
        views.DoctorHospitalLinkDeclineView.as_view(), name='doctor-hospital-link-decline',
    ),
]
