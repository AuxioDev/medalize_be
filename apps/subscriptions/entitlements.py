"""Single source of truth for what a doctor's subscription currently grants.

Every enforcement point (doctor search/detail/slots, the AI bot's doctor
search, booking, workplace creation, chat-thread creation, stats) reads
through this module instead of re-deriving status/plan logic locally.
"""
from django.db.models import Case, IntegerField, Value, When

from .models import DoctorSubscription
from .plans import PLAN_LIMITS, PLAN_PRO

# past_due is intentionally still entitled: the 14-day grace window (see
# plans.GRACE_DAYS) keeps a lapsed doctor fully visible and working while
# they renew. Only `expired` (grace ran out) and `pending` (trial never
# started — e.g. not yet verified) are excluded.
ENTITLED_STATUSES = (
    DoctorSubscription.STATUS_TRIALING,
    DoctorSubscription.STATUS_ACTIVE,
    DoctorSubscription.STATUS_PAST_DUE,
)


def entitled_doctor_filter():
    """Kwargs for a User queryset filter/get, used alongside the existing
    ``doctor_profile__is_verified=True`` check at every doctor-discovery
    call site. Excludes doctors with no current entitlement (expired, or
    never started a trial)."""
    return {'subscription__status__in': ENTITLED_STATUSES}


def effective_plan_key(subscription):
    """The apps.subscriptions.plans.PLAN_LIMITS key a subscription currently
    grants. past_due deliberately keeps the doctor's LAST plan — their own
    paid tier, or 'trial' if they lapsed straight out of the trial before
    ever paying — rather than dropping them to the cheapest tier during the
    grace window."""
    if subscription is None:
        return 'none'
    if subscription.status == DoctorSubscription.STATUS_TRIALING:
        return 'trial'
    if subscription.status in (DoctorSubscription.STATUS_ACTIVE, DoctorSubscription.STATUS_PAST_DUE):
        return subscription.plan or 'trial'
    return 'none'


def limits_for(user):
    try:
        subscription = user.subscription
    except DoctorSubscription.DoesNotExist:
        subscription = None
    return PLAN_LIMITS[effective_plan_key(subscription)]


def subscription_summary(subscription):
    """The status block shared by GET /doctor/subscription/, MeSerializer,
    and build_login_payload — one shape everywhere so the mobile app never
    has to reconcile two slightly different representations of the same
    subscription. ``subscription`` may be None (e.g. a doctor row that
    predates this app and hasn't been backfilled yet) — treated the same
    as effective_plan_key(None): no entitlement."""
    plan_key = effective_plan_key(subscription)
    return {
        'status': subscription.status if subscription else DoctorSubscription.STATUS_PENDING,
        'plan': subscription.plan if subscription else '',
        'effective_plan': plan_key,
        'trial_ends_at': subscription.trial_ends_at if subscription else None,
        'current_period_end': subscription.current_period_end if subscription else None,
        'grace_ends_at': subscription.grace_ends_at if subscription else None,
        'limits': PLAN_LIMITS[plan_key],
    }


def is_promoted(user):
    return limits_for(user)['promoted']


def promoted_rank_case():
    """Case/When ordering annotation for DoctorListView: 0 sorts a promoted
    doctor (trialing, or active/past_due on the Pro plan) ahead of everyone
    else; 1 otherwise. Mirrors effective_plan_key's 'promoted' rule at the
    SQL level so ranking doesn't require pulling every row into Python."""
    return Case(
        When(subscription__status=DoctorSubscription.STATUS_TRIALING, then=Value(0)),
        When(
            subscription__status__in=(
                DoctorSubscription.STATUS_ACTIVE, DoctorSubscription.STATUS_PAST_DUE,
            ),
            subscription__plan=PLAN_PRO,
            then=Value(0),
        ),
        default=Value(1),
        output_field=IntegerField(),
    )
