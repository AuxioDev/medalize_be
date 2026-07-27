import uuid

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status

from apps.assistant.i18n import assistant_message
from apps.assistant.models import Conversation, Message, ResponseTemplate

from .base import (
    CONVERSATIONS_URL,
    TEMPLATES_URL,
    AssistantTestCase,
    conversation_url,
    flag_url,
    messages_url,
)

User = get_user_model()


class ConversationCrudTests(AssistantTestCase):
    def test_create_conversation_returns_201_with_id(self):
        res = self.client.post(CONVERSATIONS_URL, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn('id', res.data)
        self.assertTrue(
            Conversation.objects.filter(pk=res.data['id'], patient=self.patient).exists()
        )

    def test_list_returns_only_own_conversations(self):
        mine = Conversation.objects.create(patient=self.patient)
        Conversation.objects.create(patient=self.other_patient)
        res = self.client.get(CONVERSATIONS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = [c['id'] for c in res.data['results']]
        self.assertEqual(ids, [str(mine.pk)])

    def test_list_includes_last_message_preview(self):
        conversation = Conversation.objects.create(patient=self.patient)
        Message.objects.create(
            conversation=conversation, role=Message.ROLE_USER, content='I have a headache'
        )
        res = self.client.get(CONVERSATIONS_URL)
        self.assertEqual(
            res.data['results'][0]['last_message_preview'], 'I have a headache'
        )

    def test_detail_returns_messages(self):
        conversation = Conversation.objects.create(patient=self.patient)
        Message.objects.create(
            conversation=conversation, role=Message.ROLE_USER, content='Hello doc'
        )
        res = self.client.get(conversation_url(conversation.pk))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data['messages']), 1)
        self.assertEqual(res.data['messages'][0]['content'], 'Hello doc')

    def test_detail_of_foreign_conversation_returns_404(self):
        conversation = Conversation.objects.create(patient=self.other_patient)
        res = self.client.get(conversation_url(conversation.pk))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_own_conversation(self):
        conversation = Conversation.objects.create(patient=self.patient)
        res = self.client.delete(conversation_url(conversation.pk))
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Conversation.objects.filter(pk=conversation.pk).exists())

    def test_delete_foreign_conversation_returns_404(self):
        conversation = Conversation.objects.create(patient=self.other_patient)
        res = self.client.delete(conversation_url(conversation.pk))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Conversation.objects.filter(pk=conversation.pk).exists())

    def test_doctor_role_gets_403(self):
        doctor = User.objects.create_user(
            email='doc@test.com', password='Pass1234', role='doctor'
        )
        self.client.force_authenticate(doctor)
        res = self.client.get(CONVERSATIONS_URL)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_gets_401(self):
        self.client.force_authenticate(None)
        res = self.client.get(CONVERSATIONS_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class TemplateOptionListTests(AssistantTestCase):
    def setUp(self):
        super().setUp()
        self.headache = ResponseTemplate.objects.create(
            specialization='general_practice',
            triggers={'en': ['headache', 'my head hurts'], 'ru': ['головная боль']},
            answers={'en': 'Rest and drink water.', 'ru': 'Отдохните и пейте воду.'},
        )
        self.cardio = ResponseTemplate.objects.create(
            specialization='cardiology',
            triggers={'en': ['heart palpitations'], 'ru': ['учащённое сердцебиение']},
            answers={'en': 'Try to relax.', 'ru': 'Постарайтесь расслабиться.'},
        )
        self.inactive = ResponseTemplate.objects.create(
            specialization='dermatology',
            triggers={'en': ['inactive topic']},
            answers={'en': 'Should never be listed.'},
            is_active=False,
        )

    def test_lists_only_active_templates(self):
        res = self.client.get(TEMPLATES_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = {t['id'] for t in res.data}
        self.assertEqual(ids, {str(self.headache.id), str(self.cardio.id)})

    def test_label_is_first_trigger_capitalized(self):
        res = self.client.get(TEMPLATES_URL)
        by_id = {t['id']: t for t in res.data}
        self.assertEqual(by_id[str(self.headache.id)]['label'], 'Headache')
        self.assertEqual(by_id[str(self.cardio.id)]['label'], 'Heart palpitations')

    def test_label_is_localized_to_patient_language(self):
        self.patient.language = 'ru'
        self.patient.save(update_fields=['language'])
        res = self.client.get(TEMPLATES_URL)
        by_id = {t['id']: t for t in res.data}
        self.assertEqual(by_id[str(self.headache.id)]['label'], 'Головная боль')

    def test_specialization_display_included(self):
        res = self.client.get(TEMPLATES_URL)
        by_id = {t['id']: t for t in res.data}
        self.assertEqual(by_id[str(self.cardio.id)]['specialization'], 'cardiology')
        self.assertTrue(by_id[str(self.cardio.id)]['specialization_display'])

    def test_doctor_role_gets_403(self):
        doctor = User.objects.create_user(email='doc2@test.com', password='Pass1234', role='doctor')
        self.client.force_authenticate(doctor)
        res = self.client.get(TEMPLATES_URL)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


class MessageValidationTests(AssistantTestCase):
    def setUp(self):
        super().setUp()
        self.conversation = Conversation.objects.create(patient=self.patient)

    def test_empty_content_returns_400(self):
        res = self.client.post(messages_url(self.conversation.pk), {'content': '  '}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_content_returns_400(self):
        res = self.client.post(messages_url(self.conversation.pk), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_too_long_content_returns_400(self):
        res = self.client.post(
            messages_url(self.conversation.pk), {'content': 'x' * 4001}, format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Message.objects.count(), 0)

    def test_send_to_foreign_conversation_returns_404(self):
        foreign = Conversation.objects.create(patient=self.other_patient)
        res = self.client.post(messages_url(foreign.pk), {'content': 'hi'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class FlagMessageTests(AssistantTestCase):
    def setUp(self):
        super().setUp()
        self.conversation = Conversation.objects.create(patient=self.patient)
        self.assistant_msg = Message.objects.create(
            conversation=self.conversation,
            role=Message.ROLE_ASSISTANT,
            content='You might want to see a cardiologist.',
        )
        self.user_msg = Message.objects.create(
            conversation=self.conversation, role=Message.ROLE_USER, content='chest pain'
        )

    def test_flag_assistant_message(self):
        res = self.client.post(
            flag_url(self.assistant_msg.pk), {'reason': 'Sounded like a diagnosis'},
            format='json',
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assistant_msg.refresh_from_db()
        self.assertTrue(self.assistant_msg.flagged)
        self.assertEqual(self.assistant_msg.flag_reason, 'Sounded like a diagnosis')

    def test_flag_without_reason_is_ok(self):
        res = self.client.post(flag_url(self.assistant_msg.pk), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assistant_msg.refresh_from_db()
        self.assertTrue(self.assistant_msg.flagged)
        self.assertEqual(self.assistant_msg.flag_reason, '')

    def test_flag_is_idempotent(self):
        self.client.post(flag_url(self.assistant_msg.pk), {'reason': 'first'}, format='json')
        res = self.client.post(flag_url(self.assistant_msg.pk), {'reason': 'second'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assistant_msg.refresh_from_db()
        self.assertTrue(self.assistant_msg.flagged)
        self.assertEqual(self.assistant_msg.flag_reason, 'second')

    def test_flag_user_message_returns_404(self):
        res = self.client.post(flag_url(self.user_msg.pk), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_flag_foreign_message_returns_404(self):
        self.client.force_authenticate(self.other_patient)
        res = self.client.post(flag_url(self.assistant_msg.pk), {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class ThrottleTests(AssistantTestCase):
    def test_assistant_message_throttled_after_30_per_hour(self):
        conversation = Conversation.objects.create(patient=self.patient)
        for _ in range(30):
            res = self.client.post(
                messages_url(conversation.pk), {'content': 'hello'}, format='json'
            )
            self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        res = self.client.post(
            messages_url(conversation.pk), {'content': 'hello'}, format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_throttle_does_not_apply_to_conversation_list(self):
        conversation = Conversation.objects.create(patient=self.patient)
        for _ in range(30):
            self.client.post(
                messages_url(conversation.pk), {'content': 'hello'}, format='json'
            )
        res = self.client.get(CONVERSATIONS_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)


@override_settings(ASSISTANT_ENCRYPTION_KEY='')
class FeatureDisabledTests(AssistantTestCase):
    """Encryption is the only requirement now — chat is local template
    matching, no external AI API involved anywhere in this app."""

    def test_send_message_returns_503(self):
        res = self.client.post(messages_url(uuid.uuid4()), {'content': 'hi'}, format='json')
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(res.data['detail'], assistant_message('assistant_unavailable', 'en'))

    def test_all_endpoints_return_503(self):
        pk = uuid.uuid4()
        checks = [
            self.client.get(CONVERSATIONS_URL),
            self.client.post(CONVERSATIONS_URL, {}, format='json'),
            self.client.get(conversation_url(pk)),
            self.client.delete(conversation_url(pk)),
            self.client.post(messages_url(pk), {'content': 'hi'}, format='json'),
            self.client.post(flag_url(pk), {}, format='json'),
            self.client.get(TEMPLATES_URL),
        ]
        for res in checks:
            self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_unavailable_message_is_localized(self):
        self.patient.language = 'ru'
        self.patient.save(update_fields=['language'])
        res = self.client.post(messages_url(uuid.uuid4()), {'content': 'hi'}, format='json')
        self.assertEqual(res.data['detail'], assistant_message('assistant_unavailable', 'ru'))
