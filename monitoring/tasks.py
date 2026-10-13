import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from monitoring.models import Alert
from monitoring.notifications import (
    CHANNELS,
    NotificationError,
    RetryableNotificationError,
    build_message,
    channel_config,
    enabled_channels,
)

logger = logging.getLogger(__name__)


def suppression_reason(alert: Alert) -> str | None:
    """Why ``alert`` should not be sent, or None if it should."""
    cfg = settings.NOTIFICATIONS
    if alert.kind == Alert.Kind.NEW_RANDOM_MAC and not cfg["RANDOM_MAC"]:
        return "random-MAC notifications disabled"
    cooldown = timedelta(minutes=cfg["COOLDOWN_MINUTES"])
    duplicate = Alert.objects.filter(
        kind=alert.kind,
        device_id=alert.device_id,
        remote_host_id=alert.remote_host_id,
        created_at__gte=alert.created_at - cooldown,
        suppressed=False,
        pk__lt=alert.pk,
    ).exists()
    if duplicate:
        return f"duplicate within {cfg['COOLDOWN_MINUTES']} min cooldown"
    return None


@shared_task(name="monitoring.tasks.dispatch_alert_notifications")
def dispatch_alert_notifications(alert_id: int) -> list[str]:
    """Decide whether to notify for an alert and fan out one task per channel."""
    alert = Alert.objects.filter(pk=alert_id).first()
    if alert is None or alert.notified or alert.suppressed or alert.acknowledged:
        return []
    reason = suppression_reason(alert)
    if reason:
        Alert.objects.filter(pk=alert_id).update(suppressed=True, notify_error=reason)
        logger.info("Alert %s not sent: %s", alert_id, reason)
        return []
    channels = enabled_channels()
    for channel in channels:
        send_alert_via_channel.delay(alert_id, channel)
    return channels


def _record_success(alert_id: int, channel: str) -> None:
    with transaction.atomic():
        alert = Alert.objects.select_for_update().get(pk=alert_id)
        if channel not in alert.notified_channels:
            alert.notified_channels = [*alert.notified_channels, channel]
        alert.notified = True
        alert.notified_at = alert.notified_at or timezone.now()
        alert.notify_error = ""
        alert.save(update_fields=["notified_channels", "notified", "notified_at", "notify_error"])


def _record_failure(alert_id: int, channel: str, exc: Exception) -> None:
    Alert.objects.filter(pk=alert_id).update(
        notify_attempts=F("notify_attempts") + 1, notify_error=f"{channel}: {exc}"
    )


@shared_task(
    bind=True,
    name="monitoring.tasks.send_alert_via_channel",
    autoretry_for=(RetryableNotificationError,),
    max_retries=5,
    retry_backoff=30,  # 30s, 60s, 120s, ... (with jitter)
    retry_backoff_max=900,
    retry_jitter=True,
)
def send_alert_via_channel(self, alert_id: int, channel: str) -> str:
    alert = Alert.objects.select_related("device", "remote_host").filter(pk=alert_id).first()
    if alert is None:
        return "missing"
    if channel in alert.notified_channels:
        return "already sent"
    try:
        CHANNELS[channel](build_message(alert), channel_config(channel))
    except NotificationError as exc:
        _record_failure(alert_id, channel, exc)
        logger.warning("Alert %s via %s failed (attempt %s): %s", alert_id, channel, self.request.retries + 1, exc)
        raise
    _record_success(alert_id, channel)
    return "sent"


@shared_task(name="monitoring.tasks.check_agents")
def check_agents() -> dict:
    """Flag hosts/scanners that stopped checking in; close abandoned scan runs."""
    from devices.scanning import fail_stale_runs
    from monitoring.agents import mark_overdue_hosts_offline

    return {"offline": mark_overdue_hosts_offline(), "stale_scans_closed": fail_stale_runs()}


@shared_task(name="monitoring.tasks.run_host_checks")
def run_host_checks() -> int:
    from monitoring.checks import run_due_checks

    return run_due_checks()
