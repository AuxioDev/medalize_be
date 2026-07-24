from .settings import *  # noqa: F401, F403

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
