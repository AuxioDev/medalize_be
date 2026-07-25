from django.urls import path

from . import views

urlpatterns = [
    path('conversations/', views.ConversationListCreateView.as_view(), name='assistant-conversation-list'),
    path('conversations/<uuid:pk>/', views.ConversationDetailView.as_view(), name='assistant-conversation-detail'),
    path('conversations/<uuid:pk>/messages/', views.ConversationMessageView.as_view(), name='assistant-conversation-messages'),
    path('conversations/<uuid:pk>/summary/', views.ConversationSummaryView.as_view(), name='assistant-conversation-summary'),
    path('messages/<uuid:pk>/flag/', views.MessageFlagView.as_view(), name='assistant-message-flag'),
]
