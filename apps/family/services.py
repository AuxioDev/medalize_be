import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers

from .models import Dependent

logger = logging.getLogger(__name__)

# How long an adult dependent's no-login reject link stays valid. Generous
# on purpose (unlike the 10-minute PasswordResetOTP window) — this is an
# out-of-band notice to someone who may not check the inbox it landed in
# right away and has no app/account to prompt them back, not an in-session
# credential exchange.
CONSENT_TOKEN_LIFETIME = timedelta(days=30)


def dependent_age(date_of_birth, today=None):
    """Whole years old as of `today` (defaults to the current date in the
    project's configured TIME_ZONE) for a given date of birth. `None` when
    `date_of_birth` is falsy. Pure function — no DB access — so it can be
    used from serializer validation before a Dependent instance exists, not
    just from an already-saved row (see Dependent.age, a thin property
    wrapper around this for callers that do have an instance)."""
    if not date_of_birth:
        return None
    today = today or timezone.localdate()
    years = today.year - date_of_birth.year
    if (today.month, today.day) < (date_of_birth.month, date_of_birth.day):
        years -= 1
    return years


def is_adult(date_of_birth):
    """Whether a date of birth computes to 18 or older today. `False`
    (never raises/None) for a falsy `date_of_birth` — "unknown DOB" is
    treated the same as "not (yet) known to be an adult", see
    Dependent.is_adult's docstring for why that's the right default."""
    age = dependent_age(date_of_birth)
    return age is not None and age >= 18


def _display_name(user):
    """Bare full name, falling back to the email — same idiom as
    apps.notifications.tasks._display_name, duplicated here rather than
    imported since that one is a module-private helper of a different app."""
    return f'{user.first_name} {user.last_name}'.strip() or user.email


def issue_consent_notice(dependent):
    """(Re)issue the consent notice an adult (18+) dependent is owed: mints
    a fresh no-login reject token (secrets.token_urlsafe + hashed-at-rest
    with the same django.contrib.auth.hashers.make_password
    PasswordResetOTP.code_hash uses — see apps.users.views.
    PasswordResetRequestView — adapted for a URL token instead of a typed
    code), emails it to the dependent's own contact_email so they're
    positively informed that <account holder> added them, and gives them a
    link to object with no login/account required (see
    apps.family.views.DependentConsentRejectView, which verifies +
    consumes the token via verify/apply below).

    Called from DependentCreateSerializer.create()/update() whenever a
    write makes a dependent newly adult-with-a-contact-email, or changes
    an already-adult dependent's contact_email to a new address — never
    on every unrelated edit (see the serializer for exactly when). A no-op
    if contact_email is blank: the serializer requires it before an adult
    dependent can be created/edited in the first place, but this stays
    defensive since model state can change outside that serializer (the
    Django admin, a shell, a future caller).
    """
    if not dependent.contact_email:
        return

    token = secrets.token_urlsafe(32)
    dependent.consent_token_hash = make_password(token)
    dependent.consent_token_expires_at = timezone.now() + CONSENT_TOKEN_LIFETIME
    dependent.consent_notice_sent_at = timezone.now()
    dependent.consent_objected_at = None
    dependent.save(update_fields=[
        'consent_token_hash', 'consent_token_expires_at',
        'consent_notice_sent_at', 'consent_objected_at',
    ])

    account_holder = dependent.managed_by
    lang = getattr(account_holder, 'language', '') or 'en'
    # settings.BACKEND_BASE_URL + reverse(), same idiom as
    # apps.payments.service._return_url — this link is emailed out, not
    # rendered back to a client with an active HttpRequest in scope.
    base = settings.BACKEND_BASE_URL.rstrip('/')
    path = reverse('dependent-consent-reject', args=[dependent.id])
    reject_url = f'{base}{path}?token={token}&lang={lang}'

    try:
        from apps.notifications.i18n import render_template
        from apps.notifications.tasks import send_transactional_email

        tpl = render_template(
            'dependent_added_notice', lang,
            account_holder_name=_display_name(account_holder),
            dependent_name=f'{dependent.first_name} {dependent.last_name}'.strip(),
            reject_url=reject_url,
        )
        send_transactional_email.delay(tpl['subject'], tpl['body'], dependent.contact_email)
    except Exception:
        logger.exception('Failed to enqueue consent notice for dependent %s', dependent.id)


def consent_token_is_valid(dependent, token):
    """Non-consuming check: is `token` currently a live, unused reject
    token for `dependent`? Used by DependentConsentRejectView.get() to
    decide whether to render the "confirm you want to disconnect" page at
    all — GET must never itself consume the token (see that view's
    docstring for why: an email client's link-prescanning bot follows GET
    links automatically, and only an explicit POST should be able to act).
    """
    if (
        not token
        or not dependent.consent_token_hash
        or not dependent.consent_token_expires_at
        or dependent.consent_token_expires_at < timezone.now()
        or dependent.consent_objected_at is not None
    ):
        return False
    return check_password(token, dependent.consent_token_hash)


def apply_consent_objection(dependent):
    """Performs the actual objection once a token has already been
    validated (consent_token_is_valid) under a row lock in the same
    request — see DependentConsentRejectView.post(), which mirrors
    apps.users.views.PasswordResetConfirmView's select_for_update +
    re-validate pattern to close the same TOCTOU race (two concurrent
    submits both consuming the same token).

    Soft-deletes the dependent via the exact same mechanism as an
    ordinary account-holder-initiated delete (`is_active=False` — see
    DependentDetailView.delete) so historical appointments/medications/
    records already attached to it are never orphaned, rather than a
    second delete path. The token is cleared so it cannot be replayed.
    """
    dependent.consent_objected_at = timezone.now()
    dependent.consent_token_hash = ''
    dependent.consent_token_expires_at = None
    dependent.is_active = False
    dependent.save(update_fields=[
        'consent_objected_at', 'consent_token_hash',
        'consent_token_expires_at', 'is_active', 'updated_at',
    ])


def resolve_dependent(user, dependent_id):
    """Shared ownership/validity check for an optional `dependent_id` coming
    from a client request. Reused by every app that accepts `dependent_id`
    on write (appointments, medications, records) so the rule stays in one
    place: the dependent must exist, be managed by the requesting user, and
    still be active (not soft-deleted).

    Returns the `Dependent` instance, or ``None`` when `dependent_id` is
    falsy (feature not used for this record — perfectly normal, most
    records are still for the account owner). Raises
    ``serializers.ValidationError`` — intended to be called from a
    ``validate_dependent_id`` field-level hook so DRF automatically keys the
    resulting 400 response under ``dependent_id``.
    """
    if not dependent_id:
        return None
    try:
        return Dependent.objects.get(pk=dependent_id, managed_by=user, is_active=True)
    except Dependent.DoesNotExist:
        raise serializers.ValidationError('Dependent not found.')
