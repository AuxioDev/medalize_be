import logging

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.appointments.models import Appointment
from apps.subscriptions.entitlements import limits_for
from apps.users.models import User

from .models import Block, Message, Report, Thread
from .serializers import BlockSerializer, MessageSerializer, ReportSerializer, ThreadSerializer

logger = logging.getLogger(__name__)

# Local constant — deliberately not imported from apps.assistant, which is an
# unrelated module that happens to have the same limit.
MAX_MESSAGE_LENGTH = 4000


def _messaging_enabled():
    # Message.body reuses the same EncryptedTextField/ASSISTANT_ENCRYPTION_KEY
    # as apps.assistant — an empty key must disable sending, not 500 on every
    # message. See apps/assistant/checks.py::check_assistant_encryption_key
    # (assistant.W001), which already documents this exact risk.
    return bool(getattr(settings, 'ASSISTANT_ENCRYPTION_KEY', ''))


def _blocked_either_way(a, b):
    """True if `a` blocked `b` or `b` blocked `a` — either side may have
    blocked the other, and both directions must be checked before letting
    a message through or a new thread open (see (b) in the Phase 3
    messaging fix)."""
    return Block.objects.filter(
        Q(blocker=a, blocked=b) | Q(blocker=b, blocked=a)
    ).exists()


class ThreadListCreateView(APIView):
    """Both patient and doctor are equal participants: IsAuthenticated plus
    explicit per-object participant checks, mirroring
    apps/prescriptions/views.py::AppointmentPrescriptionView rather than a
    single-role permission class."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Annotate the last-message id and unread count on the queryset itself
        # instead of letting ThreadSerializer query per-object — avoids 2N
        # extra queries per page (see ThreadSerializer.get_last_message /
        # get_unread_count, which prefer these annotations when present).
        last_message_qs = Message.objects.filter(thread=OuterRef('pk')).order_by('-created_at')
        qs = (
            Thread.objects
            .filter(Q(patient=request.user) | Q(doctor=request.user))
            .select_related('patient', 'doctor', 'doctor__doctor_profile')
            .annotate(
                last_message_id=Subquery(last_message_qs.values('id')[:1]),
                unread_count_ann=Count(
                    'messages',
                    filter=Q(messages__read_at__isnull=True) & ~Q(messages__sender=request.user),
                ),
            )
            # Explicit — annotate()'ing an aggregate (Count) forces a GROUP BY
            # that silently drops Thread.Meta's implicit `ordering`, which
            # would otherwise make pagination results non-deterministic
            # across pages (caught by DRF's UnorderedObjectListWarning).
            .order_by('-updated_at')
        )
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(qs, request)

        message_ids = [t.last_message_id for t in page if t.last_message_id]
        messages_by_id = {
            m.id: m for m in Message.objects.filter(id__in=message_ids).select_related('sender')
        }
        for thread in page:
            thread.prefetched_last_message = messages_by_id.get(thread.last_message_id)

        serializer = ThreadSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)

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

        thread = Thread.objects.filter(patient=patient, doctor=doctor).first()
        created = thread is None
        if created:
            # These checks gate only opening a brand-new thread — an already
            # existing thread stays reachable even if e.g. its only
            # appointment is later cancelled, or the doctor's plan has since
            # dropped chat (Başlanğıc, or a lapsed/expired subscription).
            # Contact is only established once a real appointment was booked
            # between the two that didn't end up cancelled/declined — a
            # single never-happened appointment shouldn't permanently unlock
            # messaging (previously this checked .exists() with no status
            # exclusion at all).
            if not Appointment.objects.filter(patient=patient, doctor=doctor).exclude(
                status__in=[Appointment.STATUS_CANCELLED, Appointment.STATUS_DECLINED]
            ).exists():
                raise PermissionDenied({
                    'code': 'no_shared_history',
                    'detail': 'You need a shared appointment history to message this user.',
                })
            if _blocked_either_way(patient, doctor):
                raise PermissionDenied({
                    'code': 'blocked',
                    'detail': 'You cannot start a conversation with this user.',
                })
            if not limits_for(doctor)['chat']:
                raise PermissionDenied({'code': 'chat_unavailable'})
            thread = Thread.objects.create(patient=patient, doctor=doctor)

        return Response(
            ThreadSerializer(thread, context={'request': request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ThreadMessageListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        # Only scope-throttle POST (sending): GET is polled periodically by
        # the mobile chat screen and must stay under the generic `user` rate,
        # not the tighter anti-spam budget meant for message sends.
        if self.request.method == 'POST':
            self.throttle_scope = 'messaging_message'
        return super().get_throttles()

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
        if not _messaging_enabled():
            return Response(
                {'detail': 'Messaging is temporarily unavailable.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        thread = self._get_thread(request, pk)

        other = thread.doctor if request.user.id == thread.patient_id else thread.patient
        if _blocked_either_way(request.user, other):
            # Explicit, not a silent drop — either side may have blocked the
            # other (see (b) in the Phase 3 messaging fix).
            raise PermissionDenied({
                'code': 'blocked',
                'detail': 'You cannot send messages in this conversation.',
            })

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


class ThreadBlockView(APIView):
    """Block/unblock the other participant of a thread — either patient or
    doctor may do this to the other. See (b) in the Phase 3 messaging fix:
    once blocked, the blocked party can't send new messages in this (or any)
    shared thread and can't open a new one with `blocked`."""

    permission_classes = [IsAuthenticated]

    def _get_thread_and_other(self, request, pk):
        try:
            thread = Thread.objects.select_related('patient', 'doctor').get(pk=pk)
        except (Thread.DoesNotExist, DjangoValidationError, ValueError, TypeError):
            raise NotFound()
        # Not 403 — same reasoning as ThreadMessageListCreateView._get_thread.
        if request.user not in (thread.patient, thread.doctor):
            raise NotFound()
        other = thread.doctor if request.user.id == thread.patient_id else thread.patient
        return thread, other

    def post(self, request, pk):
        _thread, other = self._get_thread_and_other(request, pk)
        reason = request.data.get('reason', '')
        reason = reason.strip() if isinstance(reason, str) else ''

        block, created = Block.objects.get_or_create(
            blocker=request.user, blocked=other, defaults={'reason': reason},
        )
        if not created and reason and not block.reason:
            block.reason = reason
            block.save(update_fields=['reason'])

        return Response(
            BlockSerializer(block).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request, pk):
        _thread, other = self._get_thread_and_other(request, pk)
        deleted, _counts = Block.objects.filter(blocker=request.user, blocked=other).delete()
        if not deleted:
            raise NotFound()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ThreadReportView(APIView):
    """Report a thread (the counterpart's behavior in general, not tied to
    one message) for admin follow-up. See MessageReportView below for
    reporting a specific message instead — same shape, mirroring
    apps.assistant.views.MessageFlagView, just a distinct model since a
    thread can be reported more than once for different reasons."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            thread = Thread.objects.select_related('patient', 'doctor').get(pk=pk)
        except (Thread.DoesNotExist, DjangoValidationError, ValueError, TypeError):
            raise NotFound()
        if request.user not in (thread.patient, thread.doctor):
            raise NotFound()

        serializer = ReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report = Report.objects.create(
            thread=thread, reporter=request.user, **serializer.validated_data
        )
        return Response(ReportSerializer(report).data, status=status.HTTP_201_CREATED)


class MessageReportView(APIView):
    """Report one specific message — see ThreadReportView above."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            message = (
                Message.objects
                .select_related('thread', 'thread__patient', 'thread__doctor')
                .get(pk=pk)
            )
        except (Message.DoesNotExist, DjangoValidationError, ValueError, TypeError):
            raise NotFound()
        if request.user not in (message.thread.patient, message.thread.doctor):
            raise NotFound()

        serializer = ReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report = Report.objects.create(
            thread=message.thread, message=message, reporter=request.user,
            **serializer.validated_data,
        )
        return Response(ReportSerializer(report).data, status=status.HTTP_201_CREATED)
