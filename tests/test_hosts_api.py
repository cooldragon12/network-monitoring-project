import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from api.models import ApiToken
from monitoring.models import Alert, HostCheck, HostEvent, RemoteHost

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return get_user_model().objects.create_user("owner", password="correct-horse-battery-staple")


@pytest.fixture
def session(user):
    client = APIClient()
    client.force_login(user)  # real session -> SessionAuthentication
    return client


@pytest.fixture
def bearer(user):
    _, token = ApiToken.issue(user, "script")
    return APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")


def create_host(client, **data):
    return client.post("/api/hosts/", {"name": "pi", "is_scanner": True, **data}, format="json")


def test_create_host_returns_token_once_and_it_works(session):
    resp = create_host(session, expected_interval_seconds=30)
    assert resp.status_code == 201
    body = resp.json()
    token = body["token"]
    host = RemoteHost.objects.get()
    assert host.is_scanner and host.expected_interval_seconds == 30
    assert body["token_prefix"] == token[:8] and token not in host.token_hash
    assert "token" not in session.get(f"/api/hosts/{host.pk}/").json()
    agent = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert agent.post("/api/agent/scanner/heartbeat/", {}, format="json").status_code == 200


def test_api_token_cannot_create_hosts_or_rotate(session, bearer):
    assert create_host(bearer).status_code == 403
    host_id = create_host(session).json()["id"]
    assert bearer.post(f"/api/hosts/{host_id}/rotate-token/").status_code == 403
    # ...but can read and edit.
    assert bearer.get("/api/hosts/").status_code == 200
    assert bearer.patch(f"/api/hosts/{host_id}/", {"name": "pi-2"}, format="json").status_code == 200


def test_rotate_token_invalidates_old(session):
    old = create_host(session).json()
    new = session.post(f"/api/hosts/{old['id']}/rotate-token/").json()
    assert new["token"] != old["token"]
    old_agent = APIClient(HTTP_AUTHORIZATION=f"Bearer {old['token']}")
    new_agent = APIClient(HTTP_AUTHORIZATION=f"Bearer {new['token']}")
    assert old_agent.post("/api/agent/checkin/", {}, format="json").status_code == 401
    assert new_agent.post("/api/agent/checkin/", {}, format="json").status_code == 200


def test_scan_subnet_validated(session):
    assert create_host(session, scan_subnet="8.8.8.0/24").status_code == 400
    assert create_host(session, scan_subnet="192.168.31.7/24").json()["scan_subnet"] == "192.168.31.0/24"


def test_metrics_are_read_only(session):
    host_id = create_host(session).json()["id"]
    session.patch(f"/api/hosts/{host_id}/", {"cpu_percent": 99, "status": "online"}, format="json")
    host = RemoteHost.objects.get()
    assert host.cpu_percent is None and host.status == "unknown"


def test_deactivate_and_delete(session):
    body = create_host(session).json()
    session.patch(f"/api/hosts/{body['id']}/", {"is_active": False}, format="json")
    agent = APIClient(HTTP_AUTHORIZATION=f"Bearer {body['token']}")
    assert agent.post("/api/agent/checkin/", {}, format="json").status_code == 401
    assert session.delete(f"/api/hosts/{body['id']}/").status_code == 204
    assert not RemoteHost.objects.exists()


def test_host_list_includes_checks_and_open_alerts(session):
    host_id = create_host(session).json()["id"]
    host = RemoteHost.objects.get()
    HostCheck.objects.create(host=host, kind="ping", target="192.168.31.3")
    Alert.objects.create(remote_host=host, kind="host_offline", message="x")
    data = session.get("/api/hosts/").json()[0]
    assert data["id"] == host_id and data["open_alerts"] == 1
    assert data["checks"][0]["description"] == "ping 192.168.31.3"


def test_host_events(session):
    create_host(session)
    host = RemoteHost.objects.get()
    HostEvent.objects.create(host=host, event_type="online")
    HostEvent.objects.create(host=host, event_type="offline")
    events = session.get(f"/api/hosts/{host.pk}/events/").json()
    assert [e["event_type"] for e in events] == ["offline", "online"]


# --- Checks -------------------------------------------------------------------------------

@pytest.fixture
def host(session):
    create_host(session)
    return RemoteHost.objects.get()


@pytest.mark.parametrize(
    "payload,ok",
    [
        ({"kind": "ping", "target": "192.168.31.3"}, True),
        ({"kind": "tcp", "target": "nas.lan", "port": 445}, True),
        ({"kind": "http", "target": "https://nas.lan:5001/"}, True),
        ({"kind": "tcp", "target": "nas.lan"}, False),  # no port
        ({"kind": "tcp", "target": "nas.lan:445", "port": 445}, False),  # port in target
        ({"kind": "http", "target": "nas.lan"}, False),  # not a URL
        ({"kind": "ping", "target": "http://x"}, False),
        ({"kind": "ping", "target": "x", "interval_seconds": 1}, False),  # too frequent
    ],
)
def test_check_validation(session, host, payload, ok):
    resp = session.post("/api/checks/", {"host": host.pk, **payload}, format="json")
    assert (resp.status_code == 201) is ok, resp.json()


def test_check_crud_and_filter(session, host):
    other = RemoteHost(name="other")
    other.set_new_token()
    other.save()
    check = session.post("/api/checks/", {"host": host.pk, "kind": "ping", "target": "1.1.1.1"}, format="json").json()
    session.post("/api/checks/", {"host": other.pk, "kind": "ping", "target": "1.0.0.1"}, format="json")
    assert len(session.get("/api/checks/", {"host": host.pk}).json()) == 1
    resp = session.patch(f"/api/checks/{check['id']}/", {"enabled": False, "status": "up"}, format="json")
    assert resp.json()["enabled"] is False and resp.json()["status"] == "unknown"  # status read-only
    assert session.delete(f"/api/checks/{check['id']}/").status_code == 204
