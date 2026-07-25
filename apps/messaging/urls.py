from django.urls import path

from . import views

urlpatterns = [
    path('threads/', views.ThreadListCreateView.as_view(), name='messaging-thread-list'),
    path(
        'threads/<uuid:pk>/messages/',
        views.ThreadMessageListCreateView.as_view(),
        name='messaging-thread-messages',
    ),
    path(
        'threads/unread-count/',
        views.ThreadUnreadCountView.as_view(),
        name='messaging-thread-unread-count',
    ),
]
