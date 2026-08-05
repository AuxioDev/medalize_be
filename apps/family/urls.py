from django.urls import path

from . import views

urlpatterns = [
    path('dependents/', views.DependentListCreateView.as_view(), name='dependent-list-create'),
    path('dependents/<uuid:pk>/', views.DependentDetailView.as_view(), name='dependent-detail'),
    # Public, no-login page — see DependentConsentRejectView's docstring.
    path(
        'dependents/<uuid:pk>/consent/reject/',
        views.DependentConsentRejectView.as_view(),
        name='dependent-consent-reject',
    ),
]
