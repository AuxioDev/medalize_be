import uuid
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from apps.appointments.tests.test_appointments import _register_and_login, patient_payload
from apps.family.models import Dependent
from apps.records.models import MedicalRecord

RECORDS_URL = '/api/records/'


def _detail_url(pk):
    return f'{RECORDS_URL}{pk}/'


def _png_bytes():
    buf = BytesIO()
    Image.new('RGB', (10, 10), color='red').save(buf, format='PNG')
    return buf.getvalue()


def _jpeg_bytes():
    buf = BytesIO()
    Image.new('RGB', (10, 10), color='blue').save(buf, format='JPEG')
    return buf.getvalue()


def _pdf_bytes():
    # Validation only checks the %PDF- header, matching DiplomaUploadView.
    return b'%PDF-1.4\n%fake minimal pdf for tests\n'


def _png_file(name='scan.png'):
    return SimpleUploadedFile(name, _png_bytes(), content_type='image/png')


def _jpeg_file(name='scan.jpg'):
    return SimpleUploadedFile(name, _jpeg_bytes(), content_type='image/jpeg')


def _pdf_file(name='report.pdf'):
    return SimpleUploadedFile(name, _pdf_bytes(), content_type='application/pdf')


class MedicalRecordTestBase(APITestCase):
    def setUp(self):
        self.patient_token = _register_and_login(self.client, patient_payload())
        self.doctor_token = _register_and_login(
            self.client, {
                'email': 'doctor@test.com', 'password': 'Pass1234', 'password_confirm': 'Pass1234',
                'role': 'doctor', 'first_name': 'John', 'last_name': 'Smith', 'privacy_consent': True,
            },
        )
        self.as_patient()

    def as_patient(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.patient_token}')

    def as_doctor(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.doctor_token}')

    def as_anonymous(self):
        self.client.credentials()

    def _upload(self, file, **extra):
        data = {'title': 'Blood test', 'record_type': 'lab_result'}
        data.update(extra)
        data['file'] = file
        return self.client.post(RECORDS_URL, data, format='multipart')


class UploadValidationTests(MedicalRecordTestBase):
    def test_upload_pdf_returns_201(self):
        res = self._upload(_pdf_file())
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(MedicalRecord.objects.count(), 1)
        self.assertTrue(MedicalRecord.objects.get().file.name.endswith('.pdf'))

    def test_upload_png_returns_201(self):
        res = self._upload(_png_file())
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(MedicalRecord.objects.get().file.name.endswith('.png'))

    def test_upload_jpeg_returns_201(self):
        res = self._upload(_jpeg_file())
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(MedicalRecord.objects.get().file.name.endswith('.jpg'))

    def test_upload_randomizes_filename(self):
        res = self._upload(_pdf_file(name='very-identifiable-name.pdf'))
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        stored_name = MedicalRecord.objects.get().file.name
        self.assertNotIn('very-identifiable-name', stored_name)

    def test_upload_rejects_non_pdf_non_image_bytes(self):
        bogus = SimpleUploadedFile('evil.html', b'<script>alert(1)</script>', content_type='text/html')
        res = self._upload(bogus)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(MedicalRecord.objects.count(), 0)

    def test_upload_rejects_oversized_file(self):
        oversized = SimpleUploadedFile(
            'huge.pdf', b'%PDF-1.4' + (b'0' * (15 * 1024 * 1024 + 1)), content_type='application/pdf',
        )
        res = self._upload(oversized)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(MedicalRecord.objects.count(), 0)

    def test_upload_missing_file_returns_400(self):
        res = self.client.post(
            RECORDS_URL, {'title': 'No file', 'record_type': 'other'}, format='multipart',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_missing_title_returns_400(self):
        res = self.client.post(
            RECORDS_URL, {'record_type': 'other', 'file': _pdf_file()}, format='multipart',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(MedicalRecord.objects.count(), 0)

    def test_upload_invalid_record_type_returns_400(self):
        res = self._upload(_pdf_file(), record_type='not-a-real-type')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_doctor_cannot_upload_record(self):
        self.as_doctor()
        res = self._upload(_pdf_file())
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_upload_record(self):
        self.as_anonymous()
        res = self._upload(_pdf_file())
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class DependentRecordTests(MedicalRecordTestBase):
    """dependent_id validation on MedicalRecordCreateSerializer — submitted
    as a plain multipart form field, same shared resolve_dependent() rule as
    booking/medications."""

    def _patient(self):
        from django.contrib.auth import get_user_model
        return get_user_model().objects.get(email='patient@test.com')

    def setUp(self):
        super().setUp()
        self.dependent = Dependent.objects.create(
            managed_by=self._patient(), first_name='Kid', last_name='Doe', relationship='child',
        )

    def test_upload_with_own_active_dependent_returns_201(self):
        res = self._upload(_pdf_file(), dependent_id=str(self.dependent.id))
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['dependent']['id'], str(self.dependent.id))
        record = MedicalRecord.objects.get(pk=res.data['id'])
        self.assertEqual(record.dependent_id, self.dependent.id)
        self.assertEqual(record.patient_id, self._patient().id)

    def test_upload_without_dependent_id_leaves_dependent_null(self):
        res = self._upload(_pdf_file())
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(res.data['dependent'])

    def test_upload_with_someone_elses_dependent_returns_400(self):
        other_token = _register_and_login(self.client, patient_payload(email='dep-other@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self._upload(_pdf_file(), dependent_id=str(self.dependent.id))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('dependent_id', res.data['errors'])
        self.assertEqual(MedicalRecord.objects.count(), 0)

    def test_upload_with_nonexistent_dependent_returns_400(self):
        res = self._upload(_pdf_file(), dependent_id=str(uuid.uuid4()))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('dependent_id', res.data['errors'])

    def test_upload_with_inactive_dependent_returns_400(self):
        self.dependent.is_active = False
        self.dependent.save(update_fields=['is_active'])
        res = self._upload(_pdf_file(), dependent_id=str(self.dependent.id))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('dependent_id', res.data['errors'])


class RecordListDetailTests(MedicalRecordTestBase):
    def test_list_returns_only_own_records(self):
        self._upload(_pdf_file())
        other_token = _register_and_login(self.client, patient_payload(email='other@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        self._upload(_png_file())

        self.as_patient()
        res = self.client.get(RECORDS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(len(res.data['results']), 1)

    def test_list_response_is_paginated(self):
        self._upload(_pdf_file())
        res = self.client.get(RECORDS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        for key in ('count', 'next', 'previous', 'results'):
            self.assertIn(key, res.data)

    def test_list_crosses_page_boundary_at_20(self):
        for i in range(25):
            self._upload(_pdf_file(name=f'record-{i}.pdf'))

        page1 = self.client.get(RECORDS_URL)
        self.assertEqual(page1.data['count'], 25)
        self.assertEqual(len(page1.data['results']), 20)
        self.assertIsNotNone(page1.data['next'])
        self.assertIsNone(page1.data['previous'])

        page2 = self.client.get(RECORDS_URL, {'page': 2})
        self.assertEqual(len(page2.data['results']), 5)
        self.assertIsNone(page2.data['next'])
        self.assertIsNotNone(page2.data['previous'])

    def test_get_returns_signed_file_url(self):
        create_res = self._upload(_pdf_file())
        res = self.client.get(_detail_url(create_res.data['id']))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['file'])

    def test_get_other_patients_record_returns_404(self):
        create_res = self._upload(_pdf_file())
        other_token = _register_and_login(self.client, patient_payload(email='other2@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.get(_detail_url(create_res.data['id']))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_returns_204_and_removes_row(self):
        create_res = self._upload(_pdf_file())
        res = self.client.delete(_detail_url(create_res.data['id']))
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(MedicalRecord.objects.filter(pk=create_res.data['id']).exists())

    def test_delete_other_patients_record_returns_404(self):
        create_res = self._upload(_pdf_file())
        other_token = _register_and_login(self.client, patient_payload(email='other3@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.delete(_detail_url(create_res.data['id']))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(MedicalRecord.objects.filter(pk=create_res.data['id']).exists())


class RecordAccessLogTests(MedicalRecordTestBase):
    """Passive audit trail (apps.core.models.RecordAccessLog) — a successful
    GET on the record detail endpoint creates a log row; nothing else does."""

    def test_get_creates_an_access_log_row(self):
        from django.contrib.auth import get_user_model
        from django.contrib.contenttypes.models import ContentType
        from apps.core.models import RecordAccessLog

        create_res = self._upload(_pdf_file())
        record_id = create_res.data['id']

        self.client.get(_detail_url(record_id))

        log = RecordAccessLog.objects.get()
        self.assertEqual(log.accessed_by, get_user_model().objects.get(email='patient@test.com'))
        self.assertEqual(log.content_type, ContentType.objects.get_for_model(MedicalRecord))
        self.assertEqual(str(log.object_id), record_id)
        self.assertEqual(log.action, RecordAccessLog.ACTION_VIEW)

    def test_failed_get_does_not_create_a_log_row(self):
        from apps.core.models import RecordAccessLog

        create_res = self._upload(_pdf_file())
        other_token = _register_and_login(self.client, patient_payload(email='no-access@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')

        res = self.client.get(_detail_url(create_res.data['id']))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(RecordAccessLog.objects.exists())

    def test_list_endpoint_does_not_create_log_rows(self):
        from apps.core.models import RecordAccessLog

        self._upload(_pdf_file())
        self.client.get(RECORDS_URL)
        self.assertFalse(RecordAccessLog.objects.exists())

    def test_each_get_creates_its_own_row(self):
        from apps.core.models import RecordAccessLog

        create_res = self._upload(_pdf_file())
        self.client.get(_detail_url(create_res.data['id']))
        self.client.get(_detail_url(create_res.data['id']))
        self.assertEqual(RecordAccessLog.objects.count(), 2)
