"""Pure-stdlib ARP scanning for Linux via AF_PACKET raw sockets.

No scapy: importing this costs almost nothing, which keeps the scanner small
enough for a Raspberry Pi or NAS. Needs CAP_NET_RAW (or root).
"""

from __future__ import annotations

import ipaddress
import select
import socket
import struct
import time

ETH_P_ARP = 0x0806
ARP_REQUEST = 1
ARP_REPLY = 2
BROADCAST_MAC = b"\xff" * 6
# Linux ioctls for interface addresses.
SIOCGIFADDR = 0x8915
SIOCGIFNETMASK = 0x891B
RTF_GATEWAY = 0x2
_VIRTUAL_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "tun", "tap", "wg", "tailscale", "zt")
# Pause briefly every N requests so switches/APs don't drop a burst.
_BATCH = 64


def available() -> bool:
    return hasattr(socket, "AF_PACKET")


def default_interface(route_table: str | None = None) -> str | None:
    """Interface of the IPv4 default route, from /proc/net/route."""
    if route_table is None:
        try:
            with open("/proc/net/route") as fh:
                route_table = fh.read()
        except OSError:
            return None
    rows = [line.split() for line in route_table.splitlines()[1:]]
    rows = [r for r in rows if len(r) >= 8]
    for fields in rows:
        if fields[1] == "00000000" and int(fields[3], 16) & RTF_GATEWAY:
            return fields[0]
    # No default route (isolated LAN): first directly connected, non-virtual interface.
    for fields in rows:
        iface, gateway, mask = fields[0], fields[2], fields[7]
        if gateway == "00000000" and mask != "FFFFFFFF" and not iface.startswith(_VIRTUAL_PREFIXES):
            return iface
    return None


def _ioctl_ipv4(iface: str, request: int) -> str:
    import fcntl  # Linux/Unix only; imported lazily so the module loads anywhere

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        packed = struct.pack("256s", iface.encode()[:15])
        return socket.inet_ntoa(fcntl.ioctl(sock.fileno(), request, packed)[20:24])


def interface_network(iface: str) -> tuple[str, ipaddress.IPv4Network]:
    """Return (interface IP, connected network) for ``iface``."""
    ip = _ioctl_ipv4(iface, SIOCGIFADDR)
    mask = _ioctl_ipv4(iface, SIOCGIFNETMASK)
    return ip, ipaddress.IPv4Interface(f"{ip}/{mask}").network


def build_request(src_mac: bytes, src_ip: bytes, target_ip: bytes) -> bytes:
    """Ethernet broadcast frame carrying an ARP who-has for ``target_ip``."""
    ethernet = BROADCAST_MAC + src_mac + struct.pack("!H", ETH_P_ARP)
    arp = struct.pack(
        "!HHBBH6s4s6s4s",
        1,  # hardware type: Ethernet
        0x0800,  # protocol type: IPv4
        6,
        4,
        ARP_REQUEST,
        src_mac,
        src_ip,
        b"\x00" * 6,
        target_ip,
    )
    return ethernet + arp


def parse_reply(frame: bytes) -> tuple[str, str] | None:
    """Return (mac, ip) of the sender if ``frame`` is an ARP reply."""
    if len(frame) < 42 or struct.unpack("!H", frame[12:14])[0] != ETH_P_ARP:
        return None
    _htype, ptype, hlen, plen, op = struct.unpack("!HHBBH", frame[14:22])
    if op != ARP_REPLY or ptype != 0x0800 or hlen != 6 or plen != 4:
        return None
    mac = ":".join(f"{b:02x}" for b in frame[22:28])
    return mac, socket.inet_ntoa(frame[28:32])


def _collect(sock, found: dict[str, str], network: ipaddress.IPv4Network, timeout: float) -> None:
    """Read replies into ``found`` (ip -> mac) until ``timeout`` passes quietly."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = max(0.0, deadline - time.monotonic())
        readable, _, _ = select.select([sock], [], [], remaining)
        if not readable:
            return
        parsed = parse_reply(sock.recv(2048))
        if parsed and ipaddress.IPv4Address(parsed[1]) in network:
            found.setdefault(parsed[1], parsed[0])


def arp_scan(
    network: ipaddress.IPv4Network, iface: str | None = None, timeout: float = 2.0, retries: int = 1
) -> list[tuple[str, str]]:
    """ARP-sweep ``network`` and return (mac, ip) pairs. Raises PermissionError without CAP_NET_RAW."""
    iface = iface or default_interface()
    if not iface:
        raise OSError("no default-route interface found; pass an interface explicitly")
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ARP))
    try:
        sock.bind((iface, 0))
        src_mac = sock.getsockname()[4]
        own_ip = _ioctl_ipv4(iface, SIOCGIFADDR)
        src_ip = socket.inet_aton(own_ip)
        targets = [h for h in network.hosts() if str(h) != own_ip]
        found: dict[str, str] = {}
        for _attempt in range(retries + 1):
            pending = [t for t in targets if str(t) not in found]
            if not pending:
                break
            for i, target in enumerate(pending, 1):
                sock.send(build_request(src_mac, src_ip, target.packed))
                if i % _BATCH == 0:
                    _collect(sock, found, network, 0.002)
            _collect(sock, found, network, timeout)
        return [(mac, ip) for ip, mac in found.items()]
    finally:
        sock.close()
