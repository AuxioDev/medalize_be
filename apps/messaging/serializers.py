from rest_framework import serializers

from apps.appointments.serializers import DoctorBriefSerializer, PatientBriefSerializer

from .models import Block, Message, Report, Thread


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
        # ThreadListCreateView.get() annotates this via a batch query to avoid
        # N+1; fall back to a per-object query for e.g. the single freshly
        # created/fetched Thread returned by ThreadListCreateView.post().
        if hasattr(obj, 'prefetched_last_message'):
            last = obj.prefetched_last_message
        else:
            last = obj.messages.order_by('-created_at').first()
        if last is None:
            return None
        return MessageSerializer(last, context=self.context).data

    def get_unread_count(self, obj):
        if hasattr(obj, 'unread_count_ann'):
            return obj.unread_count_ann
        request = self.context.get('request')
        if request is None or not request.user.is_authenticated:
            return 0
        return obj.messages.filter(read_at__isnull=True).exclude(sender=request.user).count()


class BlockSerializer(serializers.ModelSerializer):
    class Meta:
        model = Block
        fields = ['id', 'blocker', 'blocked', 'reason', 'created_at']
        read_only_fields = ['id', 'blocker', 'blocked', 'created_at']


class ReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = Report
        fields = ['id', 'thread', 'message', 'reporter', 'reason', 'details', 'created_at']
        read_only_fields = ['id', 'thread', 'message', 'reporter', 'created_at']
