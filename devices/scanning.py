"""Server side of the LAN scanner agent protocol.

The scanner agent (``python -m scanner agent``) runs on a machine on the LAN
and never touches the database. It heartbeats over HTTP; the server decides
when a scan is due (schedule or "Scan now"), the agent scans and reports the
results, and the server applies them here. Scans therefore never run inside
the web app.
"""

from __future__ import annotations

import ipaddress
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from devices.models import ScanRun
from devices.services import ScanSummary, process_scan_results
from monitoring.models import RemoteHost
from scanner.arp_scan import DiscoveredHost, ScanError, normalize_mac, validate_subnet
from scanner.oui import OuiDatabase, is_locally_administered

logger = logging.getLogger(__name__)

# A "Scan now" request is honoured for this long; afterwards it lapses.
SCAN_REQUEST_TTL = timedelta(minutes=2)
# A RUNNING ScanRun older than this is assumed dead (scanner crashed mid-scan).
RUNNING_SCAN_MAX_AGE = timedelta(minutes=5)
MAX_HOSTS_PER_REPORT = 4096


class ScanAlreadyPending(Exception):
    pass


class NoScannerOnline(Exception):
    pass


class ReportError(Exception):
    pass


def online_scanners():
    return RemoteHost.objects.filter(is_active=True, is_scanner=True, status=RemoteHost.Status.ONLINE)


def scan_in_progress() -> bool:
    return ScanRun.objects.filter(
        status=ScanRun.Status.RUNNING, started_at__gte=timezone.now() - RUNNING_SCAN_MAX_AGE
    ).exists()


def scan_requested() -> bool:
    return RemoteHost.objects.filter(
        is_active=True, is_scanner=True, scan_requested_at__gte=timezone.now() - SCAN_REQUEST_TTL
    ).exists()


def request_lan_scan() -> int:
    """Ask every online scanner to scan at its next heartbeat. Returns how many."""
    with transaction.atomic():
        scanners = list(online_scanners().select_for_update())
        if not scanners:
            raise NoScannerOnline
        if scan_in_progress() or scan_requested():
            raise ScanAlreadyPending
        RemoteHost.objects.filter(pk__in=[s.pk for s in scanners]).update(scan_requested_at=timezone.now())
    return len(scanners)


def scanner_subnet(host: RemoteHost) -> str | None:
    """Subnet the server wants this scanner to sweep (None = let it auto-detect)."""
    return host.scan_subnet or settings.SCANNER["SUBNET"] or None


def scan_due(host: RemoteHost, now=None) -> bool:
    """True if a manual request is pending or the scheduled interval has elapsed."""
    now = now or timezone.now()
    if host.scan_requested_at and now - host.scan_requested_at <= SCAN_REQUEST_TTL:
        return True
    last = ScanRun.objects.filter(scanner=host).values_list("started_at", flat=True).first()
    return last is None or now - last >= timedelta(minutes=settings.SCANNER["INTERVAL_MINUTES"])


def heartbeat_response(host: RemoteHost) -> dict:
    cfg = settings.SCANNER
    return {
        "scan_now": scan_due(host) and not _running_for(host),
        "subnet": scanner_subnet(host),
        "timeout_seconds": cfg["TIMEOUT_SECONDS"],
        "heartbeat_seconds": cfg["HEARTBEAT_SECONDS"],
    }


def _running_for(host: RemoteHost) -> bool:
    return ScanRun.objects.filter(
        scanner=host, status=ScanRun.Status.RUNNING, started_at__gte=timezone.now() - RUNNING_SCAN_MAX_AGE
    ).exists()


def start_scan(host: RemoteHost, subnet: str) -> ScanRun:
    """Open a ScanRun for a scanner that is about to sweep ``subnet``."""
    expected = scanner_subnet(host)
    try:
        network = validate_subnet(subnet)
    except ScanError as exc:
        raise ReportError(str(exc)) from exc
    if expected and ipaddress.ip_network(expected, strict=False) != network:
        raise ReportError(f"this scanner is configured for {expected}, not {network}")
    with transaction.atomic():
        run = ScanRun.objects.create(subnet=str(network), scanner=host)
        RemoteHost.objects.filter(pk=host.pk).update(scan_requested_at=None)
    return run


def _finish(run: ScanRun, status: str, *, devices_found: int = 0, error: str = "") -> None:
    run.status = status
    run.devices_found = devices_found
    run.error = error[:2000]
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "devices_found", "error", "finished_at"])


def parse_hosts(raw_hosts: list, network: ipaddress.IPv4Network) -> list[DiscoveredHost]:
    """Validate agent-supplied hosts. Vendor and random-MAC flags are derived
    server-side (the agent stays small and is not trusted for them)."""
    if not isinstance(raw_hosts, list) or len(raw_hosts) > MAX_HOSTS_PER_REPORT:
        raise ReportError("hosts must be a list of at most %d entries" % MAX_HOSTS_PER_REPORT)
    vendors = OuiDatabase.load_cached()
    seen: dict[str, DiscoveredHost] = {}
    for item in raw_hosts:
        if not isinstance(item, dict):
            raise ReportError("each host must be an object")
        try:
            mac = normalize_mac(str(item.get("mac", "")))
            ip = ipaddress.IPv4Address(str(item.get("ip", "")))
        except ValueError as exc:
            raise ReportError(f"invalid host entry: {exc}") from exc
        if ip not in network or mac in seen:
            continue
        hostname = item.get("hostname") or None
        seen[mac] = DiscoveredHost(
            mac=mac,
            ip=str(ip),
            hostname=str(hostname)[:255] if hostname else None,
            vendor=vendors.lookup(mac),
            locally_administered=is_locally_administered(mac),
        )
    return list(seen.values())


def finish_scan(run: ScanRun, *, ok: bool, raw_hosts=None, error: str = "") -> ScanSummary | None:
    """Apply an agent's report to an open ScanRun."""
    if run.status != ScanRun.Status.RUNNING:
        raise ReportError("this scan run is already finished")
    if not ok:
        _finish(run, ScanRun.Status.FAILED, error=error or "scanner reported a failure")
        return None
    network = ipaddress.ip_network(run.subnet, strict=False)
    try:
        hosts = parse_hosts(raw_hosts if raw_hosts is not None else [], network)
    except ReportError as exc:
        _finish(run, ScanRun.Status.FAILED, error=f"invalid report: {exc}")
        raise
    if not hosts:
        # At minimum the router answers; empty usually means the interface is
        # down, not that every device left. Never count it as a miss.
        _finish(run, ScanRun.Status.FAILED, error="no ARP replies (interface down or wrong subnet?)")
        return None
    summary = process_scan_results(hosts, run.subnet, offline_after=settings.SCANNER["OFFLINE_AFTER_MISSED_SCANS"])
    _finish(run, ScanRun.Status.SUCCESS, devices_found=len(hosts))
    logger.info("Scan %s of %s: %s", run.pk, run.subnet, summary.as_dict())
    return summary


def fail_stale_runs(now=None) -> int:
    """Close RUNNING scans whose scanner never reported back."""
    now = now or timezone.now()
    return ScanRun.objects.filter(status=ScanRun.Status.RUNNING, started_at__lt=now - RUNNING_SCAN_MAX_AGE).update(
        status=ScanRun.Status.FAILED, finished_at=now, error="scanner did not report results"
    )


def scan_locally() -> dict:
    """One-off scan in *this* process (``manage.py scan_now``, for debugging).

    Needs raw-socket privileges here; normal operation uses the scanner agent.
    """
    from scanner.arp_scan import detect_local_subnet, scan

    cfg = settings.SCANNER
    subnet, iface = cfg["SUBNET"], cfg["INTERFACE"]
    try:
        if not subnet:
            subnet, iface = detect_local_subnet()
        run = ScanRun.objects.create(subnet=str(validate_subnet(subnet)))
    except ScanError as exc:
        return {"status": "failed", "error": str(exc)}
    try:
        hosts = scan(subnet, iface=iface, timeout=cfg["TIMEOUT_SECONDS"], vendor_db=OuiDatabase())
    except ScanError as exc:
        finish_scan(run, ok=False, error=str(exc))
        return {"status": "failed", "error": str(exc)}
    summary = finish_scan(run, ok=True, raw_hosts=[h.to_dict() for h in hosts])
    run.refresh_from_db()
    return {"status": run.status, "error": run.error, **(summary.as_dict() if summary else {})}
