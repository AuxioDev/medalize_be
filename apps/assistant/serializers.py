from rest_framework import serializers

from apps.users.i18n import specialization_label

from .models import Conversation, Message, ResponseTemplate

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


class ResponseTemplateOptionSerializer(serializers.ModelSerializer):
    """Read-only quick-reply option for the template bank. ``label`` is the
    template's own first trigger phrase (capitalized) in the requesting
    patient's language, passed in via serializer context — reusing a trigger
    guarantees tapping the button sends text that matches its own template."""
    specialization_display = serializers.SerializerMethodField()
    label = serializers.SerializerMethodField()

    class Meta:
        model = ResponseTemplate
        fields = ['id', 'specialization', 'specialization_display', 'label']

    def get_specialization_display(self, obj):
        if not obj.specialization:
            return ''
        return specialization_label(obj.specialization, self.context['lang'])

    def get_label(self, obj):
        lang = self.context['lang']
        triggers = obj.triggers.get(lang) or obj.triggers.get('en') or []
        if not triggers:
            return ''
        text = triggers[0]
        return text[:1].upper() + text[1:]
