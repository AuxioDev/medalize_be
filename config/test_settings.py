from .settings import *  # noqa: F401, F403

# The existing payment test suite exercises PayriffProvider directly (patching
# apps.payments.providers.payriff.PayriffProvider.create_order/check_status
# and toggling PAYRIFF_MERCHANT_ID/PAYRIFF_SECRET_KEY via @override_settings)
# — settings.py's PAYMENT_PROVIDER default of 'mock' would silently route all
# of that through MockCardProvider instead, unmocked. Pin it back to
# 'payriff' here; tests that specifically want the mock provider's behavior
# override this explicitly with @override_settings(PAYMENT_PROVIDER='mock').
PAYMENT_PROVIDER = 'payriff'

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

# Run .delay()/.apply_async() calls synchronously, in-process, instead of
# handing them to a real Celery broker (none is running in tests — every
# .delay() call was previously failing silently with a swallowed
# ConnectionError, so tasks queued from view code never actually executed).
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
