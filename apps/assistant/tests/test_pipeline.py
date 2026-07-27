from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from rest_framework import status

from apps.appointments.models import Appointment, Review
from apps.assistant.i18n import assistant_message
from apps.assistant.models import Conversation, Message, ResponseTemplate
from apps.assistant.service import search_doctors
from apps.assistant.tasks import delete_expired_conversations
from apps.doctors.models import Workplace

from .base import AssistantTestCase, messages_url

User = get_user_model()


def create_verified_doctor(email, first_name, last_name, specialization='cardiology',
                           city='baku', verified=True):
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


class EmergencyDetectionTests(AssistantTestCase):
    """The only safety net for red-flag symptoms now that no LLM reads every
    message — checked before template matching, across all languages."""

    def setUp(self):
        super().setUp()
        self.conversation = Conversation.objects.create(patient=self.patient)

    def _post(self, content):
        return self.client.post(messages_url(self.conversation.pk), {'content': content}, format='json')

    def test_emergency_keyword_in_english(self):
        res = self._post('I have severe chest pain')
        self.assertEqual(res.data['content'], assistant_message('emergency_warning', 'en'))

    def test_emergency_keyword_in_russian(self):
        self.patient.language = 'ru'
        self.patient.save(update_fields=['language'])
        res = self._post('у меня боль в груди')
        self.assertEqual(res.data['content'], assistant_message('emergency_warning', 'ru'))

    def test_emergency_keyword_in_azerbaijani(self):
        res = self._post('nəfəs ala bilmirəm')
        self.assertEqual(res.data['content'], assistant_message('emergency_warning', 'en'))

    def test_emergency_keyword_in_turkish(self):
        res = self._post('göğüs ağrısı var')
        self.assertEqual(res.data['content'], assistant_message('emergency_warning', 'en'))

    def test_emergency_keyword_in_french(self):
        res = self._post("j'ai une douleur thoracique")
        self.assertEqual(res.data['content'], assistant_message('emergency_warning', 'en'))

    def test_emergency_keyword_in_chinese(self):
        res = self._post('我胸痛')
        self.assertEqual(res.data['content'], assistant_message('emergency_warning', 'en'))

    def test_detected_regardless_of_patient_profile_language(self):
        # patient.language stays the 'en' default but the patient types in Russian.
        res = self._post('трудно дышать')
        self.assertEqual(res.data['content'], assistant_message('emergency_warning', 'en'))

    def test_takes_priority_over_a_matching_template(self):
        ResponseTemplate.objects.create(
            triggers={'en': ['chest pain']},
            answers={'en': 'Chest pain can be caused by many things.'},
        )
        res = self._post('I have chest pain')
        self.assertEqual(res.data['content'], assistant_message('emergency_warning', 'en'))

    def test_response_does_not_query_doctors(self):
        res = self._post('severe bleeding that will not stop')
        self.assertEqual(res.data['suggested_doctors'], [])

    def test_conversation_has_two_messages(self):
        self._post('severe chest pain')
        self.assertEqual(self.conversation.messages.count(), 2)


class NotMedicalTests(AssistantTestCase):
    """Two distinct fallbacks for a message that doesn't match any template:
    a lightweight off-topic keyword list -> not_medical_refusal, anything
    else unmatched -> assistant_unclear."""

    def setUp(self):
        super().setUp()
        self.conversation = Conversation.objects.create(patient=self.patient)

    def test_off_topic_keyword_returns_not_medical_refusal(self):
        res = self.client.post(
            messages_url(self.conversation.pk),
            {'content': 'What is the weather today?'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['role'], 'assistant')
        self.assertEqual(res.data['content'], assistant_message('not_medical_refusal', 'en'))
        self.assertEqual(res.data['suggested_doctors'], [])
        self.assertEqual(self.conversation.messages.count(), 2)

    def test_off_topic_refusal_is_localized(self):
        self.patient.language = 'ru'
        self.patient.save(update_fields=['language'])
        res = self.client.post(
            messages_url(self.conversation.pk), {'content': 'какая сегодня погода'}, format='json'
        )
        self.assertEqual(res.data['content'], assistant_message('not_medical_refusal', 'ru'))

    def test_unmatched_gibberish_returns_unclear_fallback(self):
        res = self.client.post(
            messages_url(self.conversation.pk), {'content': 'zzxq flerbnop woblefritz'}, format='json'
        )
        self.assertEqual(res.data['content'], assistant_message('assistant_unclear', 'en'))

    def test_unclear_fallback_is_localized(self):
        self.patient.language = 'fr'
        self.patient.save(update_fields=['language'])
        res = self.client.post(
            messages_url(self.conversation.pk), {'content': 'zzxq flerbnop woblefritz'}, format='json'
        )
        self.assertEqual(res.data['content'], assistant_message('assistant_unclear', 'fr'))


class TemplateMatchingTests(AssistantTestCase):
    def setUp(self):
        super().setUp()
        self.conversation = Conversation.objects.create(patient=self.patient)

    def _post(self, content):
        return self.client.post(messages_url(self.conversation.pk), {'content': content}, format='json')

    def test_exact_trigger_match_returns_template_answer_with_disclaimer(self):
        ResponseTemplate.objects.create(
            triggers={'en': ['headache']},
            answers={'en': 'Headaches are often caused by dehydration or stress.'},
        )
        res = self._post('headache')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        disclaimer = assistant_message('disclaimer', 'en')
        self.assertEqual(
            res.data['content'],
            f'Headaches are often caused by dehydration or stress.\n\n{disclaimer}',
        )

    def test_matches_despite_extra_surrounding_words(self):
        ResponseTemplate.objects.create(
            triggers={'en': ['I have a really bad headache']},
            answers={'en': 'Headaches are often caused by dehydration or stress.'},
        )
        res = self._post('I have a really bad headache today')
        self.assertTrue(res.data['content'].startswith('Headaches are often caused'))

    def test_below_threshold_falls_through_to_unclear(self):
        ResponseTemplate.objects.create(
            triggers={'en': ['headache']},
            answers={'en': 'Headaches are often caused by dehydration or stress.'},
        )
        res = self._post('my knee has been aching for three weeks after running')
        self.assertEqual(res.data['content'], assistant_message('assistant_unclear', 'en'))

    def test_correct_template_selected_among_several(self):
        ResponseTemplate.objects.create(
            triggers={'en': ['headache']}, answers={'en': 'Headache answer.'},
        )
        ResponseTemplate.objects.create(
            triggers={'en': ['sore throat']}, answers={'en': 'Sore throat answer.'},
        )
        res = self._post('I have a headache')
        self.assertTrue(res.data['content'].startswith('Headache answer.'))

    def test_inactive_template_is_never_matched(self):
        ResponseTemplate.objects.create(
            triggers={'en': ['headache']},
            answers={'en': 'Should never be returned.'},
            is_active=False,
        )
        res = self._post('headache')
        self.assertEqual(res.data['content'], assistant_message('assistant_unclear', 'en'))

    def test_template_matched_by_patient_language_only(self):
        ResponseTemplate.objects.create(
            triggers={'ru': ['головная боль']},
            answers={'ru': 'Головная боль часто вызвана обезвоживанием.'},
        )
        # self.patient.language stays the 'en' default — a Russian-only
        # template must not match, even though the text matches its trigger.
        res = self._post('головная боль')
        self.assertEqual(res.data['content'], assistant_message('assistant_unclear', 'en'))

    def test_template_with_specialization_triggers_doctor_search(self):
        cardiologist = create_verified_doctor(
            'cardio2@test.com', 'Aysel', 'Aliyeva', specialization='cardiology'
        )
        ResponseTemplate.objects.create(
            triggers={'en': ['irregular heartbeat']},
            answers={'en': 'An irregular heartbeat should be checked by a specialist.'},
            specialization='cardiology',
        )
        res = self._post('irregular heartbeat')
        self.assertEqual(len(res.data['suggested_doctors']), 1)
        card = res.data['suggested_doctors'][0]
        self.assertEqual(card['id'], str(cardiologist.id))
        self.assertEqual(card['specialization_display'], 'Cardiology')
        saved = self.conversation.messages.get(role=Message.ROLE_ASSISTANT)
        self.assertEqual(saved.suggested_doctors, res.data['suggested_doctors'])

    def test_template_without_specialization_has_no_suggested_doctors(self):
        ResponseTemplate.objects.create(
            triggers={'en': ['headache']}, answers={'en': 'Headache answer.'},
        )
        res = self._post('headache')
        self.assertEqual(res.data['suggested_doctors'], [])

    def test_conversation_updated_at_bumped(self):
        ResponseTemplate.objects.create(
            triggers={'en': ['headache']}, answers={'en': 'Headache answer.'},
        )
        before = Conversation.objects.get(pk=self.conversation.pk).updated_at
        self._post('headache')
        after = Conversation.objects.get(pk=self.conversation.pk).updated_at
        self.assertGreater(after, before)

    def test_content_is_encrypted_at_rest(self):
        self._post('my secret symptom')
        message = self.conversation.messages.get(role=Message.ROLE_USER)
        self.assertEqual(message.content, 'my secret symptom')
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT content FROM assistant_message WHERE id = %s', [str(message.pk)]
            )
            raw = cursor.fetchone()[0]
        self.assertNotEqual(raw, 'my secret symptom')
        self.assertNotIn('secret', raw)

    def test_pipeline_failure_returns_503(self):
        with patch('apps.assistant.service._match_template', side_effect=RuntimeError('db down')):
            res = self._post('headache')
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(res.data['detail'], assistant_message('assistant_unavailable', 'en'))


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
            'ganja@test.com', 'Gunel', 'Ganja', specialization='cardiology', city='ganja'
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
        self.assertEqual(results[0]['city'], 'ganja')
        self.assertEqual(results[0]['city_display'], 'Ganja')

    def test_city_filter_accepts_free_text_alias(self):
        # 'Gəncə' (Azerbaijani spelling) must resolve to the same canonical
        # key as 'ganja' — see apps.core.i18n.resolve_city_key.
        results = search_doctors('cardiology', city='Gəncə')
        self.assertEqual([r['first_name'] for r in results], ['Gunel'])

    def test_city_filter_unresolvable_value_returns_empty(self):
        results = search_doctors('cardiology', city='Atlantis')
        self.assertEqual(results, [])

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
