import uuid

from rest_framework import status

from apps.appointments.tests.test_appointments import _register_and_login, patient_payload
from apps.messaging.models import Report

from .base import THREADS_URL, MessagingTestBase, messages_url


def _thread_report_url(thread_id):
    return f'{THREADS_URL}{thread_id}/report/'


def _message_report_url(message_id):
    return f'/api/messaging/messages/{message_id}/report/'


class ThreadReportTests(MessagingTestBase):
    """(3c) — either participant can report a thread in general, or one
    specific message in it, for admin follow-up (no automated action)."""

    def setUp(self):
        super().setUp()
        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        self.thread_id = res.data['id']

    def test_patient_can_report_thread(self):
        res = self.client.post(
            _thread_report_url(self.thread_id),
            {'reason': 'harassment', 'details': 'Repeated unwanted messages'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['reason'], 'harassment')
        self.assertEqual(res.data['details'], 'Repeated unwanted messages')
        self.assertIsNone(res.data['message'])
        report = Report.objects.get(pk=res.data['id'])
        self.assertEqual(str(report.thread_id), self.thread_id)
        self.assertEqual(report.reporter, self.patient)

    def test_doctor_can_report_thread(self):
        self.as_doctor()
        res = self.client.post(_thread_report_url(self.thread_id), {'reason': 'spam'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Report.objects.get(pk=res.data['id']).reporter, self.doctor)

    def test_thread_can_be_reported_more_than_once(self):
        self.client.post(_thread_report_url(self.thread_id), {'reason': 'spam'}, format='json')
        self.client.post(_thread_report_url(self.thread_id), {'reason': 'fraud'}, format='json')
        self.assertEqual(Report.objects.filter(thread_id=self.thread_id).count(), 2)

    def test_missing_reason_returns_400(self):
        res = self.client.post(_thread_report_url(self.thread_id), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_reason_returns_400(self):
        res = self.client.post(
            _thread_report_url(self.thread_id), {'reason': 'not-a-real-reason'}, format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reporter_field_cannot_be_spoofed(self):
        res = self.client.post(
            _thread_report_url(self.thread_id),
            {'reason': 'spam', 'reporter': str(self.doctor.id)},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        report = Report.objects.get(pk=res.data['id'])
        self.assertEqual(report.reporter, self.patient)  # the actual requester, not the spoofed value

    def test_non_participant_cannot_report_thread(self):
        outsider_token = _register_and_login(self.client, patient_payload(email='outsider-report@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {outsider_token}')
        res = self.client.post(_thread_report_url(self.thread_id), {'reason': 'spam'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_nonexistent_thread_returns_404(self):
        res = self.client.post(_thread_report_url(uuid.uuid4()), {'reason': 'spam'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_report_requires_authentication(self):
        self.client.credentials()
        res = self.client.post(_thread_report_url(self.thread_id), {'reason': 'spam'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_report_visible_in_admin_registration(self):
        from apps.messaging.admin import ReportAdmin
        self.assertIn('reason', ReportAdmin.list_filter)
        self.assertIn('reason', ReportAdmin.list_display)


class MessageReportTests(MessagingTestBase):
    def setUp(self):
        super().setUp()
        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        self.thread_id = res.data['id']
        msg_res = self.client.post(messages_url(self.thread_id), {'body': 'rude message'}, format='json')
        self.message_id = msg_res.data['id']

    def test_participant_can_report_a_specific_message(self):
        self.as_doctor()
        res = self.client.post(
            _message_report_url(self.message_id),
            {'reason': 'inappropriate_content', 'details': 'Offensive language'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        report = Report.objects.get(pk=res.data['id'])
        self.assertEqual(str(report.message_id), self.message_id)
        self.assertEqual(str(report.thread_id), self.thread_id)
        self.assertEqual(report.reporter, self.doctor)

    def test_non_participant_cannot_report_message(self):
        outsider_token = _register_and_login(self.client, patient_payload(email='outsider-msg@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {outsider_token}')
        res = self.client.post(_message_report_url(self.message_id), {'reason': 'spam'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_nonexistent_message_returns_404(self):
        res = self.client.post(_message_report_url(uuid.uuid4()), {'reason': 'spam'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_missing_reason_returns_400(self):
        res = self.client.post(_message_report_url(self.message_id), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
