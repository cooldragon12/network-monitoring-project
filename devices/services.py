"""Turn raw scan results into device state, events and alerts."""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from devices.models import Device, DeviceEvent
from monitoring.models import Alert
from scanner.arp_scan import DiscoveredHost

logger = logging.getLogger(__name__)


@dataclass
class ScanSummary:
    seen: int = 0
    new_devices: list[str] = field(default_factory=list)
    came_online: list[str] = field(default_factory=list)
    went_offline: list[str] = field(default_factory=list)
    ip_changed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "seen": self.seen,
            "new_devices": self.new_devices,
            "came_online": self.came_online,
            "went_offline": self.went_offline,
            "ip_changed": self.ip_changed,
        }


def _create_new_device(host: DiscoveredHost, now: datetime) -> Device:
    device = Device.objects.create(
        mac=host.mac,
        ip=host.ip,
        hostname=host.hostname or "",
        vendor=host.vendor or "",
        is_random_mac=host.locally_administered,
        is_known=False,
        is_online=True,
        first_seen=now,
        last_seen=now,
        source=Device.Source.LAN,
    )
    details = {"ip": host.ip, "vendor": host.vendor, "hostname": host.hostname}
    DeviceEvent.objects.create(
        device=device, event_type=DeviceEvent.EventType.NEW_DEVICE, timestamp=now, details=details
    )
    if host.locally_administered:
        kind, severity = Alert.Kind.NEW_RANDOM_MAC, Alert.Severity.INFO
        what = "New device with a randomized MAC (likely a phone/tablet using Wi-Fi privacy)"
    else:
        kind, severity = Alert.Kind.NEW_DEVICE, Alert.Severity.WARNING
        what = "New unknown device"
    parts = [f"{what}: {host.mac} at {host.ip}"]
    if host.vendor:
        parts.append(f"vendor {host.vendor}")
    if host.hostname:
        parts.append(f"hostname {host.hostname}")
    Alert.objects.create(
        device=device, kind=kind, severity=severity, message=", ".join(parts), created_at=now
    )
    logger.warning("New device %s (%s) at %s", host.mac, host.vendor or "unknown vendor", host.ip)
    return device


def _update_seen_device(device: Device, host: DiscoveredHost, now: datetime, summary: ScanSummary) -> None:
    if device.ip and device.ip != host.ip:
        DeviceEvent.objects.create(
            device=device,
            event_type=DeviceEvent.EventType.IP_CHANGED,
            timestamp=now,
            details={"old_ip": device.ip, "new_ip": host.ip},
        )
        summary.ip_changed.append(device.mac)
    if not device.is_online:
        DeviceEvent.objects.create(
            device=device,
            event_type=DeviceEvent.EventType.ONLINE,
            timestamp=now,
            details={"ip": host.ip, "last_seen": device.last_seen.isoformat()},
        )
        summary.came_online.append(device.mac)

    device.ip = host.ip
    if host.hostname:
        device.hostname = host.hostname
    if host.vendor and not device.vendor:
        device.vendor = host.vendor
    device.is_online = True
    device.missed_scans = 0
    device.last_seen = now
    device.save(update_fields=["ip", "hostname", "vendor", "is_online", "missed_scans", "last_seen"])


def _in_network(ip: str | None, network: ipaddress.IPv4Network) -> bool:
    if not ip:
        return True
    try:
        return ipaddress.ip_address(ip) in network
    except ValueError:
        return False


@transaction.atomic
def process_scan_results(
    hosts: Iterable[DiscoveredHost],
    subnet: str,
    *,
    offline_after: int = 3,
    now: datetime | None = None,
) -> ScanSummary:
    """Apply one successful scan of ``subnet`` to the database.

    * Unseen MACs become unknown devices and raise an alert.
    * Seen devices are marked online; IP changes are recorded.
    * Online LAN devices in ``subnet`` that did not answer accrue a missed scan
      and go offline after ``offline_after`` consecutive misses.

    Only call this for scans that actually succeeded; a failed scan must not
    count as everyone missing.
    """
    now = now or timezone.now()
    network = ipaddress.ip_network(subnet, strict=False)
    hosts = {h.mac: h for h in hosts}
    summary = ScanSummary(seen=len(hosts))

    existing = {d.mac: d for d in Device.objects.select_for_update().filter(mac__in=list(hosts))}
    for mac, host in hosts.items():
        device = existing.get(mac)
        if device is None:
            _create_new_device(host, now)
            summary.new_devices.append(mac)
        else:
            _update_seen_device(device, host, now, summary)

    missing = (
        Device.objects.select_for_update()
        .filter(source=Device.Source.LAN, is_online=True)
        .exclude(mac__in=list(hosts))
    )
    for device in missing:
        if not _in_network(device.ip, network):
            continue
        device.missed_scans += 1
        if device.missed_scans >= offline_after:
            device.is_online = False
            DeviceEvent.objects.create(
                device=device,
                event_type=DeviceEvent.EventType.OFFLINE,
                timestamp=now,
                details={"ip": device.ip, "last_seen": device.last_seen.isoformat()},
            )
            summary.went_offline.append(device.mac)
        device.save(update_fields=["missed_scans", "is_online"])

    return summary
