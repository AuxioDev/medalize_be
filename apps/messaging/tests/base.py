from cryptography.fernet import Fernet
from django.test import override_settings

from apps.appointments.tests.test_appointments import AppointmentTestBase

TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()

THREADS_URL = '/api/messaging/threads/'
UNREAD_COUNT_URL = '/api/messaging/threads/unread-count/'


def messages_url(thread_id):
    return f'{THREADS_URL}{thread_id}/messages/'


@override_settings(ASSISTANT_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
class MessagingTestBase(AppointmentTestBase):
    """A verified doctor + patient (from AppointmentTestBase) plus one
    appointment between them, i.e. the minimum shared history required to
    open a thread. Both self.patient_token/self.doctor_token are ready to
    use via as_patient()/as_doctor()."""

    def setUp(self):
        super().setUp()
        self.appointment = self._make_appointment()
