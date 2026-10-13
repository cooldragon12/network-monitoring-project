import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django.urls import reverse
from django.utils import timezone

from devices.models import Device
from devices.services import process_scan_results
from monitoring import notifications, tasks
from monitoring.models import Alert, RemoteHost
from monitoring.notifications import (
    Message,
    NotificationError,
    RetryableNotificationError,
    build_message,
    send_ntfy,
)
from scanner.arp_scan import DiscoveredHost

pytestmark = pytest.mark.django_db


# --- Fake ntfy server -----------------------------------------------------------------

class FakeNtfy:
    def __init__(self):
        self.requests = []
        self.status_codes = []  # pop one per request; default 200
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "json": json.loads(body)})
                code = outer.status_codes.pop(0) if outer.status_codes else 200
                self.send_response(code)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def ntfy_server():
    server = FakeNtfy()
    yield server
    server.close()


@pytest.fixture
def notify_settings(settings, ntfy_server):
    settings.SITE_URL = "http://192.168.31.234:8000"
    settings.NOTIFICATIONS = {
        "COOLDOWN_MINUTES": 60,
        "RANDOM_MAC": True,
        "NTFY": {"ENABLED": True, "URL": ntfy_server.url, "TOPIC": "netmon-alerts", "TOKEN": "tk_test", "TIMEOUT": 5},
        "EMAIL": {"ENABLED": False, "TO": []},
    }
    return settings


@pytest.fixture
def device():
    return Device.objects.create(mac="b8:27:eb:aa:bb:cc", ip="192.168.31.20", vendor="Raspberry Pi Foundation")


def make_alert(device, kind=Alert.Kind.NEW_DEVICE, severity=Alert.Severity.WARNING, **kwargs):
    return Alert.objects.create(device=device, kind=kind, severity=severity, message="msg", **kwargs)


# --- Formatting -------------------------------------------------------------------------

def test_build_message_new_device(notify_settings, device):
    alert = make_alert(device)
    msg = build_message(alert)
    assert msg.title == "New device: Raspberry Pi Foundation"
    assert msg.priority == 4
    assert msg.url == f"http://192.168.31.234:8000/devices/{device.pk}"


def test_build_message_random_mac_is_low_priority(notify_settings, device):
    msg = build_message(make_alert(device, Alert.Kind.NEW_RANDOM_MAC, Alert.Severity.INFO))
    assert msg.priority == 2


def test_build_message_remote_host(notify_settings):
    host = RemoteHost(name="vps")
    host.set_new_token()
    host.save()
    alert = Alert.objects.create(remote_host=host, kind=Alert.Kind.HOST_OFFLINE, severity="critical", message="m")
    msg = build_message(alert)
    assert msg.priority == 5
    assert msg.url.endswith(f"/admin/monitoring/remotehost/{host.pk}/change/")


# --- ntfy transport ----------------------------------------------------------------------

MSG = Message(title="Neuer Gerät: Wohnzimmer-TV ✓", body="body", priority=4, tags=("warning",), url="http://x/")


def test_send_ntfy_payload_and_auth(ntfy_server):
    send_ntfy(MSG, {"URL": ntfy_server.url, "TOPIC": "netmon-alerts", "TOKEN": "tk_abc"})
    req = ntfy_server.requests[0]
    assert req["path"] == "/"
    assert req["headers"]["Authorization"] == "Bearer tk_abc"
    assert req["json"] == {
        "topic": "netmon-alerts",
        "title": "Neuer Gerät: Wohnzimmer-TV ✓",
        "message": "body",
        "priority": 4,
        "tags": ["warning"],
        "click": "http://x/",
    }


def test_send_ntfy_without_token_has_no_auth_header(ntfy_server):
    send_ntfy(MSG, {"URL": ntfy_server.url, "TOPIC": "t", "TOKEN": ""})
    assert "Authorization" not in ntfy_server.requests[0]["headers"]


@pytest.mark.parametrize("code,retryable", [(500, True), (502, True), (429, True), (403, False), (400, False)])
def test_send_ntfy_error_classification(ntfy_server, code, retryable):
    ntfy_server.status_codes.append(code)
    with pytest.raises(NotificationError) as excinfo:
        send_ntfy(MSG, {"URL": ntfy_server.url, "TOPIC": "t"})
    assert isinstance(excinfo.value, RetryableNotificationError) is retryable


def test_send_ntfy_unreachable_is_retryable():
    with pytest.raises(RetryableNotificationError):
        send_ntfy(MSG, {"URL": "http://127.0.0.1:9", "TOPIC": "t", "TIMEOUT": 2})


# --- End-to-end: alert creation -> notification -------------------------------------------

def test_new_device_from_scan_is_pushed(notify_settings, ntfy_server, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        process_scan_results([DiscoveredHost(mac="b8:27:eb:aa:bb:cc", ip="192.168.31.20")], "192.168.31.0/24")

    assert len(ntfy_server.requests) == 1
    alert = Alert.objects.get()
    assert alert.notified and alert.notified_at is not None
    assert alert.notified_channels == ["ntfy"]
    assert ntfy_server.requests[0]["json"]["priority"] == 4


def test_no_notification_before_commit(notify_settings, ntfy_server, device, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        make_alert(device)
    assert ntfy_server.requests == []
    assert len(callbacks) == 1


def test_disabled_channels_send_nothing(notify_settings, ntfy_server, device, django_capture_on_commit_callbacks):
    notify_settings.NOTIFICATIONS["NTFY"]["ENABLED"] = False
    with django_capture_on_commit_callbacks(execute=True):
        alert = make_alert(device)
    alert.refresh_from_db()
    assert ntfy_server.requests == [] and not alert.notified


def test_email_channel(notify_settings, device, django_capture_on_commit_callbacks):
    notify_settings.NOTIFICATIONS["NTFY"]["ENABLED"] = False
    notify_settings.NOTIFICATIONS["EMAIL"] = {"ENABLED": True, "TO": ["me@example.com"]}
    with django_capture_on_commit_callbacks(execute=True):
        alert = make_alert(device)
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["me@example.com"]
    assert "New device" in mail.outbox[0].subject
    alert.refresh_from_db()
    assert alert.notified_channels == ["email"]


# --- Dedupe / cooldown ------------------------------------------------------------------

def test_duplicate_within_cooldown_is_suppressed(notify_settings, ntfy_server, device, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        first = make_alert(device)
        second = make_alert(device)
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.notified and not first.suppressed
    assert second.suppressed and not second.notified
    assert "cooldown" in second.notify_error
    assert len(ntfy_server.requests) == 1


def test_same_kind_after_cooldown_is_sent(notify_settings, ntfy_server, device, django_capture_on_commit_callbacks):
    make_alert(device, created_at=timezone.now() - timedelta(minutes=61), notified=True)
    with django_capture_on_commit_callbacks(execute=True):
        later = make_alert(device)
    later.refresh_from_db()
    assert later.notified and not later.suppressed


def test_different_kind_or_device_not_suppressed(notify_settings, ntfy_server, device, django_capture_on_commit_callbacks):
    other = Device.objects.create(mac="00:11:32:00:00:01")
    with django_capture_on_commit_callbacks(execute=True):
        make_alert(device)
        make_alert(device, kind=Alert.Kind.DEVICE_OFFLINE)
        make_alert(other)
    assert len(ntfy_server.requests) == 3
    assert not Alert.objects.filter(suppressed=True).exists()


def test_dispatch_is_idempotent(notify_settings, ntfy_server, device, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        alert = make_alert(device)
    tasks.dispatch_alert_notifications(alert.pk)
    tasks.send_alert_via_channel(alert.pk, "ntfy")
    assert len(ntfy_server.requests) == 1


def test_acknowledged_alert_not_sent(notify_settings, ntfy_server, device):
    alert = make_alert(device, acknowledged=True)
    assert tasks.dispatch_alert_notifications(alert.pk) == []
    assert ntfy_server.requests == []


def test_random_mac_notifications_can_be_disabled(notify_settings, ntfy_server, device, django_capture_on_commit_callbacks):
    notify_settings.NOTIFICATIONS["RANDOM_MAC"] = False
    with django_capture_on_commit_callbacks(execute=True):
        alert = make_alert(device, Alert.Kind.NEW_RANDOM_MAC, Alert.Severity.INFO)
    alert.refresh_from_db()
    assert alert.suppressed and ntfy_server.requests == []


# --- Retry -------------------------------------------------------------------------------

def test_transient_failure_is_retried_then_succeeds(notify_settings, ntfy_server, device, django_capture_on_commit_callbacks):
    ntfy_server.status_codes.extend([503, 503])
    with django_capture_on_commit_callbacks(execute=True):
        alert = make_alert(device)
    alert.refresh_from_db()
    assert len(ntfy_server.requests) == 3
    assert alert.notified
    assert alert.notify_attempts == 2
    assert alert.notify_error == ""


def test_retries_give_up_after_max(notify_settings, ntfy_server, device, django_capture_on_commit_callbacks):
    ntfy_server.status_codes.extend([500] * 10)
    with django_capture_on_commit_callbacks(execute=True):
        alert = make_alert(device)
    alert.refresh_from_db()
    assert len(ntfy_server.requests) == 1 + tasks.send_alert_via_channel.max_retries
    assert not alert.notified
    assert "HTTP 500" in alert.notify_error


def test_permanent_failure_is_not_retried(notify_settings, ntfy_server, device, django_capture_on_commit_callbacks):
    ntfy_server.status_codes.append(403)
    with django_capture_on_commit_callbacks(execute=True):
        alert = make_alert(device)
    alert.refresh_from_db()
    assert len(ntfy_server.requests) == 1
    assert alert.notify_attempts == 1 and "403" in alert.notify_error


def test_broker_outage_does_not_break_scan(notify_settings, monkeypatch, django_capture_on_commit_callbacks):
    def broker_down(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(tasks.dispatch_alert_notifications, "delay", broker_down)
    with django_capture_on_commit_callbacks(execute=True):
        process_scan_results([DiscoveredHost(mac="b8:27:eb:aa:bb:cc", ip="192.168.31.20")], "192.168.31.0/24")
    assert Alert.objects.count() == 1


# --- Admin & management command ------------------------------------------------------

def test_admin_send_now_bypasses_cooldown(notify_settings, ntfy_server, device, admin_client):
    alert = make_alert(device, suppressed=True)
    admin_client.post(
        reverse("admin:monitoring_alert_changelist"),
        {"action": "send_notification_now", "_selected_action": [alert.pk]},
    )
    alert.refresh_from_db()
    assert alert.notified and len(ntfy_server.requests) == 1


def test_send_test_notification_command(notify_settings, ntfy_server):
    call_command("send_test_notification")
    assert ntfy_server.requests[0]["json"]["title"] == "Network Monitor test"


def test_send_test_notification_reports_failure(notify_settings, ntfy_server):
    ntfy_server.status_codes.append(401)
    with pytest.raises(CommandError):
        call_command("send_test_notification")


def test_send_test_notification_requires_channel(settings):
    settings.NOTIFICATIONS = {**settings.NOTIFICATIONS, "NTFY": {**settings.NOTIFICATIONS["NTFY"], "ENABLED": False}}
    with pytest.raises(CommandError, match="No notification channels"):
        call_command("send_test_notification")


def test_channel_registry_matches_settings():
    assert set(notifications.CHANNELS) == {"ntfy", "email"}
