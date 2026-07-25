from unittest.mock import patch

from django.test import override_settings
from rest_framework import status

from apps.assistant.models import Conversation, Message

from .base import (
    AssistantTestCase,
    FakeResponse,
    FakeTextBlock,
    FakeToolUseBlock,
    conversation_url,
)

SAMPLE_SUMMARY_INPUT = {
    'summary_text': 'The patient described chest tightness for two days.',
    'possible_conditions': [
        {'name': 'Costochondritis', 'likelihood': 'medium', 'note': 'Common, benign cause.'},
        {'name': 'Angina', 'likelihood': 'low', 'note': 'Should be ruled out by a doctor.'},
    ],
    'urgency': 'soon',
    'recommended_specialization': 'cardiology',
}


def summary_url(pk):
    return f'{conversation_url(pk)}summary/'


def fake_summary_response(input_data=None):
    return FakeResponse([
        FakeToolUseBlock('record_summary', input_data or SAMPLE_SUMMARY_INPUT, id='toolu_summary_1')
    ])


class ConversationSummaryTests(AssistantTestCase):
    def setUp(self):
        super().setUp()
        self.conversation = Conversation.objects.create(patient=self.patient)
        Message.objects.create(
            conversation=self.conversation, role=Message.ROLE_USER, content='I have chest pain'
        )
        Message.objects.create(
            conversation=self.conversation, role=Message.ROLE_ASSISTANT, content='How long has it lasted?'
        )

    def test_generates_and_persists_structured_summary(self):
        with patch(
            'apps.assistant.service.call_assistant', return_value=fake_summary_response()
        ) as chat:
            res = self.client.post(summary_url(self.conversation.pk))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['summary'], SAMPLE_SUMMARY_INPUT)
        self.assertIsNotNone(res.data['summary_generated_at'])
        chat.assert_called_once()

        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.summary, SAMPLE_SUMMARY_INPUT)
        self.assertIsNotNone(self.conversation.summary_generated_at)

    def test_call_assistant_forces_the_record_summary_tool(self):
        with patch(
            'apps.assistant.service.call_assistant', return_value=fake_summary_response()
        ) as chat:
            self.client.post(summary_url(self.conversation.pk))
        called_args, called_kwargs = chat.call_args
        self.assertEqual(called_kwargs.get('tool_choice'), {'type': 'tool', 'name': 'record_summary'})
        tools_arg = called_args[2]
        self.assertEqual(tools_arg[0]['name'], 'record_summary')

    def test_uses_the_full_conversation_not_just_recent_messages(self):
        # _RECENT_MESSAGES_LIMIT in handle_user_message is 10 — the summary
        # must NOT apply that same cap.
        for i in range(15):
            Message.objects.create(
                conversation=self.conversation, role=Message.ROLE_USER, content=f'extra detail {i}'
            )
        total = self.conversation.messages.count()
        self.assertGreater(total, 10)

        with patch(
            'apps.assistant.service.call_assistant', return_value=fake_summary_response()
        ) as chat:
            self.client.post(summary_url(self.conversation.pk))
        sent_messages = chat.call_args[0][1]
        self.assertEqual(len(sent_messages), total)

    def test_summary_system_prompt_requests_patient_language(self):
        self.patient.language = 'fr'
        self.patient.save(update_fields=['language'])
        with patch(
            'apps.assistant.service.call_assistant', return_value=fake_summary_response()
        ) as chat:
            self.client.post(summary_url(self.conversation.pk))
        system_prompt = chat.call_args[0][0]
        self.assertIn('language code: fr', system_prompt)

    def test_not_enough_context_yet_returns_400(self):
        # No assistant messages at all — only user messages.
        empty = Conversation.objects.create(patient=self.patient)
        Message.objects.create(conversation=empty, role=Message.ROLE_USER, content='hi')

        with patch('apps.assistant.service.call_assistant') as chat:
            res = self.client.post(summary_url(empty.pk))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data['code'], 'validation_error')
        chat.assert_not_called()
        empty.refresh_from_db()
        self.assertIsNone(empty.summary)

    def test_foreign_conversation_returns_404_not_403(self):
        foreign = Conversation.objects.create(patient=self.other_patient)
        Message.objects.create(conversation=foreign, role=Message.ROLE_ASSISTANT, content='hi')
        res = self.client.post(summary_url(foreign.pk))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_pipeline_failure_returns_503(self):
        with patch('apps.assistant.service.call_assistant', side_effect=RuntimeError('api down')):
            res = self.client.post(summary_url(self.conversation.pk))
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.conversation.refresh_from_db()
        self.assertIsNone(self.conversation.summary)

    def test_missing_tool_use_block_returns_503(self):
        with patch(
            'apps.assistant.service.call_assistant',
            return_value=FakeResponse([FakeTextBlock('I did not call the tool.')]),
        ):
            res = self.client.post(summary_url(self.conversation.pk))
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @override_settings(ANTHROPIC_API_KEY='', ASSISTANT_ENCRYPTION_KEY='')
    def test_feature_disabled_returns_503(self):
        res = self.client.post(summary_url(self.conversation.pk))
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_doctor_cannot_access_summary_endpoint(self):
        from django.contrib.auth import get_user_model
        doctor = get_user_model().objects.create_user(
            email='doc-summary@test.com', password='Pass1234', role='doctor'
        )
        self.client.force_authenticate(doctor)
        res = self.client.post(summary_url(self.conversation.pk))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_summary_endpoint_requires_authentication(self):
        self.client.force_authenticate(None)
        res = self.client.post(summary_url(self.conversation.pk))
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_regenerating_summary_overwrites_previous_one(self):
        with patch('apps.assistant.service.call_assistant', return_value=fake_summary_response()):
            self.client.post(summary_url(self.conversation.pk))
        self.conversation.refresh_from_db()
        first_generated_at = self.conversation.summary_generated_at

        new_input = dict(SAMPLE_SUMMARY_INPUT, urgency='emergency')
        with patch(
            'apps.assistant.service.call_assistant',
            return_value=fake_summary_response(new_input),
        ):
            res = self.client.post(summary_url(self.conversation.pk))
        self.assertEqual(res.data['summary']['urgency'], 'emergency')
        self.conversation.refresh_from_db()
        self.assertGreaterEqual(self.conversation.summary_generated_at, first_generated_at)
