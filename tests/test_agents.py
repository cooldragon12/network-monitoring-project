import socket
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import ApiToken
from devices.models import Device, ScanRun
from devices.scanning import fail_stale_runs
from monitoring import checks
from monitoring.agents import mark_overdue_hosts_offline
from monitoring.models import Alert, HostCheck, HostEvent, RemoteHost
from monitoring.tasks import check_agents
from scanner.oui import OuiDatabase

pytestmark = pytest.mark.django_db

FIXTURE_DB = Path(__file__).parent / "fixtures" / "oui_sample.csv"
SUBNET = "192.168.31.0/24"


@pytest.fixture(autouse=True)
def _env(settings, monkeypatch):
    cache.clear()
    settings.SCANNER = {**settings.SCANNER, "SUBNET": SUBNET, "INTERVAL_MINUTES": 2, "HEARTBEAT_SECONDS": 5}
    monkeypatch.setattr(OuiDatabase, "load_cached", classmethod(lambda cls, path=None: OuiDatabase.load(FIXTURE_DB)))


def make_host(name="pi", **kwargs):
    host = RemoteHost(name=name, **kwargs)
    token = host.set_new_token()
    host.save()
    return host, token


@pytest.fixture
def scanner():
    host, token = make_host("scanner-pc", is_scanner=True, expected_interval_seconds=30)
    return host, APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")


@pytest.fixture
def user_api(django_user_model):
    client = APIClient()
    client.force_authenticate(django_user_model.objects.create_user("owner"))
    return client


def heartbeat(client):
    return client.post("/api/agent/scanner/heartbeat/", {"hostname": "pc", "agent_version": "1.0.0"}, format="json")


def run_scan(client, hosts, subnet=SUBNET):
    run = client.post("/api/agent/scanner/scans/", {"subnet": subnet}, format="json")
    assert run.status_code == 201, run.json()
    return client.post(
        f"/api/agent/scanner/scans/{run.json()['id']}/", {"status": "success", "hosts": hosts}, format="json"
    )


PI = {"mac": "B8-27-EB-AA-BB-CC", "ip": "192.168.31.20", "hostname": "pihole"}
PHONE = {"mac": "da:a1:19:00:00:01", "ip": "192.168.31.100"}


# --- Agent authentication -----------------------------------------------------------------

def test_agent_endpoints_require_agent_token(scanner):
    assert APIClient().post("/api/agent/checkin/", {}, format="json").status_code == 401
    bad = APIClient(HTTP_AUTHORIZATION="Bearer nope")
    assert bad.post("/api/agent/checkin/", {}, format="json").status_code == 401


def test_personal_api_token_cannot_act_as_agent(django_user_model):
    _, token = ApiToken.issue(django_user_model.objects.create_user("u"), "script")
    client = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert client.post("/api/agent/checkin/", {}, format="json").status_code == 401


def test_agent_token_cannot_use_dashboard_api(scanner):
    _, client = scanner
    assert client.get("/api/devices/").status_code == 401
    assert client.post("/api/scan/").status_code == 401


def test_revoked_host_rejected(scanner):
    host, client = scanner
    host.is_active = False
    host.save()
    assert heartbeat(client).status_code == 401


def test_non_scanner_host_can_check_in_but_not_scan():
    _, token = make_host("nas")
    client = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert client.post("/api/agent/checkin/", {}, format="json").status_code == 200
    assert heartbeat(client).status_code == 403
    assert client.post("/api/agent/scanner/scans/", {"subnet": SUBNET}, format="json").status_code == 403


def test_agent_rate_limited_per_host(scanner, monkeypatch):
    from api.agent_views import AgentRateThrottle

    monkeypatch.setattr(AgentRateThrottle, "rate", "3/min", raising=False)
    _, client = scanner
    assert [heartbeat(client).status_code for _ in range(4)] == [200, 200, 200, 429]
    # Limits are per host, so another agent is unaffected.
    _, other = make_host("other", is_scanner=True)
    assert heartbeat(APIClient(HTTP_AUTHORIZATION=f"Bearer {other}")).status_code == 200


# --- Metrics check-in -------------------------------------------------------------------

def test_checkin_stores_metrics_and_goes_online():
    host, token = make_host("nas")
    client = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")
    resp = client.post(
        "/api/agent/checkin/",
        {"hostname": "nas.lan", "agent_version": "1.0", "cpu_percent": 12.5, "mem_percent": 40,
         "disk_percent": 71.2, "uptime_seconds": 3600},
        format="json",
    )
    assert resp.status_code == 200
    host.refresh_from_db()
    assert host.status == "online"
    assert (host.cpu_percent, host.mem_percent, host.disk_percent, host.uptime_seconds) == (12.5, 40, 71.2, 3600)
    assert host.reported_hostname == "nas.lan" and host.agent_version == "1.0"
    assert host.last_ip == "127.0.0.1"
    assert HostEvent.objects.get(host=host).event_type == "online"


def test_checkin_validates_metrics():
    _, token = make_host("nas")
    client = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert client.post("/api/agent/checkin/", {"cpu_percent": 250}, format="json").status_code == 400


# --- Offline detection ------------------------------------------------------------------

def test_overdue_host_goes_offline_with_critical_alert():
    host, _ = make_host("nas", expected_interval_seconds=60, status="online",
                        last_checkin=timezone.now() - timedelta(seconds=121))
    assert mark_overdue_hosts_offline() == ["nas"]
    host.refresh_from_db()
    assert host.status == "offline"
    alert = Alert.objects.get(remote_host=host)
    assert alert.kind == "host_offline" and alert.severity == "critical"
    assert HostEvent.objects.filter(host=host, event_type="offline").count() == 1
    # Idempotent.
    assert mark_overdue_hosts_offline() == []


def test_host_within_grace_stays_online():
    host, _ = make_host("nas", expected_interval_seconds=60, status="online",
                        last_checkin=timezone.now() - timedelta(seconds=100))
    assert mark_overdue_hosts_offline() == []


def test_never_checked_in_host_is_not_alerted():
    make_host("new")
    assert mark_overdue_hosts_offline() == []
    assert not Alert.objects.exists()


def test_scanner_offline_alert_mentions_paused_scans(scanner):
    host, _ = scanner
    RemoteHost.objects.filter(pk=host.pk).update(status="online", last_checkin=timezone.now() - timedelta(minutes=5))
    mark_overdue_hosts_offline()
    assert "scans are paused" in Alert.objects.get(remote_host=host).message


def test_back_online_records_downtime():
    host, token = make_host("nas", expected_interval_seconds=60, status="online",
                            last_checkin=timezone.now() - timedelta(minutes=10))
    mark_overdue_hosts_offline()
    APIClient(HTTP_AUTHORIZATION=f"Bearer {token}").post("/api/agent/checkin/", {}, format="json")
    host.refresh_from_db()
    assert host.status == "online"
    online = HostEvent.objects.filter(host=host, event_type="online").get()
    assert online.details["down_seconds"] >= 600


def test_check_agents_task_closes_stale_scans(scanner):
    host, _ = scanner
    run = ScanRun.objects.create(subnet=SUBNET, scanner=host, started_at=timezone.now() - timedelta(minutes=10))
    result = check_agents()
    run.refresh_from_db()
    assert result["stale_scans_closed"] == 1
    assert run.status == "failed" and "did not report" in run.error
    assert fail_stale_runs() == 0


# --- Scanner protocol ------------------------------------------------------------------

def test_first_heartbeat_asks_for_scan_with_server_config(scanner):
    host, client = scanner
    data = heartbeat(client).json()
    assert data == {"scan_now": True, "subnet": SUBNET, "timeout_seconds": 2, "heartbeat_seconds": 5}
    host.refresh_from_db()
    assert host.status == "online" and host.reported_hostname == "pc"


def test_host_subnet_overrides_global(scanner):
    host, client = scanner
    host.scan_subnet = "10.0.5.0/24"
    host.save()
    assert heartbeat(client).json()["subnet"] == "10.0.5.0/24"


def test_full_scan_report_creates_devices_with_server_side_vendor(scanner):
    host, client = scanner
    resp = run_scan(client, [PI, PHONE, {"mac": "00:11:32:00:00:01", "ip": "10.9.9.9"}])
    assert resp.status_code == 200
    assert resp.json()["status"] == "success" and resp.json()["devices_found"] == 2
    assert resp.json()["new_devices"] == 2
    pi = Device.objects.get(mac="b8:27:eb:aa:bb:cc")
    assert pi.vendor == "Raspberry Pi Foundation"  # looked up by the server
    assert pi.hostname == "pihole"
    assert Device.objects.get(mac=PHONE["mac"]).is_random_mac  # derived by the server
    assert not Device.objects.filter(ip="10.9.9.9").exists()  # outside the subnet
    assert ScanRun.objects.get().scanner == host


def test_not_due_until_interval_elapses(scanner):
    _, client = scanner
    run_scan(client, [PI])
    assert heartbeat(client).json()["scan_now"] is False
    ScanRun.objects.update(started_at=timezone.now() - timedelta(minutes=3))
    assert heartbeat(client).json()["scan_now"] is True


def test_no_scan_while_one_is_running(scanner):
    _, client = scanner
    client.post("/api/agent/scanner/scans/", {"subnet": SUBNET}, format="json")
    assert heartbeat(client).json()["scan_now"] is False


def test_start_rejects_wrong_or_unsafe_subnet(scanner):
    _, client = scanner
    assert client.post("/api/agent/scanner/scans/", {"subnet": "192.168.1.0/24"}, format="json").status_code == 400
    assert client.post("/api/agent/scanner/scans/", {"subnet": "8.8.8.0/24"}, format="json").status_code == 400


def test_failed_and_empty_reports_never_count_as_missed(scanner):
    _, client = scanner
    Device.objects.create(mac="b8:27:eb:aa:bb:cc", ip="192.168.31.20", is_online=True)
    for payload in ({"status": "failed", "error": "permission denied"}, {"status": "success", "hosts": []}):
        run = client.post("/api/agent/scanner/scans/", {"subnet": SUBNET}, format="json").json()
        resp = client.post(f"/api/agent/scanner/scans/{run['id']}/", payload, format="json")
        assert resp.json()["status"] == "failed"
    device = Device.objects.get()
    assert device.is_online and device.missed_scans == 0


def test_invalid_report_rejected_and_run_failed(scanner):
    _, client = scanner
    run = client.post("/api/agent/scanner/scans/", {"subnet": SUBNET}, format="json").json()
    resp = client.post(
        f"/api/agent/scanner/scans/{run['id']}/",
        {"status": "success", "hosts": [{"mac": "zz", "ip": "192.168.31.5"}]},
        format="json",
    )
    assert resp.status_code == 400
    assert ScanRun.objects.get().status == "failed"


def test_report_twice_or_for_other_scanner_rejected(scanner):
    _, client = scanner
    run = client.post("/api/agent/scanner/scans/", {"subnet": SUBNET}, format="json").json()
    url = f"/api/agent/scanner/scans/{run['id']}/"
    assert client.post(url, {"status": "success", "hosts": [PI]}, format="json").status_code == 200
    assert client.post(url, {"status": "success", "hosts": [PI]}, format="json").status_code == 400
    _, other_token = make_host("other", is_scanner=True)
    other = APIClient(HTTP_AUTHORIZATION=f"Bearer {other_token}")
    assert other.post(url, {"status": "success", "hosts": [PI]}, format="json").status_code == 404


# --- Scan now (dashboard) ----------------------------------------------------------------

def test_scan_now_without_online_scanner_is_503(user_api, scanner):
    resp = user_api.post("/api/scan/")
    assert resp.status_code == 503
    assert "No scanner agent is online" in resp.json()["detail"]


def test_scan_now_flags_scanner_and_next_heartbeat_scans(user_api, scanner):
    host, client = scanner
    run_scan(client, [PI])  # just scanned: not due on schedule
    assert heartbeat(client).json()["scan_now"] is False

    resp = user_api.post("/api/scan/")
    assert resp.status_code == 202
    assert user_api.get("/api/stats/").json()["scan_requested"] is True
    assert user_api.post("/api/scan/").status_code == 409

    assert heartbeat(client).json()["scan_now"] is True
    run_scan(client, [PI])
    host.refresh_from_db()
    assert host.scan_requested_at is None
    assert user_api.get("/api/stats/").json()["scan_requested"] is False


def test_scan_request_lapses(user_api, scanner):
    host, client = scanner
    run_scan(client, [PI])
    user_api.post("/api/scan/")
    RemoteHost.objects.filter(pk=host.pk).update(scan_requested_at=timezone.now() - timedelta(minutes=3))
    assert heartbeat(client).json()["scan_now"] is False
    assert user_api.post("/api/scan/").status_code == 202


def test_stats_lists_scanners(user_api, scanner):
    host, client = scanner
    heartbeat(client)
    data = user_api.get("/api/stats/").json()
    assert [(s["name"], s["status"]) for s in data["scanners"]] == [("scanner-pc", "online")]


# --- Server-side checks -------------------------------------------------------------------

@pytest.fixture
def listening_port():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    yield server.getsockname()[1]
    server.close()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_tcp_check(listening_port):
    assert checks.tcp_connect("127.0.0.1", listening_port, 2).ok
    result = checks.tcp_connect("127.0.0.1", free_port(), 2)
    assert not result.ok and result.error


@pytest.fixture
def http_server():
    codes = {"/ok": 200, "/broken": 500}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(codes.get(self.path, 404))
            self.end_headers()
            self.wfile.write(b"x")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_http_check(http_server):
    assert checks.http_get(f"{http_server}/ok", 2).ok
    broken = checks.http_get(f"{http_server}/broken", 2)
    assert not broken.ok and broken.error == "HTTP 500"
    assert not checks.http_get("ftp://example.com", 2).ok


def test_icmp_packet_checksum():
    packet = checks.build_echo_request(seq=7)
    assert packet[0] == 8 and checks._icmp_checksum(packet) == 0


def test_ping_localhost():
    result = checks.ping("127.0.0.1", 2)
    if not result.ok and "not permitted" in result.error:
        pytest.skip("unprivileged ICMP not allowed in this environment")
    assert result.ok and result.latency_ms is not None


def test_check_alerts_after_threshold_and_recovers():
    host, _ = make_host("nas")
    check = HostCheck.objects.create(host=host, kind="tcp", target="127.0.0.1", port=1, failure_threshold=2)
    fail, ok = checks.CheckResult(False, error="refused"), checks.CheckResult(True, 1.0)

    checks.apply_result(check, fail)
    check.refresh_from_db()
    assert check.status == "unknown" and check.consecutive_failures == 1
    assert not Alert.objects.exists()

    checks.apply_result(check, fail)
    checks.apply_result(check, fail)
    check.refresh_from_db()
    assert check.status == "down"
    alert = Alert.objects.get()
    assert alert.kind == "check_failed" and "tcp 127.0.0.1:1" in alert.message

    checks.apply_result(check, ok)
    check.refresh_from_db()
    assert check.status == "up" and check.consecutive_failures == 0
    assert list(HostEvent.objects.filter(host=host).values_list("event_type", flat=True)) == [
        "check_recovered",
        "check_failed",
    ]


def test_run_due_checks_respects_interval(listening_port):
    host, _ = make_host("nas")
    HostCheck.objects.create(host=host, kind="tcp", target="127.0.0.1", port=listening_port, interval_seconds=60)
    HostCheck.objects.create(host=host, kind="tcp", target="127.0.0.1", port=listening_port, enabled=False)
    assert checks.run_due_checks() == 1
    assert checks.run_due_checks() == 0  # not due again yet
    assert HostCheck.objects.get(enabled=True).status == "up"
