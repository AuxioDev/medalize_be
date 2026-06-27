from django.urls import path

from . import views

urlpatterns = [
    path('fcm/', views.FCMTokenView.as_view(), name='fcm-token'),
    path('', views.NotificationListView.as_view(), name='notification-list'),
    path('<uuid:pk>/read/', views.NotificationReadView.as_view(), name='notification-read'),
    path('read-all/', views.NotificationReadAllView.as_view(), name='notification-read-all'),
]
