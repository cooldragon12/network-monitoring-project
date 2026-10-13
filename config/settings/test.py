"""Settings for the test suite: in-memory SQLite unless TEST_DATABASE_URL is set."""

import os

os.environ["DJANGO_SECRET_KEY"] = "test-only-insecure-key"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite://:memory:")

from config.settings.base import *  # noqa: E402,F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
