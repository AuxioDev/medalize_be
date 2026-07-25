import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
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
        self.assertEqual(res.data, [])

    def test_both_participants_see_the_thread(self):
        self.as_patient()
        self.client.post(THREADS_URL, {'participant_id': str(self.doctor.id)}, format='json')

        res = self.client.get(THREADS_URL)
        self.assertEqual(len(res.data), 1)

        self.as_doctor()
        res = self.client.get(THREADS_URL)
        self.assertEqual(len(res.data), 1)

    def test_list_requires_authentication(self):
        res = self.client.get(THREADS_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


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
