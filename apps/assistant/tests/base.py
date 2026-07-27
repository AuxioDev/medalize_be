from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

User = get_user_model()

TEST_ENCRYPTION_KEY = Fernet.generate_key().decode()

CONVERSATIONS_URL = '/api/assistant/conversations/'
TEMPLATES_URL = '/api/assistant/templates/'


def conversation_url(pk):
    return f'{CONVERSATIONS_URL}{pk}/'


def messages_url(pk):
    return f'{CONVERSATIONS_URL}{pk}/messages/'


def flag_url(pk):
    return f'/api/assistant/messages/{pk}/flag/'


@override_settings(ASSISTANT_ENCRYPTION_KEY=TEST_ENCRYPTION_KEY)
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
