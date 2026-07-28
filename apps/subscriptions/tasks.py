import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .models import Subscription
from .plans import GRACE_DAYS

logger = logging.getLogger(__name__)


@shared_task
def sweep_subscriptions():
    """Hourly status sweep — the subscription equivalent of
    apps.notifications.tasks.expire_stale_pending_appointments: collect ids
    per transition, bulk `.update()`, then fan out one notification task per
    affected account (doctor or hospital). Order matters: a subscription
    that just moved into past_due this pass is not also considered for the
    pre-deadline reminders below (they only match
    STATUS_TRIALING/STATUS_ACTIVE). Hospitals are never STATUS_TRIALING (see
    plans.TRIAL_ROLES), so _transition_trial_to_past_due and the trial
    reminder branches below simply never match a hospital's row — no
    role-branching needed here."""
    now = timezone.now()
    _transition_trial_to_past_due(now)
    _transition_active_to_past_due(now)
    _expire_past_due(now)
    _send_pre_deadline_reminders(now)


def _transition_trial_to_past_due(now):
    ids = list(
        Subscription.objects
        .filter(status=Subscription.STATUS_TRIALING, trial_ends_at__lte=now)
        .values_list('id', flat=True)
    )
    if not ids:
        return
    Subscription.objects.filter(id__in=ids).update(
        status=Subscription.STATUS_PAST_DUE,
        grace_ends_at=now + timedelta(days=GRACE_DAYS),
        last_reminder_stage=Subscription.REMINDER_NONE,
        updated_at=now,
    )
    _notify(ids, 'trial_ended')


def _transition_active_to_past_due(now):
    ids = list(
        Subscription.objects
        .filter(status=Subscription.STATUS_ACTIVE, current_period_end__lte=now)
        .values_list('id', flat=True)
    )
    if not ids:
        return
    Subscription.objects.filter(id__in=ids).update(
        status=Subscription.STATUS_PAST_DUE,
        grace_ends_at=now + timedelta(days=GRACE_DAYS),
        last_reminder_stage=Subscription.REMINDER_NONE,
        updated_at=now,
    )
    _notify(ids, 'subscription_expiring')


def _expire_past_due(now):
    """Grace period exhausted — this is the one transition that actually
    hides the account (see apps.subscriptions.entitlements.ENTITLED_STATUSES,
    which excludes STATUS_EXPIRED)."""
    ids = list(
        Subscription.objects
        .filter(status=Subscription.STATUS_PAST_DUE, grace_ends_at__lte=now)
        .values_list('id', flat=True)
    )
    if not ids:
        return
    Subscription.objects.filter(id__in=ids).update(
        status=Subscription.STATUS_EXPIRED, updated_at=now,
    )
    _notify(ids, 'subscription_expired')


def _send_pre_deadline_reminders(now):
    """T-3/T-1 reminders ahead of the trial or the current paid period
    ending. T-0 itself is not a reminder — it's the transition into
    past_due handled above. `last_reminder_stage` dedupes across hourly
    sweep runs so a subscription is never reminded twice for the same
    stage."""
    _remind(
        Subscription.objects.filter(
            status=Subscription.STATUS_TRIALING,
            trial_ends_at__lte=now + timedelta(days=3), trial_ends_at__gt=now + timedelta(days=1),
            last_reminder_stage=Subscription.REMINDER_NONE,
        ),
        stage=Subscription.REMINDER_T3, template='trial_ending',
    )
    _remind(
        Subscription.objects.filter(
            status=Subscription.STATUS_TRIALING,
            trial_ends_at__lte=now + timedelta(days=1),
            last_reminder_stage__in=(Subscription.REMINDER_NONE, Subscription.REMINDER_T3),
        ),
        stage=Subscription.REMINDER_T1, template='trial_ending',
    )
    _remind(
        Subscription.objects.filter(
            status=Subscription.STATUS_ACTIVE,
            current_period_end__lte=now + timedelta(days=3), current_period_end__gt=now + timedelta(days=1),
            last_reminder_stage=Subscription.REMINDER_NONE,
        ),
        stage=Subscription.REMINDER_T3, template='subscription_expiring',
    )
    _remind(
        Subscription.objects.filter(
            status=Subscription.STATUS_ACTIVE,
            current_period_end__lte=now + timedelta(days=1),
            last_reminder_stage__in=(Subscription.REMINDER_NONE, Subscription.REMINDER_T3),
        ),
        stage=Subscription.REMINDER_T1, template='subscription_expiring',
    )


def _remind(queryset, stage, template):
    ids = list(queryset.values_list('id', flat=True))
    if not ids:
        return
    Subscription.objects.filter(id__in=ids).update(last_reminder_stage=stage)
    _notify(ids, template)


def _notify(subscription_ids, template_key):
    from apps.notifications.tasks import send_subscription_notification
    user_ids = list(
        Subscription.objects.filter(id__in=subscription_ids).values_list('user_id', flat=True)
    )
    for user_id in user_ids:
        try:
            send_subscription_notification.delay(str(user_id), template_key)
        except Exception:
            logger.exception(
                'Failed to enqueue %s notification for account %s', template_key, user_id,
            )
