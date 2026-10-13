"""ARP-based discovery of hosts on a local IPv4 subnet.

Sending raw ARP frames needs elevated privileges (root, CAP_NET_RAW on Linux,
or read/write access to /dev/bpf* on macOS). See README.md for details.

Two backends are supported:
  * ``scapy``    – pure Python, the default.
  * ``arp-scan`` – the external ``arp-scan`` binary, if installed.
"""

from __future__ import annotations

import ipaddress
import logging
import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from typing import Iterable

from scanner.oui import OuiDatabase, is_locally_administered, mac_hex

logger = logging.getLogger(__name__)

BACKENDS = ("auto", "scapy", "arp-scan")

# Refuse to sweep anything larger than this without allow_large=True.
# A /22 is 1022 hosts, already bigger than a typical home LAN.
MIN_PREFIX_LEN = 22


class ScanError(Exception):
    """The scan could not be performed."""


class PermissionDeniedError(ScanError):
    """The process lacks the privileges needed to send raw ARP packets."""


@dataclass(frozen=True)
class DiscoveredHost:
    mac: str
    ip: str
    hostname: str | None = None
    vendor: str | None = None
    # Randomized/private MAC (no vendor; may rotate for the same device).
    locally_administered: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_mac(mac: str) -> str:
    """Return ``aa:bb:cc:dd:ee:ff`` form, or raise ValueError."""
    digits = mac_hex(mac).lower()
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def validate_subnet(
    subnet: str, *, allow_large: bool = False, allow_public: bool = False
) -> ipaddress.IPv4Network:
    """Parse and sanity-check the subnet to scan.

    Guards against accidentally sweeping huge or non-private ranges: this tool
    is meant for networks you own.
    """
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError as exc:
        raise ScanError(f"invalid subnet {subnet!r}: {exc}") from exc
    if not isinstance(network, ipaddress.IPv4Network):
        raise ScanError("only IPv4 subnets are supported (ARP is IPv4-only)")
    if network.prefixlen < MIN_PREFIX_LEN and not allow_large:
        raise ScanError(
            f"{network} has {network.num_addresses} addresses; refusing to scan "
            f"anything larger than /{MIN_PREFIX_LEN} without allow_large"
        )
    if not network.is_private and not allow_public:
        raise ScanError(f"{network} is not a private range; refusing without allow_public")
    if network.is_loopback or network.is_link_local or network.is_multicast:
        raise ScanError(f"{network} is not a scannable LAN subnet")
    return network


def pick_connected_network(
    routes: Iterable[tuple[int, int, str, str, str, int]], local_ip: str
) -> ipaddress.IPv4Network | None:
    """Pick the directly connected subnet containing ``local_ip``.

    ``routes`` uses scapy's routing-table tuple format:
    (net, mask, gateway, iface, output_ip, metric), with net/mask as ints.
    """
    addr = ipaddress.IPv4Address(local_ip)
    best: ipaddress.IPv4Network | None = None
    for net, mask, gateway, _iface, output_ip, _metric in routes:
        if gateway != "0.0.0.0" or output_ip != local_ip:
            continue
        try:
            candidate = ipaddress.IPv4Network(
                (net, str(ipaddress.IPv4Address(mask))), strict=False
            )
        except ValueError:
            continue
        # Skip host routes and the default route.
        if not 0 < candidate.prefixlen < 32 or addr not in candidate:
            continue
        if best is None or candidate.prefixlen > best.prefixlen:
            best = candidate
    return best


def detect_local_subnet() -> tuple[str, str]:
    """Return ``(subnet, interface)`` for the default-route interface."""
    from scapy.all import conf

    iface, local_ip, _gateway = conf.route.route("0.0.0.0")
    if not local_ip or local_ip == "0.0.0.0":
        raise ScanError("could not determine the local IP address (no default route?)")
    network = pick_connected_network(conf.route.routes, local_ip)
    if network is None:
        raise ScanError(f"could not determine the subnet for {local_ip} on {iface}")
    return str(network), str(iface)


def _looks_like_permission_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    text = str(exc).lower()
    return "permission" in text or "operation not permitted" in text


def _scapy_scan(
    network: ipaddress.IPv4Network, iface: str | None, timeout: float, retries: int
) -> list[tuple[str, str]]:
    from scapy.all import ARP, Ether, srp

    packet = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=str(network))
    try:
        answered, _ = srp(packet, iface=iface, timeout=timeout, retry=retries, verbose=False)
    except Exception as exc:
        if _looks_like_permission_error(exc):
            raise PermissionDeniedError(
                "raw socket access denied: run with sudo or grant capture "
                "privileges (see README 'Scanner privileges')"
            ) from exc
        raise ScanError(f"scapy scan failed: {exc}") from exc
    return [(received.hwsrc, received.psrc) for _sent, received in answered]


def parse_arp_scan_output(text: str) -> list[tuple[str, str]]:
    """Parse ``arp-scan --quiet --plain`` output into (mac, ip) pairs."""
    pairs = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        ip, mac = parts[0], parts[1]
        try:
            ipaddress.IPv4Address(ip)
            normalize_mac(mac)
        except ValueError:
            continue
        pairs.append((mac, ip))
    return pairs


def _arp_scan_cli(
    network: ipaddress.IPv4Network, iface: str | None, timeout: float, retries: int
) -> list[tuple[str, str]]:
    exe = shutil.which("arp-scan")
    if not exe:
        raise ScanError("arp-scan binary not found on PATH")
    cmd = [
        exe,
        "--quiet",
        "--plain",
        f"--timeout={int(timeout * 1000)}",
        f"--retry={retries + 1}",
    ]
    if iface:
        cmd.append(f"--interface={iface}")
    cmd.append(str(network))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout * (retries + 1) + 60)
    except subprocess.TimeoutExpired as exc:
        raise ScanError("arp-scan timed out") from exc
    if proc.returncode != 0:
        if _looks_like_permission_error(Exception(proc.stderr)):
            raise PermissionDeniedError(f"arp-scan needs root: {proc.stderr.strip()}")
        raise ScanError(f"arp-scan failed ({proc.returncode}): {proc.stderr.strip()}")
    return parse_arp_scan_output(proc.stdout)


def _scapy_available() -> bool:
    try:
        import scapy  # noqa: F401
    except ImportError:
        return False
    return True


def resolve_hostnames(ips: Iterable[str], timeout: float = 3.0) -> dict[str, str]:
    """Reverse-DNS lookup for ``ips`` in parallel, bounded by ``timeout`` overall."""
    ips = list(ips)
    if not ips:
        return {}
    pool = ThreadPoolExecutor(max_workers=min(32, len(ips)))
    futures = {pool.submit(socket.gethostbyaddr, ip): ip for ip in ips}
    done, _ = wait(futures, timeout=timeout)
    pool.shutdown(wait=False, cancel_futures=True)
    names = {}
    for future in done:
        try:
            names[futures[future]] = future.result()[0]
        except OSError:
            pass
    return names


def scan(
    subnet: str,
    *,
    backend: str = "auto",
    iface: str | None = None,
    timeout: float = 2.0,
    retries: int = 1,
    resolve: bool = True,
    vendor_db: OuiDatabase | None = None,
    allow_large: bool = False,
    allow_public: bool = False,
) -> list[DiscoveredHost]:
    """ARP-scan ``subnet`` and return one entry per responding MAC, sorted by IP."""
    network = validate_subnet(subnet, allow_large=allow_large, allow_public=allow_public)

    if backend not in BACKENDS:
        raise ScanError(f"unknown backend {backend!r}; choose from {BACKENDS}")
    if backend == "auto":
        backend = "scapy" if _scapy_available() else "arp-scan"
    scan_fn = _scapy_scan if backend == "scapy" else _arp_scan_cli
    logger.info("Scanning %s with %s (iface=%s)", network, backend, iface or "default")
    raw_pairs = scan_fn(network, iface, timeout, retries)

    by_mac: dict[str, str] = {}
    for raw_mac, ip in raw_pairs:
        try:
            mac = normalize_mac(raw_mac)
            addr = ipaddress.IPv4Address(ip)
        except ValueError:
            logger.debug("Ignoring malformed reply %r / %r", raw_mac, ip)
            continue
        if addr not in network:
            continue
        if mac in by_mac and by_mac[mac] != ip:
            # Usually proxy ARP or a multi-homed device; keep the first reply.
            logger.debug("MAC %s answered for %s and %s", mac, by_mac[mac], ip)
            continue
        by_mac.setdefault(mac, ip)

    hostnames = resolve_hostnames(by_mac.values()) if resolve else {}
    if vendor_db is None:
        vendor_db = OuiDatabase.load()

    hosts = [
        DiscoveredHost(
            mac=mac,
            ip=ip,
            hostname=hostnames.get(ip),
            vendor=vendor_db.lookup(mac),
            locally_administered=is_locally_administered(mac),
        )
        for mac, ip in by_mac.items()
    ]
    hosts.sort(key=lambda h: ipaddress.IPv4Address(h.ip))
    return hosts
