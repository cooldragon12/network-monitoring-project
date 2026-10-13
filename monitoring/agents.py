"""Agent check-ins and online/offline tracking for RemoteHosts."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from monitoring.models import Alert, HostEvent, RemoteHost

logger = logging.getLogger(__name__)

METRIC_FIELDS = ("cpu_percent", "mem_percent", "disk_percent", "uptime_seconds")


def offline_threshold(host: RemoteHost) -> timedelta:
    return timedelta(seconds=host.expected_interval_seconds * settings.AGENTS["OFFLINE_GRACE"])


def record_checkin(
    host: RemoteHost,
    *,
    ip: str | None = None,
    hostname: str = "",
    version: str = "",
    metrics: dict | None = None,
) -> RemoteHost:
    """Store a check-in; flips the host online (with an event) if it wasn't."""
    now = timezone.now()
    with transaction.atomic():
        host = RemoteHost.objects.select_for_update().get(pk=host.pk)
        previous_status, previous_checkin = host.status, host.last_checkin
        host.last_checkin = now
        host.last_ip = ip or host.last_ip
        if hostname:
            host.reported_hostname = hostname[:255]
        if version:
            host.agent_version = version[:50]
        for field in METRIC_FIELDS:
            if metrics and field in metrics:
                setattr(host, field, metrics[field])
        if previous_status != RemoteHost.Status.ONLINE:
            host.status = RemoteHost.Status.ONLINE
            details = {"ip": ip}
            if previous_status == RemoteHost.Status.OFFLINE and previous_checkin:
                details["down_seconds"] = int((now - previous_checkin).total_seconds())
            HostEvent.objects.create(host=host, event_type=HostEvent.EventType.ONLINE, timestamp=now, details=details)
        host.save()
    return host


def mark_overdue_hosts_offline(now=None) -> list[str]:
    """Flag online hosts that missed their check-in window; raise a critical alert."""
    now = now or timezone.now()
    flagged = []
    for host in RemoteHost.objects.filter(is_active=True, status=RemoteHost.Status.ONLINE):
        if host.last_checkin and now - host.last_checkin <= offline_threshold(host):
            continue
        with transaction.atomic():
            updated = RemoteHost.objects.filter(pk=host.pk, status=RemoteHost.Status.ONLINE).update(
                status=RemoteHost.Status.OFFLINE
            )
            if not updated:  # raced with a check-in
                continue
            last = host.last_checkin.isoformat() if host.last_checkin else None
            HostEvent.objects.create(
                host=host, event_type=HostEvent.EventType.OFFLINE, timestamp=now, details={"last_checkin": last}
            )
            what = "Scanner" if host.is_scanner else "Host"
            impact = " LAN scans are paused until it reports again." if host.is_scanner else ""
            Alert.objects.create(
                remote_host=host,
                kind=Alert.Kind.HOST_OFFLINE,
                severity=Alert.Severity.CRITICAL,
                message=f"{what} '{host.name}' stopped checking in (last check-in {last or 'never'}).{impact}",
                created_at=now,
            )
        logger.warning("Host %s marked offline", host.name)
        flagged.append(host.name)
    return flagged
