from django.urls import path

from . import views

urlpatterns = [
    path(
        'doctor/subscription/', views.DoctorSubscriptionView.as_view(),
        name='doctor-subscription',
    ),
    path(
        'doctor/subscription/plans/', views.SubscriptionPlanListView.as_view(),
        name='subscription-plans',
    ),
    path(
        'doctor/subscription/checkout/', views.SubscriptionCheckoutView.as_view(),
        name='subscription-checkout',
    ),
]
