import logging

from celery import shared_task
from django.utils import timezone

from .models import Dependent
from .services import is_adult, issue_consent_notice

logger = logging.getLogger(__name__)


@shared_task
def sweep_dependent_consent_notices():
    """Daily sweep closing the one gap issue_consent_notice's own callers
    (DependentCreateSerializer.create()/update()) leave open: a dependent
    who passively crosses the 18-year threshold with no create/update in
    between never gets notified, since nothing in the request/response
    cycle ever observes the birthday happening on its own.

    `date_of_birth__lte=<18 years ago, approximately>` is a coarse DB-level
    filter to avoid loading every dependent in the table (in particular
    every still-minor child) on each run — it is intentionally not exact
    (a plain year-subtraction, not calendar-precise for Feb 29 birthdays),
    so `is_adult()` still makes the authoritative per-row call in Python
    exactly like it does everywhere else in this app. Being a day or two
    too inclusive here just means a handful of extra rows get the (cheap,
    correct) precise check; being too exclusive would silently miss
    someone, which is the failure mode worth avoiding.

    `contact_email__gt=''` and `consent_notice_sent_at__isnull=True` mirror
    issue_consent_notice's own preconditions (see its docstring) — a
    dependent without a contact_email can't be notified at all (the
    serializer already requires one before an adult dependent can be
    created, so this only matters for pre-existing/legacy rows) and one
    already notified must not be re-notified by this sweep on every
    subsequent run."""
    today = timezone.localdate()
    try:
        eighteen_years_ago = today.replace(year=today.year - 18)
    except ValueError:
        # today is a Feb 29 with no equivalent 18 years back — a day off
        # either way is immaterial, is_adult() below is the real check.
        eighteen_years_ago = today.replace(year=today.year - 18, day=28)

    candidates = Dependent.objects.filter(
        is_active=True,
        date_of_birth__isnull=False,
        contact_email__gt='',
        consent_notice_sent_at__isnull=True,
        date_of_birth__lte=eighteen_years_ago,
    )
    for dependent in candidates:
        if not is_adult(dependent.date_of_birth):
            continue
        try:
            issue_consent_notice(dependent)
        except Exception:
            logger.exception(
                'Failed to issue consent notice for dependent %s during sweep', dependent.id
            )
