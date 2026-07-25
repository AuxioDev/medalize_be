from django.conf import settings
from django.core.checks import Error, Warning, register

_GENERATE_HINT = (
    'Generate one with: python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())" and set it in .env.'
)


@register()
def check_assistant_encryption_key(app_configs, **kwargs):
    from cryptography.fernet import Fernet

    key = getattr(settings, 'ASSISTANT_ENCRYPTION_KEY', '')
    if not key:
        return [
            Warning(
                'ASSISTANT_ENCRYPTION_KEY is not set.',
                hint=(
                    'This disables the AI assistant, as documented — but '
                    'apps.messaging.Message.body uses the same EncryptedTextField '
                    'and is not optional, so patient-doctor messaging will fail '
                    'with a 500 on every send until this is set. ' + _GENERATE_HINT
                ),
                id='assistant.W001',
            )
        ]
    try:
        Fernet(key.encode())
    except ValueError:
        return [
            Error(
                'ASSISTANT_ENCRYPTION_KEY is not a valid Fernet key.',
                hint='Must be 32 url-safe base64-encoded bytes. ' + _GENERATE_HINT,
                id='assistant.E001',
            )
        ]
    return []
