from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import FCMToken, Notification, NotificationPreference
from .serializers import (
    FCMTokenSerializer,
    NotificationPreferenceSerializer,
    NotificationSerializer,
)


_FCM_TOKEN_LIMIT = 10


class FCMTokenView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = 'fcm_register'

    def post(self, request):
        serializer = FCMTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data['token']
        # Key on the globally-unique token and (re)assign ownership to the current
        # user. get_or_create(user, token) raises IntegrityError when the same
        # device token is already registered to another account (shared device /
        # account switch) and would keep delivering the previous user's pushes to
        # the new owner.
        _, created = FCMToken.objects.update_or_create(
            token=token, defaults={'user': request.user}
        )

        # FIFO eviction: keep only the _FCM_TOKEN_LIMIT most recent tokens.
        user_tokens = FCMToken.objects.filter(user=request.user).order_by('-created_at')
        excess_ids = list(user_tokens.values_list('id', flat=True)[_FCM_TOKEN_LIMIT:])
        if excess_ids:
            FCMToken.objects.filter(id__in=excess_ids).delete()

        return Response(
            {'message': 'Token registered.'},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request):
        """De-register a device token so pushes stop going to a device that is no
        longer signed in. Called by the client on logout."""
        token = request.data.get('token')
        if not token:
            return Response(
                {'code': 'validation_error', 'errors': {'token': ['This field is required.']}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        FCMToken.objects.filter(user=request.user, token=token).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (
            Notification.objects
            .filter(user=request.user)
            .select_related('appointment')
            .order_by('-sent_at')
        )
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(NotificationSerializer(page, many=True).data)


class NotificationUnreadCountView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        count = Notification.objects.filter(user=request.user, is_read=False).count()
        return Response({'unread_count': count})


class NotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        try:
            notif = Notification.objects.get(pk=pk, user=request.user)
        except Notification.DoesNotExist:
            raise NotFound()
        notif.is_read = True
        notif.save(update_fields=['is_read'])
        return Response(NotificationSerializer(notif).data)

    def delete(self, request, pk):
        try:
            notif = Notification.objects.get(pk=pk, user=request.user)
        except Notification.DoesNotExist:
            raise NotFound()
        notif.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class NotificationReadAllView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        updated = Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({'marked_read': updated})


class NotificationPreferenceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
        return Response(NotificationPreferenceSerializer(prefs).data)

    def patch(self, request):
        prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
        serializer = NotificationPreferenceSerializer(prefs, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
