import logging
from io import BytesIO

from PIL import Image
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.uploads import randomize_upload_filename
from apps.users.permissions import IsPatient

from .models import MedicalRecord
from .serializers import MedicalRecordCreateSerializer, MedicalRecordSerializer

logger = logging.getLogger(__name__)

# Larger than the diploma cap (10 MB) — multi-page scanned lab results run
# bigger than a single diploma photo/scan.
_MAX_RECORD_FILE_SIZE = 15 * 1024 * 1024


def _validate_and_prepare_file(file):
    """Validate the real file bytes (not client-supplied content_type/
    extension) and randomize the filename. Copied verbatim from
    apps.doctors.views.DiplomaUploadView.post — see there for the full
    rationale (real-bytes check + randomized filename close the same
    exploitable-Content-Type gap for records that it closed for diplomas)."""
    if file.size > _MAX_RECORD_FILE_SIZE:
        raise ValidationError({'file': 'File size must not exceed 15 MB.'})

    header = file.read(8)
    file.seek(0)
    if header.startswith(b'%PDF-'):
        extension = 'pdf'
    else:
        try:
            img = Image.open(BytesIO(file.read()))
            if img.format not in ('JPEG', 'PNG'):
                raise ValidationError({'file': 'Only PDF, JPEG or PNG files are allowed.'})
            img.load()
            extension = 'jpg' if img.format == 'JPEG' else 'png'
        except ValidationError:
            raise
        except Exception:
            raise ValidationError({'file': 'Only PDF, JPEG or PNG files are allowed.'})
        finally:
            file.seek(0)

    randomize_upload_filename(file, extension)
    return file


class MedicalRecordListCreateView(APIView):
    permission_classes = [IsPatient]
    parser_classes = [MultiPartParser]

    def get(self, request):
        records = (
            MedicalRecord.objects.filter(patient=request.user)
            .select_related('dependent')
            .order_by('-record_date', '-created_at')
        )
        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(records, request)
        serializer = MedicalRecordSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            raise ValidationError({'file': 'No file provided.'})
        file = _validate_and_prepare_file(file)

        serializer = MedicalRecordCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        validated_data = dict(serializer.validated_data)
        dependent = validated_data.pop('dependent_id', None)
        record = MedicalRecord.objects.create(
            patient=request.user, file=file, dependent=dependent, **validated_data
        )
        return Response(
            MedicalRecordSerializer(record, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class MedicalRecordDetailView(APIView):
    permission_classes = [IsPatient]

    def _get(self, pk, patient):
        try:
            return MedicalRecord.objects.select_related('dependent').get(pk=pk, patient=patient)
        except MedicalRecord.DoesNotExist:
            raise NotFound()

    def get(self, request, pk):
        return Response(
            MedicalRecordSerializer(self._get(pk, request.user), context={'request': request}).data
        )

    def delete(self, request, pk):
        record = self._get(pk, request.user)
        # Clear the old file first so replaced/removed uploads don't
        # accumulate as orphans in Cloudinary (same pattern as
        # AvatarUploadView/DiplomaUploadView).
        if record.file:
            try:
                record.file.delete(save=False)
            except Exception:
                logger.warning('Failed to delete file for record %s', record.pk)
        record.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
