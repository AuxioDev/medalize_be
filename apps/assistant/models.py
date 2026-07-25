import uuid

from django.conf import settings
from django.db import models

from .fields import EncryptedTextField


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='assistant_conversations',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Structured, patient-shareable report generated on demand by
    # generate_summary() — see apps/assistant/service.py. Raw dict shaped by
    # the record_summary tool schema (_SUMMARY_TOOL), not a fixed set of
    # columns, same JSONField approach as Message.suggested_doctors.
    summary = models.JSONField(null=True, blank=True)
    summary_generated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'Conversation {self.id} ({self.patient.email})'


class Message(models.Model):
    ROLE_USER = 'user'
    ROLE_ASSISTANT = 'assistant'
    ROLE_CHOICES = [(ROLE_USER, 'User'), (ROLE_ASSISTANT, 'Assistant')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name='messages'
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = EncryptedTextField()
    # Compact doctor cards returned by the search_doctors tool, denormalized so
    # the mobile app can render them without an extra /doctors/ round-trip.
    suggested_doctors = models.JSONField(default=list, blank=True)
    flagged = models.BooleanField(default=False)
    flag_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'{self.role} message {self.id}'
