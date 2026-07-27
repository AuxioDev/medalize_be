import logging

from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications.i18n import recipient_language
from apps.users.permissions import IsPatient

from .i18n import assistant_message
from .models import Conversation, Message, ResponseTemplate
from .serializers import (
    ConversationDetailSerializer,
    ConversationListSerializer,
    MessageSerializer,
    ResponseTemplateOptionSerializer,
)
from .service import handle_user_message

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000


def _assistant_enabled():
    # Same optional-external-service pattern as Firebase/Cloudinary: empty
    # env var ⇒ feature off, endpoints answer 503 instead of crashing.
    # Encryption is the only requirement now — chat is local template
    # matching, no external AI API involved anywhere in this app.
    return bool(getattr(settings, 'ASSISTANT_ENCRYPTION_KEY', ''))


def _unavailable_response(user):
    lang = recipient_language(user)
    return Response(
        {'detail': assistant_message('assistant_unavailable', lang)},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


class AssistantBaseView(APIView):
    permission_classes = [IsPatient]

    @property
    def _feature_enabled(self):
        # Evaluated per request (not at import) so override_settings works and
        # the flag reflects the live environment.
        return _assistant_enabled()

    def _get_own_conversation(self, request, pk):
        try:
            return Conversation.objects.get(pk=pk, patient=request.user)
        except Conversation.DoesNotExist:
            # 404 (not 403) so the existence of other patients' conversations
            # is not revealed.
            raise NotFound()


class ConversationListCreateView(AssistantBaseView):
    def get(self, request):
        if not self._feature_enabled:
            return _unavailable_response(request.user)
        qs = (
            Conversation.objects
            .filter(patient=request.user)
            .prefetch_related('messages')
        )
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            ConversationListSerializer(page, many=True).data
        )

    def post(self, request):
        if not self._feature_enabled:
            return _unavailable_response(request.user)
        conversation = Conversation.objects.create(patient=request.user)
        return Response(
            ConversationDetailSerializer(conversation).data,
            status=status.HTTP_201_CREATED,
        )


class ConversationDetailView(AssistantBaseView):
    def get(self, request, pk):
        if not self._feature_enabled:
            return _unavailable_response(request.user)
        conversation = self._get_own_conversation(request, pk)
        return Response(ConversationDetailSerializer(conversation).data)

    def delete(self, request, pk):
        if not self._feature_enabled:
            return _unavailable_response(request.user)
        conversation = self._get_own_conversation(request, pk)
        conversation.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConversationMessageView(AssistantBaseView):
    throttle_scope = 'assistant_message'

    def post(self, request, pk):
        if not self._feature_enabled:
            return _unavailable_response(request.user)
        conversation = self._get_own_conversation(request, pk)

        content = request.data.get('content')
        if not isinstance(content, str) or not content.strip():
            return Response(
                {'code': 'validation_error',
                 'errors': {'content': ['This field is required.']}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        content = content.strip()
        if len(content) > MAX_MESSAGE_LENGTH:
            return Response(
                {'code': 'validation_error',
                 'errors': {'content': [
                     f'Ensure this field has no more than {MAX_MESSAGE_LENGTH} characters.'
                 ]}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            reply = handle_user_message(conversation, request.user, content)
        except Exception:
            logger.exception(
                'Assistant pipeline failed for conversation %s', conversation.pk
            )
            return _unavailable_response(request.user)

        return Response(MessageSerializer(reply).data, status=status.HTTP_201_CREATED)


class TemplateOptionListView(AssistantBaseView):
    """Read-only quick-reply options for the mobile chat's empty state — the
    whole active ResponseTemplate bank, labeled in the patient's language."""

    def get(self, request):
        if not self._feature_enabled:
            return _unavailable_response(request.user)
        lang = recipient_language(request.user)
        templates = ResponseTemplate.objects.filter(is_active=True).order_by('specialization', 'created_at')
        serializer = ResponseTemplateOptionSerializer(templates, many=True, context={'lang': lang})
        return Response(serializer.data)


class MessageFlagView(AssistantBaseView):
    def post(self, request, pk):
        if not self._feature_enabled:
            return _unavailable_response(request.user)
        try:
            message = Message.objects.get(
                pk=pk,
                role=Message.ROLE_ASSISTANT,
                conversation__patient=request.user,
            )
        except Message.DoesNotExist:
            raise NotFound()

        reason = request.data.get('reason', '')
        message.flagged = True
        if isinstance(reason, str) and reason.strip():
            message.flag_reason = reason.strip()
        message.save(update_fields=['flagged', 'flag_reason'])
        return Response(MessageSerializer(message).data)
