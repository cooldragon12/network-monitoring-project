import ipaddress
import json
from pathlib import Path

import pytest

from scanner import arp_scan, cli
from scanner.arp_scan import (
    PermissionDeniedError,
    ScanError,
    normalize_mac,
    parse_arp_scan_output,
    pick_connected_network,
    validate_subnet,
)
from scanner.oui import OuiDatabase, is_locally_administered

FIXTURE_DB = Path(__file__).parent / "fixtures" / "oui_sample.csv"


@pytest.fixture
def oui_db():
    return OuiDatabase.load(FIXTURE_DB)


@pytest.fixture
def fake_backend(monkeypatch):
    """Replace the scapy backend with canned (mac, ip) replies."""
    replies = []

    def fake(network, iface, timeout, retries):
        return list(replies)

    monkeypatch.setattr(arp_scan, "_scapy_scan", fake)
    monkeypatch.setattr(arp_scan, "pick_backend", lambda: "scapy")
    return replies


# --- MAC helpers ------------------------------------------------------------

@pytest.mark.parametrize(
    "raw",
    ["B8:27:EB:12:34:56", "b8-27-eb-12-34-56", "b827.eb12.3456", "B827EB123456"],
)
def test_normalize_mac_formats(raw):
    assert normalize_mac(raw) == "b8:27:eb:12:34:56"


@pytest.mark.parametrize("raw", ["", "b8:27:eb", "zz:27:eb:12:34:56", "b8:27:eb:12:34:56:78"])
def test_normalize_mac_rejects_invalid(raw):
    with pytest.raises(ValueError):
        normalize_mac(raw)


def test_locally_administered_bit():
    assert is_locally_administered("da:a1:19:00:00:01")  # 0xda has bit 1 set
    assert not is_locally_administered("b8:27:eb:00:00:01")


# --- OUI lookup ---------------------------------------------------------------

def test_oui_lookup_ma_l(oui_db):
    assert oui_db.lookup("B8:27:EB:AA:BB:CC") == "Raspberry Pi Foundation"


def test_oui_lookup_prefers_longest_prefix(oui_db):
    assert oui_db.lookup("70:B3:D5:12:3F:FF") == "Tiny Sensor Co"
    assert oui_db.lookup("70:B3:D5:99:00:00") == "IEEE Registration Authority"
    assert oui_db.lookup("F8:B5:68:A0:00:01") == "Medium Block Ltd"


def test_oui_lookup_unknown_and_invalid(oui_db):
    assert oui_db.lookup("00:00:00:00:00:01") is None
    assert oui_db.lookup("not-a-mac") is None


def test_oui_missing_file_is_empty(tmp_path):
    db = OuiDatabase.load(tmp_path / "missing.csv")
    assert len(db) == 0
    assert db.lookup("b8:27:eb:00:00:01") is None


# --- Subnet validation / detection ----------------------------------------------

def test_validate_subnet_normalizes_host_bits():
    assert str(validate_subnet("192.168.31.234/24")) == "192.168.31.0/24"


@pytest.mark.parametrize(
    "subnet,needle",
    [
        ("10.0.0.0/8", "larger than"),
        ("8.8.8.0/24", "not a private"),
        ("fd00::/64", "IPv4"),
        ("garbage", "invalid subnet"),
        ("127.0.0.0/24", "not a scannable"),
    ],
)
def test_validate_subnet_rejects(subnet, needle):
    with pytest.raises(ScanError, match=needle):
        validate_subnet(subnet)


def test_validate_subnet_allow_large():
    assert validate_subnet("10.0.0.0/16", allow_large=True).prefixlen == 16


def _route(cidr, gw, ip, iface="en0"):
    net = ipaddress.IPv4Network(cidr)
    return (int(net.network_address), int(net.netmask), gw, iface, ip, 1)


def test_pick_connected_network_ignores_default_host_and_other_routes():
    ip = "192.168.31.234"
    routes = [
        _route("0.0.0.0/0", "192.168.31.1", ip),
        _route("169.254.0.0/16", "0.0.0.0", ip),
        _route("192.168.31.0/24", "0.0.0.0", ip),
        _route("192.168.31.1/32", "0.0.0.0", ip),
        _route("127.0.0.0/8", "127.0.0.1", "127.0.0.1", "lo0"),
    ]
    assert str(pick_connected_network(routes, ip)) == "192.168.31.0/24"


def test_pick_connected_network_none_when_absent():
    assert pick_connected_network([_route("0.0.0.0/0", "10.0.0.1", "10.0.0.5")], "10.0.0.5") is None


# --- arp-scan output parsing --------------------------------------------------------

def test_parse_arp_scan_output():
    out = (
        "192.168.31.1\t64:64:4a:01:02:03\tXiaomi\n"
        "192.168.31.20\tb8:27:eb:aa:bb:cc\n"
        "garbage line\n"
        "\n"
    )
    assert parse_arp_scan_output(out) == [
        ("64:64:4a:01:02:03", "192.168.31.1"),
        ("b8:27:eb:aa:bb:cc", "192.168.31.20"),
    ]


# --- scan() -------------------------------------------------------------------

def test_scan_builds_hosts(fake_backend, oui_db, monkeypatch):
    monkeypatch.setattr(arp_scan, "resolve_hostnames", lambda ips: {"192.168.31.20": "pi.lan"})
    fake_backend.extend(
        [
            ("B8:27:EB:AA:BB:CC", "192.168.31.20"),
            ("00:11:32:00:00:01", "192.168.31.3"),
            ("da:a1:19:00:00:01", "192.168.31.100"),
        ]
    )
    hosts = arp_scan.scan("192.168.31.0/24", vendor_db=oui_db)

    assert [h.ip for h in hosts] == ["192.168.31.3", "192.168.31.20", "192.168.31.100"]
    pi = hosts[1]
    assert pi.mac == "b8:27:eb:aa:bb:cc"
    assert pi.vendor == "Raspberry Pi Foundation"
    assert pi.hostname == "pi.lan"
    assert hosts[0].vendor == "Synology Incorporated"
    assert hosts[2].locally_administered and hosts[2].vendor is None


def test_scan_dedupes_by_mac_and_filters_out_of_subnet(fake_backend, oui_db):
    fake_backend.extend(
        [
            ("b8:27:eb:aa:bb:cc", "192.168.31.20"),
            ("B8-27-EB-AA-BB-CC", "192.168.31.20"),  # duplicate reply
            ("b8:27:eb:aa:bb:cc", "192.168.31.21"),  # same MAC, other IP
            ("00:11:32:00:00:01", "10.9.9.9"),  # outside the subnet
            ("bogus", "192.168.31.5"),
        ]
    )
    hosts = arp_scan.scan("192.168.31.0/24", vendor_db=oui_db, resolve=False)
    assert [(h.mac, h.ip) for h in hosts] == [("b8:27:eb:aa:bb:cc", "192.168.31.20")]


def test_scan_rejects_unknown_backend():
    with pytest.raises(ScanError, match="unknown backend"):
        arp_scan.scan("192.168.31.0/24", backend="nope")


def test_scapy_permission_error_is_translated(monkeypatch):
    import scapy.all

    def boom(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(scapy.all, "srp", boom)
    with pytest.raises(PermissionDeniedError):
        arp_scan.scan("192.168.31.0/24", backend="scapy", resolve=False, vendor_db=OuiDatabase())


def test_arp_scan_backend_missing_binary(monkeypatch):
    monkeypatch.setattr(arp_scan.shutil, "which", lambda name: None)
    with pytest.raises(ScanError, match="not found"):
        arp_scan.scan("192.168.31.0/24", backend="arp-scan", vendor_db=OuiDatabase())


# --- CLI --------------------------------------------------------------------------

def test_cli_scan_json(fake_backend, capsys):
    fake_backend.append(("b8:27:eb:aa:bb:cc", "192.168.31.20"))
    rc = cli.main(
        ["--oui-db", str(FIXTURE_DB), "scan", "192.168.31.0/24", "--json", "--no-resolve"]
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data == [
        {
            "mac": "b8:27:eb:aa:bb:cc",
            "ip": "192.168.31.20",
            "hostname": None,
            "vendor": "Raspberry Pi Foundation",
            "locally_administered": False,
        }
    ]


def test_cli_scan_table(fake_backend, capsys):
    fake_backend.append(("b8:27:eb:aa:bb:cc", "192.168.31.20"))
    assert cli.main(["--oui-db", str(FIXTURE_DB), "scan", "192.168.31.0/24", "--no-resolve"]) == 0
    out = capsys.readouterr().out
    assert "Raspberry Pi Foundation" in out and "192.168.31.20" in out


def test_cli_permission_denied_exit_code(monkeypatch, capsys):
    def denied(*args, **kwargs):
        raise PermissionDeniedError("nope")

    monkeypatch.setattr(cli, "scan", denied)
    assert cli.main(["scan", "192.168.31.0/24"]) == 77
    assert "nope" in capsys.readouterr().err


def test_cli_lookup(capsys):
    assert cli.main(["--oui-db", str(FIXTURE_DB), "lookup", "b8:27:eb:00:00:01"]) == 0
    assert "Raspberry Pi Foundation" in capsys.readouterr().out


def test_oui_load_cached_reloads_on_change(tmp_path):
    import os

    db_file = tmp_path / "oui.csv"
    db_file.write_text(FIXTURE_DB.read_text())
    first = OuiDatabase.load_cached(db_file)
    assert OuiDatabase.load_cached(db_file) is first

    db_file.write_text("Registry,Assignment,Organization Name,Organization Address\nMA-L,B827EB,Renamed,X\n")
    os.utime(db_file, (1, 1))  # force a different mtime
    reloaded = OuiDatabase.load_cached(db_file)
    assert reloaded is not first
    assert reloaded.lookup("b8:27:eb:00:00:01") == "Renamed"
