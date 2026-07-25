from rest_framework import serializers

from apps.appointments.serializers import DoctorBriefSerializer, PatientBriefSerializer

from .models import Message, Thread


class MessageSerializer(serializers.ModelSerializer):
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ['id', 'thread', 'sender', 'body', 'read_at', 'created_at', 'is_mine']

    def get_is_mine(self, obj):
        request = self.context.get('request')
        return bool(request and request.user.is_authenticated and obj.sender_id == request.user.id)


class ThreadSerializer(serializers.ModelSerializer):
    patient = PatientBriefSerializer(read_only=True)
    doctor = DoctorBriefSerializer(read_only=True)
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = Thread
        fields = ['id', 'patient', 'doctor', 'updated_at', 'last_message', 'unread_count']

    def get_last_message(self, obj):
        last = obj.messages.order_by('-created_at').first()
        if last is None:
            return None
        return MessageSerializer(last, context=self.context).data

    def get_unread_count(self, obj):
        request = self.context.get('request')
        if request is None or not request.user.is_authenticated:
            return 0
        return obj.messages.filter(read_at__isnull=True).exclude(sender=request.user).count()
