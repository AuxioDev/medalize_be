import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.appointments.models import Appointment
from apps.users.models import User

from .models import Message, Thread
from .serializers import MessageSerializer, ThreadSerializer

logger = logging.getLogger(__name__)

# Local constant — deliberately not imported from apps.assistant, which is an
# unrelated module that happens to have the same limit.
MAX_MESSAGE_LENGTH = 4000


class ThreadListCreateView(APIView):
    """Both patient and doctor are equal participants: IsAuthenticated plus
    explicit per-object participant checks, mirroring
    apps/prescriptions/views.py::AppointmentPrescriptionView rather than a
    single-role permission class."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (
            Thread.objects
            .filter(Q(patient=request.user) | Q(doctor=request.user))
            .select_related('patient', 'doctor', 'doctor__doctor_profile')
        )
        serializer = ThreadSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)

    def post(self, request):
        participant_id = request.data.get('participant_id')
        if not participant_id:
            raise ValidationError({'participant_id': ['This field is required.']})

        try:
            other = User.objects.select_related('doctor_profile').get(pk=participant_id)
        except (User.DoesNotExist, DjangoValidationError, ValueError, TypeError):
            raise ValidationError({'participant_id': ['User not found.']})

        if request.user.role == User.ROLE_PATIENT:
            if other.role != User.ROLE_DOCTOR:
                raise ValidationError({'participant_id': ['participant_id must be a doctor.']})
            patient, doctor = request.user, other
        elif request.user.role == User.ROLE_DOCTOR:
            if other.role != User.ROLE_PATIENT:
                raise ValidationError({'participant_id': ['participant_id must be a patient.']})
            patient, doctor = other, request.user
        else:
            raise PermissionDenied({'code': 'permission_denied'})

        # Contact is only established once a real appointment has been
        # booked between the two, regardless of its status.
        if not Appointment.objects.filter(patient=patient, doctor=doctor).exists():
            raise PermissionDenied({
                'code': 'no_shared_history',
                'detail': 'You need a shared appointment history to message this user.',
            })

        thread, created = Thread.objects.get_or_create(patient=patient, doctor=doctor)
        return Response(
            ThreadSerializer(thread, context={'request': request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ThreadMessageListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_thread(self, request, pk):
        try:
            thread = Thread.objects.select_related('patient', 'doctor').get(pk=pk)
        except (Thread.DoesNotExist, DjangoValidationError, ValueError, TypeError):
            raise NotFound()
        # Not 403 — a bare 404 avoids confirming the thread exists to a user
        # who isn't one of its two participants.
        if request.user not in (thread.patient, thread.doctor):
            raise NotFound()
        return thread

    def get(self, request, pk):
        thread = self._get_thread(request, pk)
        # Mark everything the other participant sent as read before paginating
        # so the page we return already reflects it.
        thread.messages.filter(read_at__isnull=True).exclude(sender=request.user).update(
            read_at=timezone.now()
        )
        qs = thread.messages.select_related('sender').order_by('created_at')
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(qs, request)
        serializer = MessageSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)

    def post(self, request, pk):
        thread = self._get_thread(request, pk)

        body = request.data.get('body')
        if not isinstance(body, str) or not body.strip():
            raise ValidationError({'body': ['This field is required.']})
        body = body.strip()
        if len(body) > MAX_MESSAGE_LENGTH:
            raise ValidationError({
                'body': [f'Ensure this field has no more than {MAX_MESSAGE_LENGTH} characters.'],
            })

        message = Message.objects.create(thread=thread, sender=request.user, body=body)
        # auto_now only fires on Thread.save() — message creation doesn't
        # cascade, same reasoning as apps/assistant/service.py::_touch_conversation.
        thread.save(update_fields=['updated_at'])

        try:
            from apps.notifications.tasks import send_new_message
            send_new_message.delay(str(message.id))
        except Exception:
            logger.exception('Failed to enqueue message notification for message %s', message.id)

        return Response(
            MessageSerializer(message, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class ThreadUnreadCountView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        count = (
            Message.objects
            .filter(Q(thread__patient=request.user) | Q(thread__doctor=request.user))
            .filter(read_at__isnull=True)
            .exclude(sender=request.user)
            .count()
        )
        return Response({'unread_count': count})
