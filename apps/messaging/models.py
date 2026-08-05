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


class Block(models.Model):
    """Either participant of a patient/doctor pair can block the other,
    independent of any particular Thread — storage is keyed on the two
    users, not the thread, so a block still applies even before a thread
    exists (see ThreadListCreateView.post's block check gating new-thread
    creation). Once blocked, the blocked party can't send new messages in
    any existing thread with `blocked`, or open a new one (checked in both
    directions — either side may have blocked the other — by
    apps.messaging.views)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    blocker = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='blocks_made',
    )
    blocked = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='blocks_received',
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['blocker', 'blocked'], name='unique_block_per_pair'),
        ]

    def __str__(self):
        return f'{self.blocker.email} blocked {self.blocked.email}'


class Report(models.Model):
    """A patient or doctor flagging a thread — or one specific message in
    it — for admin follow-up. Mirrors the shape of
    apps.assistant.views.MessageFlagView (the existing patient-facing flag
    mechanism for bad AI replies): a reason plus optional free-text detail,
    submitted by POST and reviewed manually, no automated action taken.
    Unlike that flag (a boolean on the AI Message itself, since only ever
    one flag makes sense there), reports here are a separate model — a
    thread can be reported more than once, by either participant, each with
    its own reason, and admins need to filter/search that history."""

    REASON_SPAM = 'spam'
    REASON_HARASSMENT = 'harassment'
    REASON_INAPPROPRIATE = 'inappropriate_content'
    REASON_FRAUD = 'fraud'
    REASON_OTHER = 'other'
    REASON_CHOICES = [
        (REASON_SPAM, 'Spam'),
        (REASON_HARASSMENT, 'Harassment'),
        (REASON_INAPPROPRIATE, 'Inappropriate content'),
        (REASON_FRAUD, 'Fraud or scam'),
        (REASON_OTHER, 'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name='reports')
    # Null when the report is about the thread/counterpart in general rather
    # than one specific message.
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, null=True, blank=True, related_name='reports',
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='messaging_reports_filed',
    )
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        target = f'message {self.message_id}' if self.message_id else f'thread {self.thread_id}'
        return f'{self.reporter.email} reported {target} ({self.reason})'
