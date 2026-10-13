"""Server-side host checks: ICMP ping, TCP connect, HTTP GET. Stdlib only."""

from __future__ import annotations

import logging
import os
import socket
import struct
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from monitoring.models import Alert, HostCheck, HostEvent

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    ok: bool
    latency_ms: float | None = None
    error: str = ""


def _icmp_checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def build_echo_request(seq: int, payload: bytes = b"netmon-ping") -> bytes:
    header = struct.pack("!BBHHH", 8, 0, 0, 0, seq)
    checksum = _icmp_checksum(header + payload)
    return struct.pack("!BBHHH", 8, 0, checksum, 0, seq) + payload


def ping(target: str, timeout: float) -> CheckResult:
    """ICMP echo via an unprivileged datagram socket (Linux ping_group_range, macOS)."""
    try:
        address = socket.gethostbyname(target)
    except OSError as exc:
        return CheckResult(False, error=f"cannot resolve {target}: {exc}")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except PermissionError:
        return CheckResult(False, error="ICMP not permitted for this process (net.ipv4.ping_group_range)")
    seq = int.from_bytes(os.urandom(2), "big")
    try:
        sock.settimeout(timeout)
        start = time.monotonic()
        sock.sendto(build_echo_request(seq), (address, 0))
        deadline = start + timeout
        while True:
            sock.settimeout(max(0.01, deadline - time.monotonic()))
            data, _ = sock.recvfrom(1024)
            # macOS includes the IP header on datagram ICMP sockets; Linux doesn't.
            if data and data[0] >> 4 == 4:
                data = data[(data[0] & 0x0F) * 4 :]
            if len(data) >= 8 and data[0] == 0 and struct.unpack("!H", data[6:8])[0] == seq:
                return CheckResult(True, (time.monotonic() - start) * 1000)
    except TimeoutError:
        return CheckResult(False, error=f"no reply within {timeout:g}s")
    except OSError as exc:
        return CheckResult(False, error=str(exc))
    finally:
        sock.close()


def tcp_connect(target: str, port: int, timeout: float) -> CheckResult:
    start = time.monotonic()
    try:
        with socket.create_connection((target, port), timeout=timeout):
            return CheckResult(True, (time.monotonic() - start) * 1000)
    except OSError as exc:
        return CheckResult(False, error=f"{target}:{port}: {exc}")


def http_get(url: str, timeout: float) -> CheckResult:
    if not url.startswith(("http://", "https://")):
        return CheckResult(False, error="target must start with http:// or https://")
    request = urllib.request.Request(url, headers={"User-Agent": "netmon-check/1.0"})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            resp.read(1024)
            status = resp.status
    except urllib.error.HTTPError as exc:
        return CheckResult(False, (time.monotonic() - start) * 1000, f"HTTP {exc.code}")
    except (urllib.error.URLError, OSError) as exc:
        return CheckResult(False, error=str(getattr(exc, "reason", exc)))
    latency = (time.monotonic() - start) * 1000
    return CheckResult(status < 400, latency, "" if status < 400 else f"HTTP {status}")


def execute(check: HostCheck) -> CheckResult:
    timeout = float(check.timeout_seconds)
    if check.kind == HostCheck.Kind.PING:
        return ping(check.target, timeout)
    if check.kind == HostCheck.Kind.TCP:
        if not check.port:
            return CheckResult(False, error="TCP check needs a port")
        return tcp_connect(check.target, check.port, timeout)
    return http_get(check.target, timeout)


def apply_result(check: HostCheck, result: CheckResult, now=None) -> None:
    """Store a result; alert when a check goes down, record when it recovers."""
    now = now or timezone.now()
    with transaction.atomic():
        check = HostCheck.objects.select_for_update().select_related("host").get(pk=check.pk)
        was_down = check.status == HostCheck.Status.DOWN
        check.last_checked_at = now
        check.last_latency_ms = result.latency_ms
        check.last_error = result.error[:1000]
        if result.ok:
            check.consecutive_failures = 0
            check.status = HostCheck.Status.UP
            if was_down:
                HostEvent.objects.create(
                    host=check.host,
                    event_type=HostEvent.EventType.CHECK_RECOVERED,
                    timestamp=now,
                    details={"check": check.describe()},
                )
        else:
            check.consecutive_failures += 1
            if check.consecutive_failures >= check.failure_threshold:
                check.status = HostCheck.Status.DOWN
                if not was_down:
                    HostEvent.objects.create(
                        host=check.host,
                        event_type=HostEvent.EventType.CHECK_FAILED,
                        timestamp=now,
                        details={"check": check.describe(), "error": result.error},
                    )
                    Alert.objects.create(
                        remote_host=check.host,
                        kind=Alert.Kind.CHECK_FAILED,
                        severity=Alert.Severity.WARNING,
                        message=f"{check.describe()} on '{check.host.name}' is failing: {result.error}",
                        created_at=now,
                    )
        check.save()


def due_checks(now=None):
    now = now or timezone.now()
    for check in HostCheck.objects.filter(enabled=True, host__is_active=True):
        if check.last_checked_at is None or now - check.last_checked_at >= timedelta(seconds=check.interval_seconds):
            yield check


def run_due_checks(max_workers: int = 16) -> int:
    checks = list(due_checks())
    if not checks:
        return 0
    with ThreadPoolExecutor(max_workers=min(max_workers, len(checks))) as pool:
        results = list(pool.map(execute, checks))
    for check, result in zip(checks, results):
        apply_result(check, result)
    return len(checks)
