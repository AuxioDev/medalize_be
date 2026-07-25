import datetime

from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.permissions import IsPatient

from .models import DoseLog, Medication
from .serializers import DoseLogSerializer, MedicationCreateSerializer, MedicationSerializer


class MedicationListCreateView(APIView):
    permission_classes = [IsPatient]

    def get(self, request):
        medications = (
            Medication.objects.filter(patient=request.user)
            .select_related('dependent')
            .prefetch_related('schedules')
            .order_by('-is_active', '-created_at')
        )
        return Response(MedicationSerializer(medications, many=True).data)

    def post(self, request):
        serializer = MedicationCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        medication = serializer.save()
        return Response(MedicationSerializer(medication).data, status=status.HTTP_201_CREATED)


class MedicationDetailView(APIView):
    permission_classes = [IsPatient]

    def _get(self, pk, patient):
        try:
            return (
                Medication.objects.select_related('dependent')
                .prefetch_related('schedules')
                .get(pk=pk, patient=patient)
            )
        except Medication.DoesNotExist:
            raise NotFound()

    def get(self, request, pk):
        return Response(MedicationSerializer(self._get(pk, request.user)).data)

    def patch(self, request, pk):
        medication = self._get(pk, request.user)
        serializer = MedicationCreateSerializer(
            medication, data=request.data, partial=True, context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        medication = serializer.save()
        return Response(MedicationSerializer(medication).data)

    def delete(self, request, pk):
        # Soft delete: keeps DoseLog history from being orphaned and losing
        # adherence stats, instead of a hard delete.
        medication = self._get(pk, request.user)
        medication.is_active = False
        medication.save(update_fields=['is_active'])
        return Response(status=status.HTTP_204_NO_CONTENT)


class DoseLogListCreateView(APIView):
    permission_classes = [IsPatient]

    def get(self, request):
        date_str = request.query_params.get('date', '').strip()
        if date_str:
            try:
                target_date = datetime.date.fromisoformat(date_str)
            except ValueError:
                raise ValidationError({'date': 'Enter a valid date in YYYY-MM-DD format.'})
        else:
            target_date = timezone.localdate()

        logs = DoseLog.objects.filter(
            schedule__medication__patient=request.user,
            scheduled_at__date=target_date,
        ).select_related('schedule')
        return Response(DoseLogSerializer(logs, many=True).data)

    def post(self, request):
        serializer = DoseLogSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        schedule = serializer.validated_data['schedule']
        # Defense-in-depth: the PK field only checks the schedule exists at
        # all, not that it belongs to this patient.
        if schedule.medication.patient_id != request.user.id:
            raise NotFound()

        # update_or_create makes a repeat tap (or a retried request after a
        # network failure) idempotent instead of hitting the unique constraint.
        log, _created = DoseLog.objects.update_or_create(
            schedule=schedule,
            scheduled_at=serializer.validated_data['scheduled_at'],
            defaults={'status': serializer.validated_data['status']},
        )
        return Response(DoseLogSerializer(log).data, status=status.HTTP_201_CREATED)
