import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.env import env_bool, env_int, env_list, env_str, load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def test_env_str_required(monkeypatch):
    monkeypatch.delenv("NM_TEST_VAR", raising=False)
    with pytest.raises(ImproperlyConfigured):
        env_str("NM_TEST_VAR")
    assert env_str("NM_TEST_VAR", "fallback") == "fallback"


@pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("off", False), ("", False)])
def test_env_bool(monkeypatch, raw, expected):
    monkeypatch.setenv("NM_TEST_VAR", raw)
    assert env_bool("NM_TEST_VAR", not expected) is expected


def test_env_bool_invalid(monkeypatch):
    monkeypatch.setenv("NM_TEST_VAR", "maybe")
    with pytest.raises(ImproperlyConfigured):
        env_bool("NM_TEST_VAR", False)


def test_env_int_and_list(monkeypatch):
    monkeypatch.setenv("NM_TEST_INT", "5")
    monkeypatch.setenv("NM_TEST_LIST", "a, b,,c")
    assert env_int("NM_TEST_INT", 1) == 5
    assert env_list("NM_TEST_LIST") == ["a", "b", "c"]
    monkeypatch.setenv("NM_TEST_INT", "x")
    with pytest.raises(ImproperlyConfigured):
        env_int("NM_TEST_INT", 1)


def test_load_dotenv_does_not_override(monkeypatch, tmp_path):
    monkeypatch.setenv("NM_EXISTING", "real")
    monkeypatch.delenv("NM_NEW", raising=False)
    monkeypatch.delenv("NM_QUOTED", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nNM_EXISTING=file\nexport NM_NEW=hello\nNM_QUOTED='a b'\n")
    load_dotenv(env_file)
    assert os.environ["NM_EXISTING"] == "real"
    assert os.environ["NM_NEW"] == "hello"
    assert os.environ["NM_QUOTED"] == "a b"


def _settings_without_dotenv(env_overrides, code):
    """Import base settings in a fresh process, ignoring the project's .env."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "DATABASE_URL"))}
    env.update(env_overrides)
    env["DJANGO_SETTINGS_MODULE"] = "config.settings.base"
    prelude = "import config.env as e; e.load_dotenv = lambda path: None\n"
    return subprocess.run(
        [sys.executable, "-c", prelude + code], cwd=ROOT, env=env, capture_output=True, text=True
    )


def test_base_settings_require_secret_key():
    result = _settings_without_dotenv(
        {"DATABASE_URL": "sqlite://:memory:"}, "from django.conf import settings; settings.SECRET_KEY"
    )
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY is not set" in result.stderr


def test_base_settings_secure_by_default():
    code = (
        "from django.conf import settings as s;"
        "print(s.DEBUG, s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, s.SESSION_COOKIE_HTTPONLY)"
    )
    result = _settings_without_dotenv(
        {"DJANGO_SECRET_KEY": "x" * 50, "DATABASE_URL": "sqlite://:memory:"}, code
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "True", "True", "True"]


def test_beat_schedules_agent_jobs_and_no_scans(settings):
    tasks = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
    assert tasks == {"monitoring.tasks.check_agents", "monitoring.tasks.run_host_checks"}
    # Scans are driven by the scanner agent over HTTP, never by Celery.
    assert not hasattr(settings, "CELERY_TASK_ROUTES")
