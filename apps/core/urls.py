from django.urls import path

from . import views

urlpatterns = [
    path('doctors/specializations/', views.specializations_list, name='specializations-list'),
    path('locations/', views.locations_list, name='locations-list'),
    path('geocode/reverse/', views.ReverseGeocodeView.as_view(), name='geocode-reverse'),
]
