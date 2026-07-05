import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

CONVERSATION_TTL_DAYS = 90


@shared_task
def delete_expired_conversations():
    """Deletes assistant conversations (and, via CASCADE, their messages) not
    updated for CONVERSATION_TTL_DAYS. Chat content is medical data — it must
    not be retained indefinitely."""
    from .models import Conversation

    cutoff = timezone.now() - timedelta(days=CONVERSATION_TTL_DAYS)
    deleted_count, _ = Conversation.objects.filter(updated_at__lt=cutoff).delete()
    if deleted_count:
        logger.info('Deleted %s expired assistant conversations/messages', deleted_count)
    return deleted_count
