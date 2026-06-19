from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.users.models import DoctorProfile


@api_view(['GET'])
@permission_classes([AllowAny])
def specializations_list(request):
    return Response([
        {'value': value, 'label': label}
        for value, label in DoctorProfile.SPECIALIZATION_CHOICES
    ])
