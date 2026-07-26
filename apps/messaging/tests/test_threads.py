import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from rest_framework import status

from apps.appointments.tests.test_appointments import (
    _register_and_login,
    doctor_payload,
    patient_payload,
)
from apps.messaging.models import Message, Thread
from apps.notifications.models import Notification
from apps.notifications.tasks import send_new_message

from .base import THREADS_URL, UNREAD_COUNT_URL, MessagingTestBase, messages_url

User = get_user_model()


class ThreadCreateTests(MessagingTestBase):
    def test_patient_creates_thread_with_doctor(self):
        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['patient']['id'], str(self.patient.id))
        self.assertEqual(res.data['doctor']['id'], str(self.doctor.id))
        self.assertTrue(Thread.objects.filter(patient=self.patient, doctor=self.doctor).exists())

    def test_doctor_creates_thread_with_patient(self):
        self.as_doctor()
        res = self.client.post(THREADS_URL, {'participant_id': str(self.patient.id)}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Thread.objects.filter(patient=self.patient, doctor=self.doctor).exists())

    def test_create_is_idempotent_get_or_create(self):
        self.as_patient()
        res1 = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        res2 = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res1.data['id'], res2.data['id'])
        self.assertEqual(Thread.objects.count(), 1)

    def test_either_side_gets_the_same_thread(self):
        self.as_patient()
        res1 = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        self.as_doctor()
        res2 = self.client.post(THREADS_URL, {'participant_id': str(self.patient.id)}, format='json')
        self.assertEqual(res1.data['id'], res2.data['id'])
        self.assertEqual(Thread.objects.count(), 1)

    def test_rejects_creation_without_shared_appointment_history(self):
        _register_and_login(self.client, doctor_payload(email='other-doc@test.com'))
        other_doctor = User.objects.get(email='other-doc@test.com')
        other_doctor.doctor_profile.is_verified = True
        other_doctor.doctor_profile.save(update_fields=['is_verified'])

        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': str(other_doctor.id)}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data['code'], 'no_shared_history')
        self.assertFalse(Thread.objects.filter(patient=self.patient, doctor=other_doctor).exists())

    def test_patient_targeting_a_patient_returns_400(self):
        _register_and_login(self.client, patient_payload(email='other-pat@test.com'))
        other_patient = User.objects.get(email='other-pat@test.com')
        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': str(other_patient.id)}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_doctor_targeting_a_doctor_returns_400(self):
        _register_and_login(self.client, doctor_payload(email='other-doc2@test.com'))
        other_doctor = User.objects.get(email='other-doc2@test.com')
        self.as_doctor()
        res = self.client.post(THREADS_URL, {'participant_id': str(other_doctor.id)}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_participant_returns_400(self):
        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': str(uuid.uuid4())}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_participant_id_returns_400(self):
        self.as_patient()
        res = self.client.post(THREADS_URL, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_participant_id_returns_400(self):
        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': 'not-a-uuid'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_requires_authentication(self):
        res = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class ThreadListTests(MessagingTestBase):
    def test_lists_only_own_threads(self):
        self.as_patient()
        self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')

        _register_and_login(self.client, patient_payload(email='outsider@test.com'))
        outsider_token = _register_and_login(self.client, patient_payload(email='outsider2@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {outsider_token}')
        res = self.client.get(THREADS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 0)
        self.assertEqual(res.data['results'], [])

    def test_both_participants_see_the_thread(self):
        self.as_patient()
        self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')

        res = self.client.get(THREADS_URL)
        self.assertEqual(res.data['count'], 1)

        self.as_doctor()
        res = self.client.get(THREADS_URL)
        self.assertEqual(res.data['count'], 1)

    def test_list_requires_authentication(self):
        res = self.client.get(THREADS_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class ThreadListPaginationAndFieldsTests(MessagingTestBase):
    def test_list_response_is_paginated(self):
        self.as_patient()
        self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        res = self.client.get(THREADS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        for key in ('count', 'next', 'previous', 'results'):
            self.assertIn(key, res.data)

    def test_last_message_and_unread_count_reflect_reality(self):
        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        thread_id = res.data['id']
        self.client.post(messages_url(thread_id), {'body': 'first'}, format='json')
        self.client.post(messages_url(thread_id), {'body': 'second'}, format='json')

        self.as_doctor()
        res = self.client.get(THREADS_URL)
        thread_data = res.data['results'][0]
        self.assertEqual(thread_data['last_message']['body'], 'second')
        self.assertEqual(thread_data['unread_count'], 2)

        self.client.get(messages_url(thread_id))
        res = self.client.get(THREADS_URL)
        self.assertEqual(res.data['results'][0]['unread_count'], 0)

    def test_list_with_no_messages_yet_has_null_last_message(self):
        self.as_patient()
        self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        res = self.client.get(THREADS_URL)
        thread_data = res.data['results'][0]
        self.assertIsNone(thread_data['last_message'])
        self.assertEqual(thread_data['unread_count'], 0)

    def test_list_query_count_does_not_scale_with_thread_count(self):
        """Regression guard for the N+1 fixed in ThreadListCreateView.get():
        query count must stay flat as the number of threads on the page grows,
        not scale 2x per thread (get_last_message/get_unread_count used to
        each run a fresh query per thread)."""
        self.as_patient()
        for i in range(4):
            other_patient_token = _register_and_login(
                self.client, patient_payload(email=f'msg-patient{i}@test.com'),
            )
            other_patient = User.objects.get(email=f'msg-patient{i}@test.com')
            self._make_appointment(patient=other_patient, starts_at=self._future_dt(13 + i))

            self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {other_patient_token}')
            res = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
            thread_id = res.data['id']
            self.client.post(messages_url(thread_id), {'body': 'hi'}, format='json')
            self.client.post(messages_url(thread_id), {'body': 'hi again'}, format='json')

        self.as_doctor()
        # 1) auth user lookup, 2) paginator count, 3) annotated thread query,
        # 4) batch fetch of last messages by id — flat regardless of how many
        # threads/messages exist, unlike the old 2N+1 pattern.
        with self.assertNumQueries(4):
            res = self.client.get(THREADS_URL)
        self.assertEqual(res.data['count'], 4)


class ThreadMessageTests(MessagingTestBase):
    def setUp(self):
        super().setUp()
        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        self.thread_id = res.data['id']

    def test_participant_can_send_message(self):
        with patch('apps.notifications.tasks.send_new_message.delay') as mock_delay:
            res = self.client.post(messages_url(self.thread_id), {'body': 'Hello doctor'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['body'], 'Hello doctor')
        self.assertTrue(res.data['is_mine'])
        mock_delay.assert_called_once()

    def test_other_participant_can_reply(self):
        self.client.post(messages_url(self.thread_id), {'body': 'Hello doctor'}, format='json')
        self.as_doctor()
        res = self.client.post(messages_url(self.thread_id), {'body': 'Hello patient'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data['is_mine'])

    def test_empty_body_returns_400(self):
        res = self.client.post(messages_url(self.thread_id), {'body': '   '}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Message.objects.count(), 0)

    def test_missing_body_returns_400(self):
        res = self.client.post(messages_url(self.thread_id), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_too_long_body_returns_400(self):
        res = self.client.post(messages_url(self.thread_id), {'body': 'x' * 4001}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Message.objects.count(), 0)

    def test_thread_updated_at_bumped_on_new_message(self):
        before = Thread.objects.get(pk=self.thread_id).updated_at
        self.client.post(messages_url(self.thread_id), {'body': 'Hi'}, format='json')
        after = Thread.objects.get(pk=self.thread_id).updated_at
        self.assertGreater(after, before)

    def test_non_participant_gets_404_not_403(self):
        outsider_token = _register_and_login(self.client, patient_payload(email='outsider@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {outsider_token}')
        res = self.client.get(messages_url(self.thread_id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_participant_cannot_post_either(self):
        outsider_token = _register_and_login(self.client, patient_payload(email='outsider3@test.com'))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {outsider_token}')
        res = self.client.post(messages_url(self.thread_id), {'body': 'sneaky'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_nonexistent_thread_returns_404(self):
        res = self.client.get(messages_url(uuid.uuid4()))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_reading_marks_incoming_unread_messages_as_read(self):
        self.client.post(messages_url(self.thread_id), {'body': 'Hi'}, format='json')
        self.as_doctor()
        res = self.client.get(messages_url(self.thread_id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        message = Message.objects.get(thread_id=self.thread_id, sender=self.patient)
        self.assertIsNotNone(message.read_at)

    def test_reading_does_not_mark_own_messages(self):
        self.client.post(messages_url(self.thread_id), {'body': 'Hi'}, format='json')
        self.client.get(messages_url(self.thread_id))
        message = Message.objects.get(thread_id=self.thread_id, sender=self.patient)
        self.assertIsNone(message.read_at)

    def test_read_marking_is_idempotent(self):
        self.client.post(messages_url(self.thread_id), {'body': 'Hi'}, format='json')
        self.as_doctor()
        self.client.get(messages_url(self.thread_id))
        message = Message.objects.get(thread_id=self.thread_id, sender=self.patient)
        first_read_at = message.read_at
        self.assertIsNotNone(first_read_at)

        self.client.get(messages_url(self.thread_id))
        message.refresh_from_db()
        self.assertEqual(message.read_at, first_read_at)

    def test_body_is_encrypted_at_rest(self):
        self.client.post(messages_url(self.thread_id), {'body': 'my secret condition'}, format='json')
        message = Message.objects.get(thread_id=self.thread_id)
        self.assertEqual(message.body, 'my secret condition')
        with connection.cursor() as cursor:
            cursor.execute('SELECT body FROM messaging_message WHERE id = %s', [str(message.pk)])
            raw = cursor.fetchone()[0]
        self.assertNotEqual(raw, 'my secret condition')
        self.assertNotIn('secret', raw)

    def test_message_list_is_paginated(self):
        res = self.client.get(messages_url(self.thread_id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('results', res.data)
        self.assertIn('count', res.data)


class UnreadCountTests(MessagingTestBase):
    def test_unread_count_across_threads(self):
        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        thread_id = res.data['id']
        self.client.post(messages_url(thread_id), {'body': 'Hi'}, format='json')
        self.client.post(messages_url(thread_id), {'body': 'Hi again'}, format='json')

        self.as_doctor()
        res = self.client.get(UNREAD_COUNT_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['unread_count'], 2)

        self.client.get(messages_url(thread_id))
        res = self.client.get(UNREAD_COUNT_URL)
        self.assertEqual(res.data['unread_count'], 0)

    def test_unread_count_does_not_count_own_messages(self):
        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        thread_id = res.data['id']
        self.client.post(messages_url(thread_id), {'body': 'Hi'}, format='json')

        res = self.client.get(UNREAD_COUNT_URL)
        self.assertEqual(res.data['unread_count'], 0)

    def test_unread_count_requires_authentication(self):
        res = self.client.get(UNREAD_COUNT_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class NewMessagePushTaskTests(MessagingTestBase):
    def test_send_new_message_notifies_the_other_participant(self):
        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        thread_id = res.data['id']
        res = self.client.post(messages_url(thread_id), {'body': 'Hello'}, format='json')
        message_id = res.data['id']

        send_new_message(message_id)

        notif = Notification.objects.filter(user=self.doctor).latest('sent_at')
        self.assertIn(self.patient.first_name, notif.title + notif.message)
        # Privacy: message content must never leak into the push/notification text.
        self.assertNotIn('Hello', notif.title + notif.message)

    def test_send_new_message_unknown_message_is_a_noop(self):
        # setUp's doctor-verification save already fires its own notification
        # (see apps/users/models.py's is_verified post_save signal) — assert
        # no *additional* Notification is created, rather than an absolute count.
        before = Notification.objects.count()
        send_new_message(str(uuid.uuid4()))
        self.assertEqual(Notification.objects.count(), before)


@override_settings(ASSISTANT_ENCRYPTION_KEY='')
class MessagingDisabledTests(MessagingTestBase):
    """Message.body shares ASSISTANT_ENCRYPTION_KEY/EncryptedTextField with
    apps.assistant — an unset key must disable sending (503), not 500."""

    def setUp(self):
        super().setUp()
        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        self.thread_id = res.data['id']

    def test_sending_message_returns_503_when_key_unset(self):
        res = self.client.post(messages_url(self.thread_id), {'body': 'Hi'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(Message.objects.count(), 0)

    def test_reading_messages_is_not_gated_when_key_unset(self):
        res = self.client.get(messages_url(self.thread_id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_thread_creation_is_not_gated_when_key_unset(self):
        # The thread was already created in setUp while the key was unset —
        # its mere existence proves ThreadListCreateView.post isn't gated.
        self.assertTrue(Thread.objects.filter(pk=self.thread_id).exists())


class MessageThrottleTests(MessagingTestBase):
    def setUp(self):
        super().setUp()
        self.as_patient()
        res = self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')
        self.thread_id = res.data['id']

    def test_sending_is_throttled_after_60_per_minute(self):
        for _ in range(60):
            res = self.client.post(messages_url(self.thread_id), {'body': 'hi'}, format='json')
            self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        res = self.client.post(messages_url(self.thread_id), {'body': 'hi'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_throttle_does_not_apply_to_reading_messages(self):
        for _ in range(60):
            self.client.post(messages_url(self.thread_id), {'body': 'hi'}, format='json')
        # The 61st POST above would 429, but GET (chat polling) must stay
        # unaffected by the send-scoped throttle — see get_throttles().
        res = self.client.get(messages_url(self.thread_id))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
