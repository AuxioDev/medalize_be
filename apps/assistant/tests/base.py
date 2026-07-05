from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

User = get_user_model()

TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()

CONVERSATIONS_URL = '/api/assistant/conversations/'


def conversation_url(pk):
    return f'{CONVERSATIONS_URL}{pk}/'


def messages_url(pk):
    return f'{CONVERSATIONS_URL}{pk}/messages/'


def flag_url(pk):
    return f'/api/assistant/messages/{pk}/flag/'


class FakeTextBlock:
    type = 'text'

    def __init__(self, text):
        self.text = text


class FakeToolUseBlock:
    type = 'tool_use'

    def __init__(self, name, input, id='toolu_test_1'):
        self.name = name
        self.input = input
        self.id = id


class FakeResponse:
    def __init__(self, blocks):
        self.content = blocks


def fake_text_response(text):
    return FakeResponse([FakeTextBlock(text)])


def fake_tool_response(name='search_doctors', input=None, id='toolu_test_1'):
    return FakeResponse([FakeToolUseBlock(name, input or {}, id=id)])


@override_settings(
    ANTHROPIC_API_KEY='test-anthropic-key',
    ASSISTANT_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY,
)
class AssistantTestCase(APITestCase):
    """Authenticated patient with the assistant feature enabled and a valid
    test-only Fernet key so encrypted content round-trips in the test DB."""

    def setUp(self):
        cache.clear()
        self.patient = User.objects.create_user(
            email='patient@test.com', password='Pass1234', role='patient',
            first_name='Jane', last_name='Doe',
        )
        self.other_patient = User.objects.create_user(
            email='other@test.com', password='Pass1234', role='patient',
            first_name='Olga', last_name='Ivanova',
        )
        self.client.force_authenticate(self.patient)
