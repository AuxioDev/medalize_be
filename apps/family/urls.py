from django.urls import path

from . import views

urlpatterns = [
    path('dependents/', views.DependentListCreateView.as_view(), name='dependent-list-create'),
    path('dependents/<uuid:pk>/', views.DependentDetailView.as_view(), name='dependent-detail'),
]
