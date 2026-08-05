from rest_framework.throttling import SimpleRateThrottle


class ReviewCreateRateThrottle(SimpleRateThrottle):
    """Caps review creation per request IP, deliberately keyed by IP rather
    than by user (unlike apps.users.throttles.EmailChangeRateThrottle's
    UserRateThrottle) — a doctor gaming their own rating with several
    patient accounts they control would defeat a per-user limit, but those
    accounts typically share one IP. Complements the needs_manual_review
    content-based signal set in
    apps.appointments.serializers.ReviewCreateSerializer.create(); this is
    the volume-based half of the same anti-fraud fix. DRF's built-in
    AnonRateThrottle can't be reused here — it only throttles unauthenticated
    requests, and review creation always requires an authenticated patient."""
    scope = 'review_create'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request),
        }
