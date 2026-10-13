import ipaddress
import socket
import struct
from pathlib import Path

import pytest

from devices.models import Device, ScanRun
from monitoring.models import RemoteHost
from scanner import agent as agent_mod
from scanner import linux_arp
from scanner.agent import AgentConfig, AgentHTTPError, ApiClient, ConfigError, ScannerAgent, load_config
from scanner.arp_scan import DiscoveredHost, PermissionDeniedError
from scanner.oui import OuiDatabase

FIXTURE_DB = Path(__file__).parent / "fixtures" / "oui_sample.csv"


# --- Config -------------------------------------------------------------------------------

def test_config_from_env_and_file_precedence(tmp_path):
    env = {"NETMON_SERVER_URL": "http://env:8000", "NETMON_TOKEN": "env-token", "NETMON_BACKEND": "scapy"}
    cfg = load_config(env=env)
    assert (cfg.server_url, cfg.token, cfg.backend) == ("http://env:8000", "env-token", "scapy")

    conf = tmp_path / "scanner.env"
    conf.write_text("# comment\nNETMON_SERVER_URL=http://file:8000/\nexport NETMON_TOKEN='file-token'\n")
    cfg = load_config(str(conf), env=env)
    assert (cfg.server_url, cfg.token) == ("http://file:8000", "file-token")

    cfg = load_config(str(conf), env=env, NETMON_SERVER_URL="https://cli:443", NETMON_INTERFACE="eth0")
    assert cfg.server_url == "https://cli:443" and cfg.interface == "eth0"


def test_config_token_file(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret-token\n")
    cfg = load_config(env={"NETMON_SERVER_URL": "http://x", "NETMON_TOKEN_FILE": str(token_file)})
    assert cfg.token == "secret-token"


@pytest.mark.parametrize(
    "env,needle",
    [
        ({"NETMON_TOKEN": "t"}, "NETMON_SERVER_URL"),
        ({"NETMON_SERVER_URL": "http://x"}, "NETMON_TOKEN"),
        ({"NETMON_SERVER_URL": "ftp://x", "NETMON_TOKEN": "t"}, "NETMON_SERVER_URL"),
        ({"NETMON_SERVER_URL": "http://x", "NETMON_TOKEN": "t", "NETMON_BACKEND": "magic"}, "NETMON_BACKEND"),
    ],
)
def test_config_errors(env, needle):
    with pytest.raises(ConfigError, match=needle):
        load_config(env=env)


# --- Linux raw backend (pure functions; run on any OS) ----------------------------------------

def test_arp_request_frame_layout():
    src_mac = bytes.fromhex("0242ac110002")
    frame = linux_arp.build_request(src_mac, socket.inet_aton("192.168.31.234"), socket.inet_aton("192.168.31.1"))
    assert len(frame) == 42
    assert frame[:6] == b"\xff" * 6 and frame[6:12] == src_mac
    assert struct.unpack("!H", frame[12:14])[0] == 0x0806
    assert struct.unpack("!H", frame[20:22])[0] == 1  # request
    assert frame[38:42] == socket.inet_aton("192.168.31.1")


def _reply(mac_hex: str, ip: str, op: int = 2) -> bytes:
    sender = bytes.fromhex(mac_hex)
    eth = b"\x02" * 6 + sender + struct.pack("!H", 0x0806)
    arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, op, sender, socket.inet_aton(ip), b"\x02" * 6,
                      socket.inet_aton("192.168.31.234"))
    return eth + arp + b"\x00" * 18  # Ethernet padding


def test_parse_arp_reply():
    assert linux_arp.parse_reply(_reply("b827ebaabbcc", "192.168.31.20")) == ("b8:27:eb:aa:bb:cc", "192.168.31.20")
    assert linux_arp.parse_reply(_reply("b827ebaabbcc", "192.168.31.20", op=1)) is None  # a request
    assert linux_arp.parse_reply(b"\x00" * 20) is None
    ipv4 = b"\x00" * 12 + struct.pack("!H", 0x0800) + b"\x00" * 40
    assert linux_arp.parse_reply(ipv4) is None


def test_default_interface_from_proc_route():
    table = (
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n"
        "eth0\t001FA8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\n"
        "wlan0\t00000000\t011FA8C0\t0003\t0\t0\t600\t00000000\n"
    )
    assert linux_arp.default_interface(table) == "wlan0"
    assert linux_arp.default_interface("Iface\tDestination\n") is None


def test_default_interface_fallback_without_gateway():
    # Docker Desktop VM / isolated LAN: no default route.
    table = (
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n"
        "lo\t0000007F\t00000000\t0001\t0\t0\t0\t000000FF\n"
        "docker0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\n"
        "eth0\t0041A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\n"
        "services1\t0741A8C0\t00000000\t0005\t0\t0\t0\tFFFFFFFF\n"
    )
    assert linux_arp.default_interface(table) == "eth0"


@pytest.mark.skipif(not linux_arp.available(), reason="AF_PACKET is Linux-only")
def test_raw_backend_without_privileges_raises_permission_denied():
    import os

    if os.geteuid() == 0:
        pytest.skip("running as root")
    from scanner.arp_scan import _raw_scan

    with pytest.raises(PermissionDeniedError):
        _raw_scan(ipaddress.ip_network("192.168.31.0/30"), None, 0.1, 0)


# --- End to end against a live Django server ----------------------------------------------

def fake_scan(hosts):
    calls = []

    def scan_fn(subnet, **kwargs):
        calls.append({"subnet": subnet, **kwargs})
        return hosts

    scan_fn.calls = calls
    return scan_fn


@pytest.fixture
def live_scanner(live_server, settings, monkeypatch):
    settings.SCANNER = {**settings.SCANNER, "SUBNET": "192.168.31.0/24", "INTERVAL_MINUTES": 2}
    monkeypatch.setattr(OuiDatabase, "load_cached", classmethod(lambda cls, path=None: OuiDatabase.load(FIXTURE_DB)))
    host = RemoteHost(name="pc", is_scanner=True)
    token = host.set_new_token()
    host.save()
    return host, AgentConfig(server_url=live_server.url, token=token)


@pytest.mark.django_db(transaction=True)
def test_agent_end_to_end(live_scanner):
    host, cfg = live_scanner
    scan_fn = fake_scan(
        [
            DiscoveredHost(mac="b8:27:eb:aa:bb:cc", ip="192.168.31.20", hostname="pihole"),
            DiscoveredHost(mac="64:64:4a:01:02:03", ip="192.168.31.1"),
        ]
    )
    agent = ScannerAgent(cfg, ApiClient(cfg), scan_fn=scan_fn)

    assert agent.run_once() == 5.0  # first heartbeat: scan is due
    assert scan_fn.calls[0]["subnet"] == "192.168.31.0/24"
    assert len(scan_fn.calls[0]["vendor_db"]) == 0  # agent never loads the OUI DB
    run = ScanRun.objects.get()
    assert run.status == "success" and run.devices_found == 2 and run.scanner_id == host.pk
    assert Device.objects.get(mac="b8:27:eb:aa:bb:cc").vendor == "Raspberry Pi Foundation"

    agent.run_once()  # not due yet
    assert len(scan_fn.calls) == 1

    host.refresh_from_db()
    assert host.status == "online" and host.agent_version == agent_mod.VERSION


@pytest.mark.django_db(transaction=True)
def test_agent_reports_scan_failure(live_scanner):
    from scanner.arp_scan import ScanError

    _, cfg = live_scanner

    def broken(subnet, **kwargs):
        raise PermissionDeniedError("raw socket access denied")

    ScannerAgent(cfg, ApiClient(cfg), scan_fn=broken).run_once()
    run = ScanRun.objects.get()
    assert run.status == "failed" and "denied" in run.error
    assert issubclass(PermissionDeniedError, ScanError)


@pytest.mark.django_db(transaction=True)
def test_agent_survives_unexpected_scan_exception(live_scanner):
    _, cfg = live_scanner

    def crash(subnet, **kwargs):
        raise RuntimeError("boom")

    ScannerAgent(cfg, ApiClient(cfg), scan_fn=crash).run_once()
    assert "RuntimeError: boom" in ScanRun.objects.get().error


@pytest.mark.django_db(transaction=True)
def test_agent_with_bad_token_gets_401(live_scanner):
    _, cfg = live_scanner
    cfg.token = "wrong"
    with pytest.raises(AgentHTTPError) as excinfo:
        ScannerAgent(cfg, ApiClient(cfg), scan_fn=fake_scan([])).run_once()
    assert excinfo.value.status == 401


def test_run_forever_backs_off_and_stops(monkeypatch):
    import threading

    class Down:
        calls = 0

        def post(self, *args):
            Down.calls += 1
            raise ConnectionRefusedError("down")

    stop = threading.Event()
    waits = []

    class FakeEvent:
        def is_set(self):
            return len(waits) >= 4

        def wait(self, seconds):
            waits.append(seconds)

    cfg = AgentConfig(server_url="http://x", token="t")
    ScannerAgent(cfg, Down(), scan_fn=fake_scan([])).run_forever(FakeEvent())
    assert waits == [1.0, 2.0, 4.0, 8.0]
    assert not stop.is_set()
