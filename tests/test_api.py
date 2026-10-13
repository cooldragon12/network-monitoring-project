import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient

from api.models import ApiToken
from devices.models import Device, DeviceEvent, ScanRun
from monitoring.models import Alert

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture
def user():
    return get_user_model().objects.create_user("owner", password=PASSWORD)


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def devices():
    pi = Device.objects.create(
        mac="b8:27:eb:aa:bb:cc", ip="192.168.31.20", vendor="Raspberry Pi Foundation", is_online=True
    )
    nas = Device.objects.create(
        mac="00:11:32:00:00:01", ip="192.168.31.3", label="NAS", is_known=True, is_online=True
    )
    phone = Device.objects.create(mac="da:a1:19:00:00:01", ip="192.168.31.100", is_random_mac=True)
    Alert.objects.create(device=pi, kind=Alert.Kind.NEW_DEVICE, message="new pi")
    Alert.objects.create(device=phone, kind=Alert.Kind.NEW_RANDOM_MAC, severity="info", message="new phone")
    return {"pi": pi, "nas": nas, "phone": phone}


def macs(response):
    return [d["mac"] for d in response.json()["results"]]


# --- Access control --------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    ["/api/devices/", "/api/alerts/", "/api/events/", "/api/scans/", "/api/stats/", "/api/meta/", "/api/tokens/"],
)
def test_anonymous_is_rejected(url):
    assert APIClient().get(url).status_code in (401, 403)


def test_anonymous_gets_401_with_bearer_challenge():
    resp = APIClient().get("/api/devices/")
    assert resp.status_code == 401
    assert resp["WWW-Authenticate"] == "Bearer"


def test_session_endpoint_anonymous_and_sets_csrf_cookie():
    resp = APIClient().get("/api/auth/session/")
    assert resp.status_code == 200
    assert resp.json() == {"authenticated": False, "user": None}
    assert "csrftoken" in resp.cookies


# --- Login / logout (with real CSRF enforcement) ---------------------------------------------

def _csrf_client():
    client = APIClient(enforce_csrf_checks=True)
    token = client.get("/api/auth/session/").cookies["csrftoken"].value
    return client, token


def test_login_requires_csrf(user):
    client = APIClient(enforce_csrf_checks=True)
    resp = client.post("/api/auth/login/", {"username": "owner", "password": PASSWORD}, format="json")
    assert resp.status_code == 403


def test_login_logout_flow(user):
    client, csrf = _csrf_client()
    resp = client.post(
        "/api/auth/login/", {"username": "owner", "password": PASSWORD}, format="json", HTTP_X_CSRFTOKEN=csrf
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["username"] == "owner"

    csrf = client.cookies["csrftoken"].value  # rotated on login
    assert client.get("/api/devices/").status_code == 200
    assert client.get("/api/auth/session/").json()["authenticated"] is True

    # Unsafe requests from the session need the CSRF header.
    assert client.post("/api/auth/logout/").status_code == 403
    assert client.post("/api/auth/logout/", HTTP_X_CSRFTOKEN=csrf).status_code == 204
    assert client.get("/api/devices/").status_code == 401


def test_login_wrong_password(user):
    client, csrf = _csrf_client()
    resp = client.post(
        "/api/auth/login/", {"username": "owner", "password": "nope"}, format="json", HTTP_X_CSRFTOKEN=csrf
    )
    assert resp.status_code == 400
    assert client.get("/api/devices/").status_code == 401


def test_login_is_throttled(user, settings):
    client, csrf = _csrf_client()
    statuses = [
        client.post(
            "/api/auth/login/", {"username": "owner", "password": "bad"}, format="json", HTTP_X_CSRFTOKEN=csrf
        ).status_code
        for _ in range(12)
    ]
    assert statuses[:10] == [400] * 10
    assert statuses[10:] == [429, 429]


# --- API tokens ----------------------------------------------------------------------------

def test_create_token_shows_plaintext_once_and_authenticates(api, user):
    resp = api.post("/api/tokens/", {"name": "home-assistant"}, format="json")
    assert resp.status_code == 201
    token = resp.json()["token"]
    stored = ApiToken.objects.get()
    assert stored.token_hash != token and token not in stored.token_hash
    assert stored.token_prefix == token[:8]

    listing = api.get("/api/tokens/").json()
    assert listing == [
        {"id": stored.id, "name": "home-assistant", "token_prefix": token[:8],
         "created_at": listing[0]["created_at"], "last_used_at": None}
    ]

    bearer = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert bearer.get("/api/devices/").status_code == 200
    stored.refresh_from_db()
    assert stored.last_used_at is not None


def test_token_duplicate_name_rejected(api):
    api.post("/api/tokens/", {"name": "x"}, format="json")
    assert api.post("/api/tokens/", {"name": "x"}, format="json").status_code == 400


def test_tokens_cannot_manage_tokens(user):
    _, token = ApiToken.issue(user, "script")
    bearer = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert bearer.get("/api/tokens/").status_code in (401, 403)
    assert bearer.post("/api/tokens/", {"name": "evil"}, format="json").status_code in (401, 403)


def test_revoked_and_invalid_tokens_rejected(api, user):
    instance, token = ApiToken.issue(user, "script")
    assert api.delete(f"/api/tokens/{instance.id}/").status_code == 204
    assert APIClient(HTTP_AUTHORIZATION=f"Bearer {token}").get("/api/devices/").status_code == 401
    assert APIClient(HTTP_AUTHORIZATION="Bearer garbage").get("/api/devices/").status_code == 401


def test_token_of_inactive_user_rejected(user):
    _, token = ApiToken.issue(user, "script")
    user.is_active = False
    user.save()
    assert APIClient(HTTP_AUTHORIZATION=f"Bearer {token}").get("/api/devices/").status_code == 401


def test_cannot_see_or_delete_other_users_tokens(api):
    other = get_user_model().objects.create_user("other", password=PASSWORD)
    theirs, _ = ApiToken.issue(other, "theirs")
    assert api.get("/api/tokens/").json() == []
    assert api.delete(f"/api/tokens/{theirs.id}/").status_code == 404


# --- Devices ---------------------------------------------------------------------------------

def test_device_list_unknown_first_with_open_alert_counts(api, devices):
    resp = api.get("/api/devices/")
    assert resp.status_code == 200
    data = resp.json()["results"]
    assert [d["mac"] for d in data][-1] == devices["nas"].mac  # known last
    pi = next(d for d in data if d["mac"] == devices["pi"].mac)
    assert pi["open_alerts"] == 1
    assert pi["display_name"] == "Raspberry Pi Foundation"


@pytest.mark.parametrize(
    "params,expected",
    [
        ({"status": "online"}, {"pi", "nas"}),
        ({"status": "offline"}, {"phone"}),
        ({"known": "false"}, {"pi", "phone"}),
        ({"known": "true"}, {"nas"}),
        ({"random": "true"}, {"phone"}),
        ({"search": "raspberry"}, {"pi"}),
        ({"search": "192.168.31.3"}, {"nas"}),
        ({"search": "da:a1"}, {"phone"}),
    ],
)
def test_device_filters(api, devices, params, expected):
    resp = api.get("/api/devices/", params)
    assert set(macs(resp)) == {devices[k].mac for k in expected}


def test_device_bad_filter_is_400(api, devices):
    assert api.get("/api/devices/", {"status": "sideways"}).status_code == 400
    assert api.get("/api/devices/", {"known": "maybe"}).status_code == 400


def test_device_ordering(api, devices):
    resp = api.get("/api/devices/", {"ordering": "-last_seen"})
    assert resp.status_code == 200
    resp = api.get("/api/devices/", {"ordering": "label"})
    assert macs(resp)[-1] == devices["nas"].mac
    # Unknown fields fall back to the default ordering instead of erroring.
    assert api.get("/api/devices/", {"ordering": "token_hash"}).status_code == 200


def test_patch_device_label_and_type(api, devices):
    pi = devices["pi"]
    resp = api.patch(f"/api/devices/{pi.id}/", {"label": "Garage Pi", "device_type": "server"}, format="json")
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Garage Pi"
    pi.refresh_from_db()
    assert pi.label == "Garage Pi" and pi.device_type == "server"


def test_patch_device_readonly_fields_ignored(api, devices):
    pi = devices["pi"]
    api.patch(f"/api/devices/{pi.id}/", {"mac": "00:00:00:00:00:00", "is_online": False}, format="json")
    pi.refresh_from_db()
    assert pi.mac == "b8:27:eb:aa:bb:cc" and pi.is_online


def test_patch_device_invalid_type(api, devices):
    resp = api.patch(f"/api/devices/{devices['pi'].id}/", {"device_type": "toaster"}, format="json")
    assert resp.status_code == 400


def test_patch_is_known_acknowledges_alerts(api, devices):
    pi = devices["pi"]
    resp = api.patch(f"/api/devices/{pi.id}/", {"is_known": True}, format="json")
    assert resp.status_code == 200
    assert resp.json()["is_known"] is True
    assert resp.json()["open_alerts"] == 0
    assert Alert.objects.get(device=pi).acknowledged


def test_put_not_allowed(api, devices):
    assert api.put(f"/api/devices/{devices['pi'].id}/", {}, format="json").status_code == 405


def test_bulk_mark_known(api, devices):
    resp = api.post(
        "/api/devices/mark-known/", {"ids": [devices["pi"].id, devices["phone"].id]}, format="json"
    )
    assert resp.json() == {"marked": 2, "alerts_acknowledged": 2}
    assert not Device.objects.filter(is_known=False).exists()
    assert api.post("/api/devices/mark-known/", {"ids": "all"}, format="json").status_code == 400


def test_delete_device(api, devices):
    assert api.delete(f"/api/devices/{devices['phone'].id}/").status_code == 204
    assert not Device.objects.filter(mac=devices["phone"].mac).exists()


def test_device_events(api, devices):
    pi = devices["pi"]
    DeviceEvent.objects.create(device=pi, event_type="new_device")
    DeviceEvent.objects.create(device=pi, event_type="ip_changed", details={"old_ip": "a", "new_ip": "b"})
    DeviceEvent.objects.create(device=devices["nas"], event_type="online")
    resp = api.get(f"/api/devices/{pi.id}/events/")
    types = [e["event_type"] for e in resp.json()["results"]]
    assert types == ["ip_changed", "new_device"]


# --- Alerts / events / stats ------------------------------------------------------------------

def test_alert_list_filters(api, devices):
    assert len(api.get("/api/alerts/").json()["results"]) == 2
    resp = api.get("/api/alerts/", {"kind": "new_random_mac"})
    assert [a["subject_name"] for a in resp.json()["results"]] == [str(devices["phone"])]
    resp = api.get("/api/alerts/", {"device": devices["pi"].id})
    assert len(resp.json()["results"]) == 1
    assert api.get("/api/alerts/", {"device": "abc"}).status_code == 400


def test_acknowledge_alert(api, devices):
    alert = Alert.objects.get(device=devices["pi"])
    resp = api.post(f"/api/alerts/{alert.id}/acknowledge/")
    assert resp.json()["acknowledged"] is True
    assert api.get("/api/alerts/", {"acknowledged": "false"}).json()["count"] == 1


def test_acknowledge_all(api, devices):
    assert api.post("/api/alerts/acknowledge-all/").json() == {"acknowledged": 2}
    assert not Alert.objects.filter(acknowledged=False).exists()


def test_alerts_are_read_only(api, devices):
    alert = Alert.objects.first()
    assert api.patch(f"/api/alerts/{alert.id}/", {"message": "x"}, format="json").status_code == 405
    assert api.delete(f"/api/alerts/{alert.id}/").status_code == 405


def test_events_filter(api, devices):
    DeviceEvent.objects.create(device=devices["pi"], event_type="online")
    DeviceEvent.objects.create(device=devices["nas"], event_type="offline")
    resp = api.get("/api/events/", {"event_type": "offline"})
    assert [e["device_name"] for e in resp.json()["results"]] == ["NAS"]


def test_stats(api, devices, settings):
    settings.SCANNER = {**settings.SCANNER, "SUBNET": "192.168.31.0/24"}
    ScanRun.objects.create(subnet="192.168.31.0/24", status="success", devices_found=3)
    data = api.get("/api/stats/").json()
    assert data["devices"] == {"total": 3, "online": 2, "unknown": 2, "unknown_online": 1}
    assert data["open_alerts"] == 2
    assert data["last_scan"]["devices_found"] == 3
    assert data["subnet"] == "192.168.31.0/24"


def test_meta(api):
    data = api.get("/api/meta/").json()
    assert {"value": "phone", "label": "Phone / tablet"} in data["device_types"]


# --- SPA shell --------------------------------------------------------------------------------

def test_spa_served_for_client_routes(client, settings, tmp_path):
    (tmp_path / "index.html").write_text("<div id=root></div>")
    settings.FRONTEND_DIST_DIR = tmp_path
    for path in ("/", "/devices/5", "/alerts"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert b"id=root" in resp.content
    assert "csrftoken" in client.cookies


def test_spa_not_built_returns_503(client, settings, tmp_path):
    settings.FRONTEND_DIST_DIR = tmp_path
    assert client.get("/").status_code == 503


def test_unknown_api_path_is_404_not_spa(api):
    assert api.get("/api/nope/").status_code == 404


# --- On-demand scan ----------------------------------------------------------------------

@pytest.fixture
def queued(monkeypatch):
    """Capture scan tasks instead of running them (eager mode would scan for real)."""
    from devices import tasks

    calls = []
    monkeypatch.setattr(tasks.run_lan_scan, "apply_async", lambda **kw: calls.append(kw))
    return calls


def test_scan_now_queues_task_and_returns_immediately(api, queued):
    previous = ScanRun.objects.create(subnet="192.168.31.0/24", status="success")
    resp = api.post("/api/scan/")
    assert resp.status_code == 202
    assert resp.json() == {"queued": True, "after_scan_id": previous.id}
    assert len(queued) == 1
    assert queued[0]["expires"] > 0
    assert ScanRun.objects.count() == 1  # nothing ran in the request
    assert api.get("/api/stats/").json()["scan_requested"] is True


def test_scan_now_rejects_duplicates_while_queued(api, queued):
    assert api.post("/api/scan/").status_code == 202
    assert api.post("/api/scan/").status_code == 409
    assert len(queued) == 1


def test_scan_now_rejects_while_scan_running(api, queued):
    ScanRun.objects.create(subnet="192.168.31.0/24", status="running")
    assert api.post("/api/scan/").status_code == 409
    assert queued == []


def test_scan_now_ignores_stale_running_scan(api, queued):
    from datetime import timedelta

    from django.utils import timezone

    ScanRun.objects.create(
        subnet="192.168.31.0/24", status="running", started_at=timezone.now() - timedelta(minutes=30)
    )
    assert api.post("/api/scan/").status_code == 202


def test_worker_start_clears_request_flag(api, queued, monkeypatch):
    from devices import tasks

    api.post("/api/scan/")
    monkeypatch.setattr(tasks, "scan", lambda subnet, **kw: [])
    tasks.run_lan_scan()
    assert api.get("/api/stats/").json()["scan_requested"] is False
    assert api.post("/api/scan/").status_code == 202


def test_scan_now_broker_down(api, monkeypatch):
    from devices import tasks

    def broker_down(**kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(tasks.run_lan_scan, "apply_async", broker_down)
    assert api.post("/api/scan/").status_code == 503
    # The flag is released so the user can retry once the broker is back.
    assert api.get("/api/stats/").json()["scan_requested"] is False


def test_scan_now_requires_auth():
    assert APIClient().post("/api/scan/").status_code == 401


def test_scan_now_is_throttled(api, queued, monkeypatch):
    from devices import tasks

    monkeypatch.setattr(tasks, "scan_in_progress", lambda: False)
    statuses = []
    for _ in range(8):
        cache.delete(tasks.SCAN_REQUEST_KEY)
        statuses.append(api.post("/api/scan/").status_code)
    assert statuses[:6] == [202] * 6
    assert statuses[6:] == [429, 429]
