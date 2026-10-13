"""Django settings, configured entirely from environment variables.

A ``.env`` file in the project root is read for local (non-Docker) processes,
e.g. the scanner worker running natively on the host. Real environment
variables always win. See ``.env.example`` for every option.
"""

from pathlib import Path

import dj_database_url

from config.env import env_bool, env_int, env_list, env_str, load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

# --- Core / security -------------------------------------------------------------

SECRET_KEY = env_str("DJANGO_SECRET_KEY")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Secure cookies by default; set to false only for plain-HTTP local development.
_secure_cookies = env_bool("DJANGO_SECURE_COOKIES", True)
SESSION_COOKIE_SECURE = _secure_cookies
CSRF_COOKIE_SECURE = _secure_cookies
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Behind an HTTPS reverse proxy (e.g. Caddy) that sets X-Forwarded-Proto.
if env_bool("DJANGO_BEHIND_HTTPS_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 0)

# --- Applications ------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "devices",
    "monitoring",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database ---------------------------------------------------------------------

DATABASES = {
    "default": dj_database_url.parse(env_str("DATABASE_URL"), conn_max_age=60, conn_health_checks=True)
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Auth -------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "admin:login"

REST_FRAMEWORK = {
    # Token auth first so unauthenticated requests get 401 + WWW-Authenticate.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "api.authentication.ApiTokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_RATES": {"login": env_str("LOGIN_THROTTLE_RATE", "10/min")},
}

# Shared cache (throttling state must be shared across processes).
# CACHE_URL=locmem:// gives a per-process cache for quick local runs without Redis.
_cache_url = env_str("CACHE_URL", "redis://localhost:6379/1")
CACHES = {
    "default": (
        {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
        if _cache_url.startswith("locmem:")
        else {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": _cache_url}
    )
}

# --- I18N / static ---------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = env_str("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = Path(env_str("STATIC_ROOT", str(BASE_DIR / "staticfiles")))
# Built React app (Vite). The Docker image builds it outside the bind mount.
FRONTEND_DIST_DIR = Path(env_str("FRONTEND_DIST_DIR", str(BASE_DIR / "frontend" / "dist")))
STATICFILES_DIRS = [FRONTEND_DIST_DIR] if FRONTEND_DIST_DIR.is_dir() else []
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# --- Celery ----------------------------------------------------------------------

CELERY_BROKER_URL = env_str("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = None
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_DEFAULT_QUEUE = "default"
# The LAN scan runs on a dedicated queue consumed only by a worker that sits on
# the LAN with raw-socket privileges (see README). Never run scans in web requests.
SCANNER_QUEUE = "scanner"
CELERY_TASK_ROUTES = {"devices.tasks.run_lan_scan": {"queue": SCANNER_QUEUE}}

# --- Scanner -----------------------------------------------------------------------

SCANNER = {
    # Empty = auto-detect from the scanner host's default route.
    "SUBNET": env_str("SCAN_SUBNET", ""),
    "INTERFACE": env_str("SCAN_INTERFACE", "") or None,
    "INTERVAL_MINUTES": env_int("SCAN_INTERVAL_MINUTES", 2),
    "OFFLINE_AFTER_MISSED_SCANS": env_int("OFFLINE_AFTER_MISSED_SCANS", 3),
    "TIMEOUT_SECONDS": env_int("SCAN_TIMEOUT_SECONDS", 2),
}

# --- Email (optional notification channel, off unless configured) ------------------

EMAIL_BACKEND = env_str("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env_str("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env_str("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env_str("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env_str("DEFAULT_FROM_EMAIL", "network-monitor@localhost")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env_str("LOG_LEVEL", "INFO")},
}

# Keep flash messages server-side: one-time agent tokens are shown via messages
# and must not round-trip through a (signed but readable) cookie.
MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

# --- Periodic tasks ---------------------------------------------------------------

from datetime import timedelta  # noqa: E402

_scan_interval = timedelta(minutes=SCANNER["INTERVAL_MINUTES"])
CELERY_BEAT_SCHEDULE = {
    "lan-scan": {
        "task": "devices.tasks.run_lan_scan",
        "schedule": _scan_interval,
        # Drop queued scans that were not picked up before the next one is due
        # (e.g. the scanner worker was offline), instead of a backlog burst.
        "options": {"expires": _scan_interval.total_seconds()},
    },
}

# --- Notifications -------------------------------------------------------------------

# Base URL used for links in notifications (must be reachable from your phone).
SITE_URL = env_str("SITE_URL", "http://127.0.0.1:8000")

NOTIFICATIONS = {
    # Repeat alerts of the same kind for the same device/host within this window are not sent.
    "COOLDOWN_MINUTES": env_int("NOTIFY_COOLDOWN_MINUTES", 60),
    # Send (low-priority) notifications for new randomized-MAC devices.
    "RANDOM_MAC": env_bool("NOTIFY_RANDOM_MAC", True),
    "NTFY": {
        "ENABLED": env_bool("NOTIFY_NTFY_ENABLED", False),
        "URL": env_str("NOTIFY_NTFY_URL", "http://127.0.0.1:8080"),
        "TOPIC": env_str("NOTIFY_NTFY_TOPIC", "netmon-alerts"),
        "TOKEN": env_str("NOTIFY_NTFY_TOKEN", ""),
        "TIMEOUT": 10,
    },
    "EMAIL": {
        "ENABLED": env_bool("NOTIFY_EMAIL_ENABLED", False),
        "TO": env_list("NOTIFY_EMAIL_TO"),
    },
}
