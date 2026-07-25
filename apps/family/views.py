from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.permissions import IsPatient

from .models import Dependent
from .serializers import DependentCreateSerializer, DependentSerializer


class DependentListCreateView(APIView):
    permission_classes = [IsPatient]

    def get(self, request):
        dependents = (
            Dependent.objects.filter(managed_by=request.user, is_active=True)
            .order_by('first_name')
        )
        return Response(DependentSerializer(dependents, many=True).data)

    def post(self, request):
        serializer = DependentCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        dependent = serializer.save()
        return Response(DependentSerializer(dependent).data, status=status.HTTP_201_CREATED)


class DependentDetailView(APIView):
    permission_classes = [IsPatient]

    def _get(self, pk, user):
        try:
            return Dependent.objects.get(pk=pk, managed_by=user)
        except Dependent.DoesNotExist:
            raise NotFound()

    def get(self, request, pk):
        return Response(DependentSerializer(self._get(pk, request.user)).data)

    def patch(self, request, pk):
        dependent = self._get(pk, request.user)
        serializer = DependentCreateSerializer(
            dependent, data=request.data, partial=True, context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        dependent = serializer.save()
        return Response(DependentSerializer(dependent).data)

    def delete(self, request, pk):
        # Soft delete: keeps historical appointments/medications/records
        # attached to this dependent from being orphaned (same pattern as
        # apps.medications.views.MedicationDetailView.delete).
        dependent = self._get(pk, request.user)
        dependent.is_active = False
        dependent.save(update_fields=['is_active', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)
