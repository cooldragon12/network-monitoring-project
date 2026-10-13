import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from devices.models import ScanRun
from devices.services import process_scan_results
from scanner.arp_scan import ScanError, detect_local_subnet, scan
from scanner.oui import OuiDatabase

logger = logging.getLogger(__name__)

# On-demand scans: a cache flag marks "queued", cleared when the worker starts.
# The queued task expires with the flag, so a scan requested while the scanner
# worker is down does not fire much later.
SCAN_REQUEST_KEY = "lan-scan:requested"
SCAN_REQUEST_TTL = 120
# A RUNNING ScanRun older than this is assumed dead (worker crashed mid-scan).
RUNNING_SCAN_MAX_AGE = timedelta(minutes=5)


class ScanAlreadyPending(Exception):
    pass


class ScanQueueUnavailable(Exception):
    pass


def scan_in_progress() -> bool:
    return ScanRun.objects.filter(
        status=ScanRun.Status.RUNNING, started_at__gte=timezone.now() - RUNNING_SCAN_MAX_AGE
    ).exists()


def scan_requested() -> bool:
    return cache.get(SCAN_REQUEST_KEY) is not None


def request_lan_scan() -> None:
    """Queue an immediate scan on the scanner worker (never runs in-process)."""
    if scan_in_progress() or not cache.add(SCAN_REQUEST_KEY, True, timeout=SCAN_REQUEST_TTL):
        raise ScanAlreadyPending
    try:
        run_lan_scan.apply_async(expires=SCAN_REQUEST_TTL)
    except Exception as exc:  # broker down / misconfigured
        cache.delete(SCAN_REQUEST_KEY)
        raise ScanQueueUnavailable(str(exc)) from exc


def _finish(run: ScanRun, status: str, *, devices_found: int = 0, error: str = "") -> None:
    run.status = status
    run.devices_found = devices_found
    run.error = error
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "devices_found", "error", "finished_at"])


def _start_run(subnet: str) -> ScanRun:
    run = ScanRun.objects.create(subnet=subnet)
    # Clear the on-demand flag only once the run is visible, so the dashboard
    # never sees "not queued" and "no new scan" at the same time.
    cache.delete(SCAN_REQUEST_KEY)
    return run


def perform_lan_scan() -> dict:
    """Scan the configured subnet and record the results.

    Runs only on the scanner worker (or the ``scan_now`` command), never in a
    web request. A failed or empty scan is recorded but does not count as a
    missed scan for any device.
    """
    cfg = settings.SCANNER
    subnet, iface = cfg["SUBNET"], cfg["INTERFACE"]
    if not subnet:
        try:
            subnet, detected_iface = detect_local_subnet()
        except ScanError as exc:
            logger.error("Subnet auto-detection failed: %s", exc)
            run = _start_run("(auto-detect)")
            _finish(run, ScanRun.Status.FAILED, error=str(exc))
            return {"status": run.status, "error": run.error}
        iface = iface or detected_iface

    run = _start_run(subnet)
    try:
        hosts = scan(
            subnet,
            iface=iface,
            timeout=cfg["TIMEOUT_SECONDS"],
            vendor_db=OuiDatabase.load_cached(),
        )
    except ScanError as exc:
        logger.error("LAN scan of %s failed: %s", subnet, exc)
        _finish(run, ScanRun.Status.FAILED, error=str(exc))
        return {"status": run.status, "error": run.error}
    except Exception as exc:
        _finish(run, ScanRun.Status.FAILED, error=f"{type(exc).__name__}: {exc}")
        raise

    if not hosts:
        # At minimum the router should answer; an empty result usually means the
        # interface is down or misconfigured, not that every device left.
        _finish(run, ScanRun.Status.FAILED, error="no ARP replies (interface down or wrong subnet?)")
        return {"status": run.status, "error": run.error}

    summary = process_scan_results(hosts, subnet, offline_after=cfg["OFFLINE_AFTER_MISSED_SCANS"])
    _finish(run, ScanRun.Status.SUCCESS, devices_found=len(hosts))
    logger.info("Scan of %s: %s", subnet, summary.as_dict())
    return {"status": run.status, "scan_run": run.pk, **summary.as_dict()}


@shared_task(name="devices.tasks.run_lan_scan")
def run_lan_scan() -> dict:
    return perform_lan_scan()
