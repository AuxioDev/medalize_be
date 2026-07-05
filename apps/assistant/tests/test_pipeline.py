import datetime
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from rest_framework import status

from apps.appointments.models import Appointment, Review
from apps.assistant.i18n import assistant_message
from apps.assistant.models import Conversation, Message
from apps.assistant.service import search_doctors
from apps.assistant.tasks import delete_expired_conversations
from apps.doctors.models import Workplace

from .base import (
    AssistantTestCase,
    FakeResponse,
    FakeTextBlock,
    FakeToolUseBlock,
    fake_text_response,
    fake_tool_response,
    messages_url,
)

User = get_user_model()


def create_verified_doctor(email, first_name, last_name, specialization='cardiology',
                           city='Baku', verified=True):
    doctor = User.objects.create_user(
        email=email, password='Pass1234', role='doctor',
        first_name=first_name, last_name=last_name,
    )
    profile = doctor.doctor_profile
    profile.specialization = specialization
    profile.is_verified = verified
    profile.save(update_fields=['specialization', 'is_verified'])
    Workplace.objects.create(
        doctor=doctor, name=f'{first_name} Clinic', address='1 Test St',
        city=city, type='clinic', is_primary=True,
    )
    return doctor


def add_review(doctor, patient, rating, slot_index=0):
    workplace = doctor.workplaces.first()
    starts = timezone.now() - timedelta(days=30) + timedelta(minutes=30 * slot_index)
    appointment = Appointment.objects.create(
        doctor=doctor, patient=patient, workplace=workplace,
        starts_at=starts, ends_at=starts + timedelta(minutes=30),
        status=Appointment.STATUS_COMPLETED,
    )
    return Review.objects.create(
        appointment=appointment, doctor=doctor, patient=patient, rating=rating,
    )


class NotMedicalGateTests(AssistantTestCase):
    def setUp(self):
        super().setUp()
        self.conversation = Conversation.objects.create(patient=self.patient)

    def test_not_medical_saves_refusal_without_calling_main_model(self):
        with patch('apps.assistant.service.classify_is_medical', return_value=False) as gate, \
                patch('apps.assistant.service.call_assistant') as chat:
            res = self.client.post(
                messages_url(self.conversation.pk),
                {'content': 'What is the capital of France?'},
                format='json',
            )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        gate.assert_called_once()
        chat.assert_not_called()
        self.assertEqual(res.data['role'], 'assistant')
        self.assertEqual(res.data['content'], assistant_message('not_medical_refusal', 'en'))
        self.assertEqual(res.data['suggested_doctors'], [])
        self.assertEqual(self.conversation.messages.count(), 2)

    def test_refusal_is_localized_to_patient_language(self):
        self.patient.language = 'ru'
        self.patient.save(update_fields=['language'])
        with patch('apps.assistant.service.classify_is_medical', return_value=False):
            res = self.client.post(
                messages_url(self.conversation.pk), {'content': 'привет'}, format='json'
            )
        self.assertEqual(res.data['content'], assistant_message('not_medical_refusal', 'ru'))

    def test_gate_receives_conversation_context(self):
        Message.objects.create(
            conversation=self.conversation, role=Message.ROLE_USER, content='I have a rash'
        )
        Message.objects.create(
            conversation=self.conversation, role=Message.ROLE_ASSISTANT, content='Where is it?'
        )
        with patch('apps.assistant.service.classify_is_medical', return_value=False) as gate:
            self.client.post(
                messages_url(self.conversation.pk), {'content': 'on my arm'}, format='json'
            )
        recent = gate.call_args[0][0]
        self.assertEqual([m['content'] for m in recent],
                         ['I have a rash', 'Where is it?', 'on my arm'])
        self.assertEqual([m['role'] for m in recent], ['user', 'assistant', 'user'])


class MedicalPathTests(AssistantTestCase):
    def setUp(self):
        super().setUp()
        self.conversation = Conversation.objects.create(patient=self.patient)

    def _send(self, content='I have chest pain', chat_side_effect=None):
        with patch('apps.assistant.service.classify_is_medical', return_value=True), \
                patch('apps.assistant.service.call_assistant',
                      side_effect=chat_side_effect or [fake_text_response('Please rest.')]) as chat:
            res = self.client.post(
                messages_url(self.conversation.pk), {'content': content}, format='json'
            )
        return res, chat

    def test_medical_answer_gets_disclaimer_appended(self):
        res, chat = self._send()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        chat.assert_called_once()
        disclaimer = assistant_message('disclaimer', 'en')
        self.assertEqual(res.data['content'], f'Please rest.\n\n{disclaimer}')
        self.assertEqual(res.data['suggested_doctors'], [])

    def test_disclaimer_localized(self):
        self.patient.language = 'tr'
        self.patient.save(update_fields=['language'])
        res, _ = self._send()
        self.assertTrue(res.data['content'].endswith(assistant_message('disclaimer', 'tr')))

    def test_system_prompt_includes_patient_context(self):
        profile = self.patient.patient_profile
        profile.date_of_birth = datetime.date(1990, 1, 1)
        profile.allergies = 'penicillin'
        profile.chronic_conditions = 'asthma'
        profile.medications = 'ventolin'
        profile.save()
        _, chat = self._send()
        system_prompt = chat.call_args[0][0]
        self.assertIn('penicillin', system_prompt)
        self.assertIn('asthma', system_prompt)
        self.assertIn('ventolin', system_prompt)
        self.assertIn('Age:', system_prompt)

    def test_system_prompt_omits_empty_profile_fields(self):
        _, chat = self._send()
        system_prompt = chat.call_args[0][0]
        self.assertNotIn('Allergies', system_prompt)
        self.assertNotIn('Chronic conditions', system_prompt)
        self.assertNotIn('Age:', system_prompt)

    def test_system_prompt_requests_patient_language(self):
        self.patient.language = 'az'
        self.patient.save(update_fields=['language'])
        _, chat = self._send()
        self.assertIn('language code: az', chat.call_args[0][0])

    def test_conversation_updated_at_bumped(self):
        before = Conversation.objects.get(pk=self.conversation.pk).updated_at
        self._send()
        after = Conversation.objects.get(pk=self.conversation.pk).updated_at
        self.assertGreater(after, before)

    def test_pipeline_failure_returns_503(self):
        with patch('apps.assistant.service.classify_is_medical', side_effect=RuntimeError('api down')):
            res = self.client.post(
                messages_url(self.conversation.pk), {'content': 'chest pain'}, format='json'
            )
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(res.data['detail'], assistant_message('assistant_unavailable', 'en'))

    def test_content_is_encrypted_at_rest(self):
        self._send(content='my secret symptom')
        message = self.conversation.messages.get(role=Message.ROLE_USER)
        self.assertEqual(message.content, 'my secret symptom')
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT content FROM assistant_message WHERE id = %s', [str(message.pk)]
            )
            raw = cursor.fetchone()[0]
        self.assertNotEqual(raw, 'my secret symptom')
        self.assertNotIn('secret', raw)


class ToolUseTests(AssistantTestCase):
    def setUp(self):
        super().setUp()
        self.conversation = Conversation.objects.create(patient=self.patient)
        self.cardiologist = create_verified_doctor(
            'cardio@test.com', 'Aysel', 'Aliyeva', specialization='cardiology'
        )

    def test_tool_call_fills_suggested_doctors_from_real_db(self):
        responses = [
            fake_tool_response(input={'specialization': 'cardiology'}),
            fake_text_response('See a cardiologist.'),
        ]
        with patch('apps.assistant.service.classify_is_medical', return_value=True), \
                patch('apps.assistant.service.call_assistant', side_effect=responses) as chat:
            res = self.client.post(
                messages_url(self.conversation.pk), {'content': 'chest pain'}, format='json'
            )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(len(res.data['suggested_doctors']), 1)
        card = res.data['suggested_doctors'][0]
        self.assertEqual(card['id'], str(self.cardiologist.id))
        self.assertEqual(card['first_name'], 'Aysel')
        self.assertEqual(card['specialization_display'], 'Cardiology')
        self.assertEqual(card['city'], 'Baku')
        # Persisted on the assistant message too.
        saved = self.conversation.messages.get(role=Message.ROLE_ASSISTANT)
        self.assertEqual(saved.suggested_doctors, res.data['suggested_doctors'])
        # The tool result was sent back to the model on the second call.
        second_call_messages = chat.call_args[0][1]
        self.assertEqual(second_call_messages[-1]['content'][0]['type'], 'tool_result')

    def test_tool_loop_capped_at_three_iterations(self):
        endless_tool = [
            fake_tool_response(input={'specialization': 'cardiology'}, id=f'toolu_{i}')
            for i in range(5)
        ]
        with patch('apps.assistant.service.classify_is_medical', return_value=True), \
                patch('apps.assistant.service.call_assistant', side_effect=endless_tool) as chat:
            res = self.client.post(
                messages_url(self.conversation.pk), {'content': 'chest pain'}, format='json'
            )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # Initial call + at most 3 tool round-trips.
        self.assertEqual(chat.call_count, 4)
        # Model never produced text; the reply is just the disclaimer.
        self.assertEqual(res.data['content'], assistant_message('disclaimer', 'en'))

    def test_mixed_text_and_tool_response(self):
        responses = [
            FakeResponse([
                FakeTextBlock('Let me find you a doctor.'),
                FakeToolUseBlock('search_doctors', {'specialization': 'cardiology'}),
            ]),
            fake_text_response('Dr. Aliyeva is a great fit.'),
        ]
        with patch('apps.assistant.service.classify_is_medical', return_value=True), \
                patch('apps.assistant.service.call_assistant', side_effect=responses):
            res = self.client.post(
                messages_url(self.conversation.pk), {'content': 'chest pain'}, format='json'
            )
        self.assertTrue(res.data['content'].startswith('Dr. Aliyeva is a great fit.'))
        self.assertEqual(len(res.data['suggested_doctors']), 1)


class SearchDoctorsQueryTests(AssistantTestCase):
    def setUp(self):
        super().setUp()
        self.cardio_high = create_verified_doctor(
            'high@test.com', 'Zara', 'High', specialization='cardiology'
        )
        add_review(self.cardio_high, self.patient, 5, slot_index=0)
        self.cardio_low = create_verified_doctor(
            'low@test.com', 'Bob', 'Low', specialization='cardiology'
        )
        add_review(self.cardio_low, self.patient, 3, slot_index=1)
        self.cardio_ganja = create_verified_doctor(
            'ganja@test.com', 'Gunel', 'Ganja', specialization='cardiology', city='Ganja'
        )
        self.unverified = create_verified_doctor(
            'unverified@test.com', 'Uma', 'Hidden', specialization='cardiology', verified=False
        )
        self.dermatologist = create_verified_doctor(
            'derm@test.com', 'Dana', 'Skin', specialization='dermatology'
        )

    def test_filters_by_specialization_and_verified_only(self):
        results = search_doctors('cardiology')
        emails = {r['first_name'] for r in results}
        self.assertEqual(emails, {'Zara', 'Bob', 'Gunel'})

    def test_ordered_by_rating_desc_nulls_last(self):
        results = search_doctors('cardiology')
        self.assertEqual([r['first_name'] for r in results], ['Zara', 'Bob', 'Gunel'])
        self.assertEqual(results[0]['average_rating'], 5.0)
        self.assertEqual(results[1]['average_rating'], 3.0)
        self.assertIsNone(results[2]['average_rating'])

    def test_city_filter(self):
        results = search_doctors('cardiology', city='ganja')
        self.assertEqual([r['first_name'] for r in results], ['Gunel'])
        self.assertEqual(results[0]['city'], 'Ganja')

    def test_specialization_display_localized(self):
        results = search_doctors('cardiology', lang='ru')
        self.assertEqual(results[0]['specialization'], 'cardiology')
        self.assertNotEqual(results[0]['specialization_display'], 'cardiology')

    def test_capped_at_five(self):
        for i in range(6):
            create_verified_doctor(
                f'extra{i}@test.com', f'Extra{i}', 'Doc', specialization='neurology'
            )
        self.assertEqual(len(search_doctors('neurology')), 5)

    def test_unknown_specialization_returns_empty(self):
        self.assertEqual(search_doctors('astrology'), [])


class TtlCleanupTests(AssistantTestCase):
    def test_deletes_conversations_older_than_90_days_only(self):
        old = Conversation.objects.create(patient=self.patient)
        Message.objects.create(conversation=old, role=Message.ROLE_USER, content='old')
        fresh = Conversation.objects.create(patient=self.patient)
        Message.objects.create(conversation=fresh, role=Message.ROLE_USER, content='fresh')

        Conversation.objects.filter(pk=old.pk).update(
            updated_at=timezone.now() - timedelta(days=91)
        )
        Conversation.objects.filter(pk=fresh.pk).update(
            updated_at=timezone.now() - timedelta(days=89)
        )

        delete_expired_conversations()

        self.assertFalse(Conversation.objects.filter(pk=old.pk).exists())
        self.assertFalse(Message.objects.filter(conversation_id=old.pk).exists())
        self.assertTrue(Conversation.objects.filter(pk=fresh.pk).exists())
        self.assertTrue(Message.objects.filter(conversation_id=fresh.pk).exists())
