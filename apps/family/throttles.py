from rest_framework.throttling import AnonRateThrottle


class DependentConsentRateThrottle(AnonRateThrottle):
    """Rate-limits DependentConsentRejectView (GET + POST) — same
    AnonRateThrottle-per-scope idiom as apps.users.throttles.
    PasswordResetRateThrottle. The reject token itself already has 256 bits
    of entropy (secrets.token_urlsafe(32)), so this is defense-in-depth
    against brute force rather than the primary defense, and against a
    would-be objector being locked out by a much stricter cap than they'd
    plausibly hit through normal use (a mis-click, a retry, opening the
    link on two devices)."""

    scope = 'dependent_consent'
