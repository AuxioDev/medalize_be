from django.urls import path

from . import views

urlpatterns = [
    path('doctors/specializations/', views.specializations_list, name='specializations-list'),
]
