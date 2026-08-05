import uuid

from rest_framework import status

from apps.appointments.tests.test_appointments import _register_and_login, patient_payload
from apps.messaging.models import Block, Thread

from .base import THREADS_URL, MessagingTestBase, messages_url


def _block_url(thread_id):
    return f'{THREADS_URL}{thread_id}/block/'


class ThreadBlockTests(MessagingTestBase):
    """(3b) — either participant can block the other; the blocked party then
    can't send messages in a shared thread and can't open a new one."""

    def setUp(self):
        super().setUp()
        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        self.thread_id = res.data['id']

    def test_patient_can_block_doctor(self):
        res = self.client.post(_block_url(self.thread_id), {'reason': 'inappropriate remarks'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['reason'], 'inappropriate remarks')
        self.assertTrue(
            Block.objects.filter(blocker=self.patient, blocked=self.doctor).exists()
        )

    def test_doctor_can_block_patient(self):
        self.as_doctor()
        res = self.client.post(_block_url(self.thread_id), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Block.objects.filter(blocker=self.doctor, blocked=self.patient).exists()
        )

    def test_blocking_is_idempotent(self):
        res1 = self.client.post(_block_url(self.thread_id), {}, format='json')
        res2 = self.client.post(_block_url(self.thread_id), {}, format='json')
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(Block.objects.filter(blocker=self.patient, blocked=self.doctor).count(), 1)

    def test_blocked_party_cannot_send_new_messages(self):
        self.client.post(_block_url(self.thread_id), {}, format='json')
        self.as_doctor()
        res = self.client.post(messages_url(self.thread_id), {'body': 'hello?'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data['code'], 'blocked')

    def test_blocker_also_cannot_send_after_blocking(self):
        # Blocking is mutual for messaging purposes — both directions are
        # checked (see apps.messaging.views._blocked_either_way).
        self.client.post(_block_url(self.thread_id), {}, format='json')
        res = self.client.post(messages_url(self.thread_id), {'body': 'still me'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data['code'], 'blocked')

    def test_reading_a_blocked_thread_still_works(self):
        # Blocking stops new *sends*, not read access to history already there.
        self.client.post(_block_url(self.thread_id), {}, format='json')
        res = self.client.get(messages_url(self.thread_id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_blocked_party_cannot_open_a_new_thread_with_blocker(self):
        # A thread already exists between self.patient/self.doctor (setUp) —
        # blocking there doesn't retroactively delete it (existing threads
        # stay reachable). The real test of "cannot open a NEW thread" needs
        # a pair with no thread yet at all: see
        # test_unblocked_thread_never_created_when_blocked_beforehand below.
        self.client.post(_block_url(self.thread_id), {}, format='json')
        self.as_doctor()
        # Re-fetching the same (already existing) thread still succeeds —
        # this isn't "opening a new thread", it's the existing idempotent
        # get-or-create returning the same row.
        res = self.client.post(THREADS_URL, {'participant_id': str(self.patient.id)}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['id'], self.thread_id)

    def test_unblocked_thread_never_created_when_blocked_beforehand(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        other_token = _register_and_login(self.client, patient_payload(email='fresh-pat@test.com'))
        other_patient = User.objects.get(email='fresh-pat@test.com')
        self._make_appointment(patient=other_patient, starts_at=self._future_dt(17))

        # Doctor blocks this patient before any thread exists between them.
        Block.objects.create(blocker=self.doctor, blocked=other_patient)

        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_token}')
        res = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data['code'], 'blocked')
        self.assertFalse(Thread.objects.filter(patient=other_patient, doctor=self.doctor).exists())

    def test_unblock_removes_the_restriction(self):
        self.client.post(_block_url(self.thread_id), {}, format='json')
        del_res = self.client.delete(_block_url(self.thread_id))
        self.assertEqual(del_res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Block.objects.filter(blocker=self.patient, blocked=self.doctor).exists())

        self.as_doctor()
        res = self.client.post(messages_url(self.thread_id), {'body': 'hi again'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_unblock_when_not_blocked_returns_404(self):
        res = self.client.delete(_block_url(self.thread_id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_participant_cannot_block(self):
        outsider_token = _register_and_login(self.client, patient_payload(email='outsider-block@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {outsider_token}')
        res = self.client.post(_block_url(self.thread_id), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_nonexistent_thread_returns_404(self):
        res = self.client.post(_block_url(uuid.uuid4()), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_block_requires_authentication(self):
        self.client.credentials()
        res = self.client.post(_block_url(self.thread_id), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
