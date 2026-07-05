from rest_framework import serializers

from .models import Conversation, Message

_PREVIEW_LENGTH = 120


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ['id', 'role', 'content', 'suggested_doctors', 'flagged', 'created_at']


class ConversationListSerializer(serializers.ModelSerializer):
    last_message_preview = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ['id', 'created_at', 'updated_at', 'last_message_preview']

    def get_last_message_preview(self, obj):
        last = obj.messages.order_by('-created_at').first()
        if last is None:
            return None
        return last.content[:_PREVIEW_LENGTH]


class ConversationDetailSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)

    class Meta:
        model = Conversation
        fields = ['id', 'created_at', 'updated_at', 'messages']
