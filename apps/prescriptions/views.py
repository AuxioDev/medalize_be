import logging

from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.appointments.models import Appointment
from apps.core.models import log_record_access
from apps.medications.models import Medication
from apps.medications.serializers import MedicationSerializer
from apps.users.permissions import IsDoctorVerified, IsPatient

from .models import Prescription
from .serializers import PrescriptionCreateSerializer, PrescriptionSerializer

logger = logging.getLogger(__name__)


class AppointmentPrescriptionView(APIView):
    """GET is open to the appointment's doctor or patient (IsAuthenticated +
    manual participant check); POST — issuing a prescription — is doctor-only
    and requires verification, so permissions vary per method."""

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsDoctorVerified()]
        return [IsAuthenticated()]

    def get(self, request, pk):
        try:
            appointment = Appointment.objects.select_related('doctor', 'patient', 'dependent').get(pk=pk)
        except Appointment.DoesNotExist:
            raise NotFound()
        # Not 403 — a bare 404 avoids confirming the appointment exists to a
        # user who isn't a participant in it.
        if request.user not in (appointment.doctor, appointment.patient):
            raise NotFound()
        try:
            prescription = appointment.prescription
        except Prescription.DoesNotExist:
            raise NotFound()
        # Passive audit trail on successful single-object retrieval — see
        # apps.core.models.RecordAccessLog.
        log_record_access(request.user, prescription)
        return Response(PrescriptionSerializer(prescription).data)

    def post(self, request, pk):
        try:
            appointment = Appointment.objects.select_related('doctor', 'patient', 'dependent').get(
                pk=pk, doctor=request.user,
            )
        except Appointment.DoesNotExist:
            raise NotFound()

        serializer = PrescriptionCreateSerializer(
            data=request.data, context={'appointment': appointment, 'request': request},
        )
        serializer.is_valid(raise_exception=True)
        prescription = serializer.save()

        try:
            from apps.notifications.tasks import send_prescription_issued
            send_prescription_issued.delay(str(prescription.id))
        except Exception:
            logger.exception(
                'Failed to enqueue prescription notification for prescription %s', prescription.id
            )

        return Response(PrescriptionSerializer(prescription).data, status=status.HTTP_201_CREATED)


class PatientPrescriptionListView(APIView):
    permission_classes = [IsPatient]

    def get(self, request):
        qs = (
            Prescription.objects.filter(patient=request.user)
            .select_related('doctor', 'patient', 'dependent')
            .prefetch_related('items')
            .order_by('-issued_at')
        )
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(PrescriptionSerializer(page, many=True).data)


class PrescriptionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            prescription = (
                Prescription.objects.select_related('doctor', 'patient', 'dependent')
                .prefetch_related('items').get(pk=pk)
            )
        except Prescription.DoesNotExist:
            raise NotFound()
        if request.user not in (prescription.doctor, prescription.patient):
            raise NotFound()
        log_record_access(request.user, prescription)
        return Response(PrescriptionSerializer(prescription).data)


class PrescriptionApplyView(APIView):
    permission_classes = [IsPatient]

    def post(self, request, pk):
        try:
            prescription = Prescription.objects.prefetch_related('items').get(pk=pk, patient=request.user)
        except Prescription.DoesNotExist:
            raise NotFound()

        # Don't duplicate medications if the prescription was already applied.
        existing = Medication.objects.filter(prescription_item__prescription=prescription)
        if existing.exists():
            return Response(MedicationSerializer(existing, many=True).data)

        # No MedicationSchedule created here — frequency is doctor free text
        # and can't be reliably parsed into exact reminder times, so the
        # patient sets the schedule themselves on the edit screen.
        medications = Medication.objects.bulk_create([
            Medication(
                patient=request.user,
                name=item.drug_name,
                dosage=item.dosage,
                notes=item.instructions,
                source=Medication.SOURCE_PRESCRIPTION,
                prescription_item=item,
            )
            for item in prescription.items.all()
        ])
        return Response(
            MedicationSerializer(medications, many=True).data, status=status.HTTP_201_CREATED
        )
