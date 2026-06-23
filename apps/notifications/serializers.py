from rest_framework import serializers

from .models import FCMToken, Notification


class FCMTokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=500)


class NotificationSerializer(serializers.ModelSerializer):
    appointment_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Notification
        fields = [
            'id', 'type', 'title', 'message',
            'is_read', 'sent_at', 'appointment_id',
        ]
