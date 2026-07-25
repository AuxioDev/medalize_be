import uuid

from django.conf import settings
from django.db import models

from apps.assistant.fields import EncryptedTextField


class Thread(models.Model):
    """One conversation between a specific patient and doctor pair. Creation
    requires a real shared appointment history (enforced in the view, not
    here) and is idempotent — at most one thread ever exists per pair."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='message_threads_as_patient',
    )
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='message_threads_as_doctor',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['patient', 'doctor'], name='unique_thread_per_pair'),
        ]

    def __str__(self):
        return f'Thread {self.id} ({self.patient.email} <-> {self.doctor.email})'


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    # Chat content is medical data — same field-level encryption as
    # apps/assistant/models.py::Message.content, reusing the same field class
    # (and therefore the same ASSISTANT_ENCRYPTION_KEY) rather than a second
    # encryption scheme.
    body = EncryptedTextField()
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'message {self.id} in thread {self.thread_id}'
