from django.urls import path

from . import views

urlpatterns = [
    path(
        'appointments/<uuid:pk>/payment/',
        views.AppointmentPaymentView.as_view(),
        name='appointment-payment',
    ),
    path('payments/webhook/payriff/', views.PayriffWebhookView.as_view(), name='payriff-webhook'),
    path('payments/return/', views.PayriffReturnView.as_view(), name='payriff-return'),
    path('payments/mock-checkout/', views.MockCheckoutView.as_view(), name='mock-checkout'),
]
