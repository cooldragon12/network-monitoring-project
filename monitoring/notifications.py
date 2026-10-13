"""Notification channels (ntfy, email) and alert formatting."""

from __future__ import annotations

import json
import logging
import smtplib
import urllib.error
import urllib.request
from dataclasses import dataclass

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from monitoring.models import Alert

logger = logging.getLogger(__name__)


class NotificationError(Exception):
    """Delivery failed permanently (bad config, auth); retrying will not help."""


class RetryableNotificationError(NotificationError):
    """Delivery failed transiently (network, 5xx, rate limit); worth retrying."""


@dataclass(frozen=True)
class Message:
    title: str
    body: str
    priority: int  # ntfy scale: 1 (min) .. 5 (max)
    tags: tuple[str, ...]
    url: str


_PRIORITY = {
    Alert.Severity.INFO: 2,
    Alert.Severity.WARNING: 4,
    Alert.Severity.CRITICAL: 5,
}
_TAGS = {
    Alert.Kind.NEW_DEVICE: ("warning", "new"),
    Alert.Kind.NEW_RANDOM_MAC: ("iphone", "new"),
    Alert.Kind.DEVICE_OFFLINE: ("electric_plug",),
    Alert.Kind.HOST_OFFLINE: ("rotating_light",),
}


def alert_url(alert: Alert) -> str:
    if alert.device_id:
        path = reverse("admin:devices_device_change", args=[alert.device_id])
    else:
        path = reverse("admin:monitoring_remotehost_change", args=[alert.remote_host_id])
    return settings.SITE_URL.rstrip("/") + path


def build_message(alert: Alert) -> Message:
    return Message(
        title=f"{alert.get_kind_display()}: {alert.subject}",
        body=alert.message,
        priority=_PRIORITY.get(alert.severity, 3),
        tags=_TAGS.get(alert.kind, ()),
        url=alert_url(alert),
    )


def send_ntfy(message: Message, cfg: dict) -> None:
    """Publish via ntfy's JSON API (UTF-8 safe, unlike header-based publishing)."""
    payload = {
        "topic": cfg["TOPIC"],
        "title": message.title,
        "message": message.body,
        "priority": message.priority,
        "tags": list(message.tags),
        "click": message.url,
    }
    headers = {"Content-Type": "application/json"}
    if cfg.get("TOKEN"):
        headers["Authorization"] = f"Bearer {cfg['TOKEN']}"
    req = urllib.request.Request(
        cfg["URL"].rstrip("/") + "/",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.get("TIMEOUT", 10)) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        detail = f"ntfy returned HTTP {exc.code}"
        if exc.code == 429 or exc.code >= 500:
            raise RetryableNotificationError(detail) from exc
        raise NotificationError(f"{detail} (check NOTIFY_NTFY_TOKEN / topic access)") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise RetryableNotificationError(f"ntfy unreachable: {exc}") from exc


def send_email(message: Message, cfg: dict) -> None:
    body = f"{message.body}\n\n{message.url}"
    try:
        send_mail(f"[Network Monitor] {message.title}", body, None, cfg["TO"], fail_silently=False)
    except (smtplib.SMTPException, OSError) as exc:
        raise RetryableNotificationError(f"email failed: {exc}") from exc


CHANNELS = {
    "ntfy": send_ntfy,
    "email": send_email,
}


def enabled_channels() -> list[str]:
    cfg = settings.NOTIFICATIONS
    channels = []
    if cfg["NTFY"]["ENABLED"]:
        channels.append("ntfy")
    if cfg["EMAIL"]["ENABLED"] and cfg["EMAIL"]["TO"]:
        channels.append("email")
    return channels


def channel_config(channel: str) -> dict:
    return settings.NOTIFICATIONS[channel.upper()]
