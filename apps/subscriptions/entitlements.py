"""Single source of truth for what an account's current subscription grants.

Every enforcement point (doctor search/detail/slots, the AI bot's doctor
search, booking, workplace creation, chat-thread creation, stats, and the
hospital dashboard) reads through this module instead of re-deriving
status/plan logic locally.
"""
from django.db.models import Case, IntegerField, Value, When

from .models import Subscription
from .plans import LIMITS_BY_ROLE, PLAN_PRO, ROLE_DOCTOR, TRIAL_ROLES

# past_due is intentionally still entitled: the 14-day grace window (see
# plans.GRACE_DAYS) keeps a lapsed account fully visible and working while
# it renews. Only `expired` (grace ran out) and `pending` (trial never
# started for a doctor — e.g. not yet verified — or a hospital that was
# approved but never checked out) are excluded.
ENTITLED_STATUSES = (
    Subscription.STATUS_TRIALING,
    Subscription.STATUS_ACTIVE,
    Subscription.STATUS_PAST_DUE,
)


def entitled_doctor_filter():
    """Kwargs for a User queryset filter/get, used alongside the existing
    ``doctor_profile__is_verified=True`` check at every doctor-discovery
    call site. Excludes doctors with no current entitlement (expired, or
    never started a trial)."""
    return {'subscription__status__in': ENTITLED_STATUSES}


def effective_plan_key(subscription, role=ROLE_DOCTOR):
    """The apps.subscriptions.plans.LIMITS_BY_ROLE[role] key a subscription
    currently grants.

    Doctor (trial-eligible) path unchanged: past_due deliberately keeps the
    doctor's LAST plan — their own paid tier, or 'trial' if they lapsed
    straight out of the trial before ever paying — rather than dropping them
    to the cheapest tier during the grace window.

    Non-trial roles (hospitals) can never be 'trialing' — start_trial_on_*
    is never wired up for them, see signals.py — so ACTIVE/PAST_DUE simply
    return the paid plan they hold, or 'none' if paid access was never
    established (e.g. the very first checkout never completed).
    """
    if subscription is None:
        return 'none'
    if role not in TRIAL_ROLES:
        if subscription.status in (Subscription.STATUS_ACTIVE, Subscription.STATUS_PAST_DUE):
            return subscription.plan or 'none'
        return 'none'
    if subscription.status == Subscription.STATUS_TRIALING:
        return 'trial'
    if subscription.status in (Subscription.STATUS_ACTIVE, Subscription.STATUS_PAST_DUE):
        return subscription.plan or 'trial'
    return 'none'


def limits_for(user):
    try:
        subscription = user.subscription
    except Subscription.DoesNotExist:
        subscription = None
    return LIMITS_BY_ROLE[user.role][effective_plan_key(subscription, user.role)]


def subscription_summary(subscription, role=ROLE_DOCTOR):
    """The status block shared by GET /doctor/subscription/,
    GET /hospital/subscription/, MeSerializer, and build_login_payload — one
    shape everywhere so the mobile app never has to reconcile two slightly
    different representations of the same subscription. ``subscription`` may
    be None (e.g. a row that predates this app and hasn't been backfilled
    yet) — treated the same as effective_plan_key(None): no entitlement."""
    plan_key = effective_plan_key(subscription, role)
    return {
        'status': subscription.status if subscription else Subscription.STATUS_PENDING,
        'plan': subscription.plan if subscription else '',
        'effective_plan': plan_key,
        'trial_ends_at': subscription.trial_ends_at if subscription else None,
        'current_period_end': subscription.current_period_end if subscription else None,
        'grace_ends_at': subscription.grace_ends_at if subscription else None,
        'limits': LIMITS_BY_ROLE[role][plan_key],
    }


def is_promoted(user):
    return limits_for(user)['promoted']


def promoted_rank_case():
    """Case/When ordering annotation for DoctorListView: 0 sorts a promoted
    doctor (trialing, or active/past_due on the Pro plan) ahead of everyone
    else; 1 otherwise. Mirrors effective_plan_key's 'promoted' rule at the
    SQL level so ranking doesn't require pulling every row into Python.
    Doctor-only — hospitals never appear in this ranking."""
    return Case(
        When(subscription__status=Subscription.STATUS_TRIALING, then=Value(0)),
        When(
            subscription__status__in=(
                Subscription.STATUS_ACTIVE, Subscription.STATUS_PAST_DUE,
            ),
            subscription__plan=PLAN_PRO,
            then=Value(0),
        ),
        default=Value(1),
        output_field=IntegerField(),
    )
