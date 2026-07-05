"""Field-level encryption for patient chat content.

Messages exchanged with the AI assistant are medical data, so the ``content``
column is encrypted at rest with Fernet (symmetric AES, key from
``ASSISTANT_ENCRYPTION_KEY``). The key is only required at runtime for actual
reads/writes — migrations work without it.
"""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


def _fernet():
    return Fernet(settings.ASSISTANT_ENCRYPTION_KEY.encode())


class EncryptedTextField(models.TextField):
    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == '':
            return value
        return _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None or value == '':
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            return ''
