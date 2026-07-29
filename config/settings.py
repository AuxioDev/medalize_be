import environ
from datetime import timedelta
from pathlib import Path

env = environ.Env(DEBUG=(bool, False))

BASE_DIR = Path(__file__).resolve().parent.parent

environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('SECRET_KEY')
DEBUG = env('DEBUG')
JWT_SECRET_KEY = env('JWT_SECRET_KEY')


def _validate_secret(name, value):
    from django.core.exceptions import ImproperlyConfigured

    if len(value) < 50:
        raise ImproperlyConfigured(
            f'{name} must be at least 50 characters. Generate one with: '
            'python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"'
        )
    # The `django-insecure-` prefix is Django's throwaway dev default and must
    # never reach production, even though it satisfies the length check above.
    if not DEBUG and value.startswith('django-insecure-'):
        raise ImproperlyConfigured(
            f'{name} uses the insecure Django development default. Set a unique, random secret for production.'
        )


def _validate_secrets_distinct(secret_key, jwt_secret_key):
    from django.core.exceptions import ImproperlyConfigured

    # A single leaked key must not compromise both Django sessions/signing and
    # every JWT ever issued — the two secrets have to be independent.
    if jwt_secret_key == secret_key:
        raise ImproperlyConfigured('JWT_SECRET_KEY must be different from SECRET_KEY.')


_validate_secret('SECRET_KEY', SECRET_KEY)
_validate_secret('JWT_SECRET_KEY', JWT_SECRET_KEY)
_validate_secrets_distinct(SECRET_KEY, JWT_SECRET_KEY)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
    'corsheaders',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'drf_spectacular',
    'apps.users',
    'apps.core',
    'apps.family',
    'apps.doctors',
    'apps.hospitals',
    'apps.appointments',
    'apps.notifications',
    'apps.assistant',
    'apps.medications',
    'apps.prescriptions',
    'apps.records',
    'apps.messaging',
    'apps.payments',
    'apps.subscriptions',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    # Serves static files (Django admin assets) in production without a
    # separate web server. Must come right after SecurityMiddleware.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

AUTH_USER_MODEL = 'users.User'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env('DB_NAME', default='medalize_db'),
        'USER': env('DB_USER', default='postgres'),
        # No fallback: docker-compose's own Postgres container also has no
        # password fallback (see docker-compose.yml) — a forgotten
        # DB_PASSWORD used to silently produce a real database with a known
        # weak password ('postgres') and no error anywhere. Fail loud
        # instead, matching SECRET_KEY/JWT_SECRET_KEY below.
        'PASSWORD': env('DB_PASSWORD'),
        'HOST': env('DB_HOST', default='localhost'),
        'PORT': env('DB_PORT', default='5432'),
        'CONN_MAX_AGE': 60,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
# Single-market assumption: doctors, patients and workplaces are all treated
# as being in this one timezone. Working hours (WorkingHours.start_time/
# end_time) are naive times combined with a date via timezone.make_aware(),
# which uses this setting as the "current timezone" — so this value must
# match where doctors actually practice, not UTC, or a doctor entering
# "09:00" has it silently stored/interpreted as 09:00 in the wrong zone. If
# Medalize ever expands to doctors/hospitals in genuinely different
# timezones, this needs to become a per-workplace field instead.
TIME_ZONE = env('TIME_ZONE', default='Asia/Baku')
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Nginx terminates TLS; trust forwarded proto header
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'EXCEPTION_HANDLER': 'apps.core.exceptions.custom_exception_handler',
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
        'rest_framework.throttling.ScopedRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/minute',
        'user': '1000/minute',
        'login': '10/minute',
        'social_login': '10/minute',
        'register': '5/minute',
        'password_reset': '3/minute',
        'email_change': '3/minute',
        'fcm_register': '20/hour',
        'assistant_message': '30/hour',
        'messaging_message': '60/minute',
        # Pre-launch hardening: these previously relied only on the generic
        # `user: 1000/minute` default, which is far too generous for actions
        # with a real cost (Cloudinary storage/bandwidth per upload) or an
        # abuse surface (spam-booking/cancelling slots other patients want).
        'appointment_book': '20/hour',
        'appointment_mutate': '30/hour',
        'file_upload': '30/hour',
        # Nominatim's usage policy caps at 1 req/sec across the whole
        # backend; this per-user cap keeps us well under that even if
        # several doctors edit workplaces at once.
        'reverse_geocode': '20/minute',
        # Opens a Payriff hosted-checkout order per call — cheap to abuse,
        # costly to leave uncapped (each call is a real API round-trip to
        # the payment provider).
        'subscription_checkout': '10/hour',
        # The only user-submitted-content endpoint in the app (POST
        # /api/hospitals/, a doctor's "add your variant" when their hospital
        # isn't in the registry) — new rows are immediately visible to every
        # other doctor, so this is capped tighter than most write actions.
        'hospital_create': '5/hour',
    },
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=14),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': JWT_SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'TOKEN_OBTAIN_SERIALIZER': 'apps.users.serializers.CustomTokenObtainPairSerializer',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Medalize API',
    'DESCRIPTION': 'Medalize backend API documentation',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='noreply@medalize.com')

# ── Social login (Google / Apple id_token verification) ──────────────────────
# Comma-separated OAuth client ids accepted as `aud` in Google id_tokens
# (list the iOS, Android and web client ids from the Firebase/Google Cloud
# console). Empty ⇒ Google sign-in disabled.
GOOGLE_OAUTH_CLIENT_IDS = env.list('GOOGLE_OAUTH_CLIENT_IDS', default=[])
# Comma-separated bundle ids accepted as `aud` in Apple id_tokens (the app's
# bundle identifier registered in the Apple Developer portal, e.g.
# az.medalize.app). Empty ⇒ Apple sign-in disabled.
APPLE_BUNDLE_IDS = env.list('APPLE_BUNDLE_IDS', default=[])

CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=['http://localhost:3000'])
CORS_ALLOW_CREDENTIALS = True

REDIS_URL = env('REDIS_URL', default='redis://localhost:6379/0')

CACHES = {
    'default': {
        # django-redis backend so slot caches can be invalidated by pattern
        # (cache.delete_pattern) when a doctor changes hours or blocked periods.
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': REDIS_URL,
        'KEY_PREFIX': 'medalize',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        },
    }
}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_RESULT_EXPIRES = 3600
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_BEAT_SCHEDULE = {
    'send-appointment-reminders': {
        'task': 'apps.notifications.tasks.send_appointment_reminders',
        'schedule': timedelta(minutes=30),
    },
    'auto-complete-past-appointments': {
        'task': 'apps.notifications.tasks.auto_complete_past_appointments',
        'schedule': timedelta(minutes=30),
    },
    'expire-stale-pending-appointments': {
        'task': 'apps.notifications.tasks.expire_stale_pending_appointments',
        'schedule': timedelta(minutes=30),
    },
    'delete-expired-assistant-conversations': {
        'task': 'apps.assistant.tasks.delete_expired_conversations',
        # 90-day TTL cleanup; unlike the 30-minute jobs above, once a day is enough.
        'schedule': timedelta(hours=24),
    },
    'sweep-subscriptions': {
        'task': 'apps.subscriptions.tasks.sweep_subscriptions',
        'schedule': timedelta(hours=1),
    },
}

FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Defense-in-depth backstop — nginx already 301s HTTP→HTTPS
    # (nginx/default.conf), but this makes Django itself refuse to serve a
    # plain-HTTP request too, in case nginx is ever misconfigured or
    # bypassed. Safe with SECURE_PROXY_SSL_HEADER below (already set) — a
    # real HTTPS request through nginx carries X-Forwarded-Proto: https, so
    # this doesn't loop-redirect proxied traffic.
    SECURE_SSL_REDIRECT = True

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
}

# ── Media storage (Cloudinary CDN) ────────────────────────────────────────────
# Uploads (doctor diplomas, avatars) are stored on Cloudinary when credentials
# are present; otherwise they fall back to local filesystem storage so local
# development and tests work without a Cloudinary account.
CLOUDINARY_CLOUD_NAME = env('CLOUDINARY_CLOUD_NAME', default='')
CLOUDINARY_API_KEY = env('CLOUDINARY_API_KEY', default='')
CLOUDINARY_API_SECRET = env('CLOUDINARY_API_SECRET', default='')
# Optional: a Cloudinary "token-based authentication" key (Console > Settings
# > Security), separate from the API secret above. When set, diploma/avatar
# URLs carry a genuinely time-limited (5 min) signed token instead of just a
# non-expiring path signature. Safe to leave blank — URLs are still signed
# and un-guessable either way, see apps/core/storage.py.
CLOUDINARY_AUTH_TOKEN_KEY = env('CLOUDINARY_AUTH_TOKEN_KEY', default='')

USE_CLOUDINARY = bool(
    CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET
)

if USE_CLOUDINARY:
    INSTALLED_APPS += ['cloudinary', 'cloudinary_storage']
    CLOUDINARY_STORAGE = {
        'CLOUD_NAME': CLOUDINARY_CLOUD_NAME,
        'API_KEY': CLOUDINARY_API_KEY,
        'API_SECRET': CLOUDINARY_API_SECRET,
    }
    import cloudinary

    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True,
    )
    _DEFAULT_FILE_BACKEND = 'apps.core.storage.PrivateImageCloudinaryStorage'
else:
    _DEFAULT_FILE_BACKEND = 'django.core.files.storage.FileSystemStorage'

# Django 5.1+ removed DEFAULT_FILE_STORAGE in favour of the STORAGES setting.
STORAGES = {
    'default': {'BACKEND': _DEFAULT_FILE_BACKEND},
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'
    },
}

# ── AI symptom assistant (local template matching, no external API) ─────────
# Leave blank to disable the feature (all /api/assistant/ endpoints answer
# 503). Not needed for migrations — only for message encryption at runtime.
ASSISTANT_ENCRYPTION_KEY = env('ASSISTANT_ENCRYPTION_KEY', default='')

# Firebase Admin SDK for FCM push notifications.
# Set FIREBASE_CREDENTIALS_JSON to the path of your serviceAccountKey.json,
# or leave empty to disable push notifications (emails + in-app still work).
FIREBASE_CREDENTIALS_JSON = env('FIREBASE_CREDENTIALS_JSON', default='')

# ── In-app payments (Payriff — Azerbaijani card acquiring) ───────────────────
# Both values must be set for the real 'payriff' provider to be enabled;
# leave either blank to disable it. Irrelevant while PAYMENT_PROVIDER='mock'
# below (the current default) — the mock provider needs no credentials.
# See apps/payments/providers/payriff.py::PayriffProvider for the (partially
# unverified — flagged there) API details this integrates against.
PAYRIFF_MERCHANT_ID = env('PAYRIFF_MERCHANT_ID', default='')
PAYRIFF_SECRET_KEY = env('PAYRIFF_SECRET_KEY', default='')

# Which apps.payments.providers.base.PaymentProvider backs every payment
# surface in the app (appointments here, doctor/hospital subscriptions in
# apps.subscriptions — all of them go through apps.payments.service.
# get_provider(), never construct a provider directly). 'mock' is a
# temporary stand-in that skips Payriff entirely: a same-backend page with a
# cosmetic card-entry form marks the order paid on submit, no real API calls
# or credentials involved. Set to 'payriff' (or unset) once the real
# integration is verified against actual merchant credentials — see
# PayriffProvider's docstring for exactly what's still unconfirmed there.
PAYMENT_PROVIDER = env('PAYMENT_PROVIDER', default='mock')

# Absolute base URL of this backend (no trailing slash needed), used to build
# the approveURL/cancelURL/declineURL Payriff redirects the user's browser to
# after checkout (apps/payments/service.py::_return_url). Not used for
# anything else — the mobile app talks to the API via its own configured
# base URL, unrelated to this setting.
BACKEND_BASE_URL = env('BACKEND_BASE_URL', default='http://localhost:8000')

if FIREBASE_CREDENTIALS_JSON:
    import firebase_admin
    from firebase_admin import credentials as fb_credentials
    try:
        firebase_admin.get_app()
    except ValueError:
        _fb_cred = fb_credentials.Certificate(FIREBASE_CREDENTIALS_JSON)
        firebase_admin.initialize_app(_fb_cred)
