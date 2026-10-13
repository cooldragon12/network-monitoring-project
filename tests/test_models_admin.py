import pytest
from django.contrib.messages import get_messages
from django.core.management import call_command
from django.db import IntegrityError
from django.urls import reverse

from devices.models import Device, DeviceEvent, ScanRun
from monitoring.models import Alert, RemoteHost, hash_token

pytestmark = pytest.mark.django_db


@pytest.fixture
def device():
    return Device.objects.create(mac="b8:27:eb:aa:bb:cc", ip="192.168.31.20", vendor="Raspberry Pi Foundation")


@pytest.fixture
def host():
    h = RemoteHost(name="nas")
    h.set_new_token()
    h.save()
    return h


def test_migrations_are_up_to_date():
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)


# --- Models ---------------------------------------------------------------------

def test_device_display_name_fallbacks(device):
    assert device.display_name == "Raspberry Pi Foundation"
    device.hostname = "pi.lan"
    assert device.display_name == "pi.lan"
    device.label = "Garage Pi"
    assert str(device) == "Garage Pi"
    assert Device(mac="00:00:00:00:00:01").display_name == "00:00:00:00:00:01"


def test_device_defaults(device):
    assert not device.is_known
    assert not device.is_online
    assert device.source == Device.Source.LAN
    assert device.missed_scans == 0


def test_device_mac_unique(device):
    with pytest.raises(IntegrityError):
        Device.objects.create(mac=device.mac)


def test_events_cascade_and_order(device):
    DeviceEvent.objects.create(device=device, event_type="first_seen")
    DeviceEvent.objects.create(device=device, event_type="online", details={"ip": "192.168.31.20"})
    assert [e.event_type for e in device.events.all()] == ["online", "first_seen"]
    device.delete()
    assert DeviceEvent.objects.count() == 0


def test_scan_run_duration():
    run = ScanRun.objects.create(subnet="192.168.31.0/24")
    assert run.status == ScanRun.Status.RUNNING
    assert run.duration is None


def test_alert_requires_exactly_one_subject(device, host):
    Alert.objects.create(device=device, kind="new_device", message="x")
    Alert.objects.create(remote_host=host, kind="host_offline", message="x")
    with pytest.raises(IntegrityError):
        Alert.objects.create(kind="new_device", message="no subject")


def test_alert_requires_not_both_subjects(device, host):
    with pytest.raises(IntegrityError):
        Alert.objects.create(device=device, remote_host=host, kind="new_device", message="both")


def test_alert_acknowledge(device):
    alert = Alert.objects.create(device=device, kind="new_device", message="x")
    alert.acknowledge()
    alert.refresh_from_db()
    assert alert.acknowledged and alert.acknowledged_at is not None


# --- Agent tokens ------------------------------------------------------------------

def test_token_stored_only_as_hash():
    h = RemoteHost(name="pi")
    token = h.set_new_token()
    h.save()
    h.refresh_from_db()
    assert len(token) >= 40
    assert h.token_hash == hash_token(token)
    assert token not in h.token_hash
    assert h.token_prefix == token[:8]


def test_token_authenticate():
    h = RemoteHost(name="pi")
    token = h.set_new_token()
    h.save()
    assert RemoteHost.authenticate(token) == h
    assert RemoteHost.authenticate("wrong") is None
    assert RemoteHost.authenticate("") is None
    h.is_active = False
    h.save()
    assert RemoteHost.authenticate(token) is None


def test_tokens_are_unique():
    tokens = {RemoteHost(name=str(i)).set_new_token() for i in range(50)}
    assert len(tokens) == 50


# --- Admin --------------------------------------------------------------------------

def test_admin_requires_login(client):
    resp = client.get(reverse("admin:devices_device_changelist"))
    assert resp.status_code == 302
    assert reverse("admin:login") in resp["Location"]


@pytest.mark.parametrize(
    "url_name",
    [
        "admin:devices_device_changelist",
        "admin:devices_scanrun_changelist",
        "admin:devices_deviceevent_changelist",
        "admin:monitoring_alert_changelist",
        "admin:monitoring_remotehost_changelist",
    ],
)
def test_admin_changelists_render(admin_client, device, host, url_name):
    DeviceEvent.objects.create(device=device, event_type="first_seen")
    Alert.objects.create(device=device, kind="new_device", message="new")
    ScanRun.objects.create(subnet="192.168.31.0/24")
    assert admin_client.get(reverse(url_name)).status_code == 200


def test_admin_device_change_page_renders(admin_client, device):
    DeviceEvent.objects.create(device=device, event_type="first_seen")
    resp = admin_client.get(reverse("admin:devices_device_change", args=[device.pk]))
    assert resp.status_code == 200


@pytest.mark.parametrize("query", ["b8:27:eb", "192.168.31.20", "Garage"])
def test_admin_device_search(admin_client, device, query):
    device.label = "Garage Pi"
    device.save()
    Device.objects.create(mac="00:11:32:00:00:01", ip="192.168.31.3", label="NAS")
    resp = admin_client.get(reverse("admin:devices_device_changelist"), {"q": query})
    assert list(resp.context["cl"].result_list) == [device]


def test_admin_device_filters(admin_client, device):
    Device.objects.create(mac="00:11:32:00:00:01", is_known=True, is_online=True)
    url = reverse("admin:devices_device_changelist")
    resp = admin_client.get(url, {"is_known__exact": "0"})
    assert list(resp.context["cl"].result_list) == [device]
    resp = admin_client.get(url, {"is_online__exact": "1"})
    assert [d.mac for d in resp.context["cl"].result_list] == ["00:11:32:00:00:01"]


def test_admin_add_remote_host_shows_token_once(admin_client):
    resp = admin_client.post(
        reverse("admin:monitoring_remotehost_add"),
        {"name": "vps", "expected_interval_seconds": 300, "is_active": "on"},
    )
    assert resp.status_code == 302
    host = RemoteHost.objects.get(name="vps")
    msgs = [str(m) for m in get_messages(resp.wsgi_request)]
    token_msgs = [m for m in msgs if "shown only once" in m]
    assert len(token_msgs) == 1
    token = token_msgs[0].rsplit(": ", 1)[1]
    assert RemoteHost.authenticate(token) == host


def test_admin_edit_remote_host_keeps_token(admin_client, host):
    old_hash = host.token_hash
    admin_client.post(
        reverse("admin:monitoring_remotehost_change", args=[host.pk]),
        {"name": "nas-renamed", "expected_interval_seconds": 600, "is_active": "on"},
    )
    host.refresh_from_db()
    assert host.name == "nas-renamed"
    assert host.token_hash == old_hash


def test_admin_revoke_and_rotate(admin_client, host):
    url = reverse("admin:monitoring_remotehost_changelist")
    admin_client.post(url, {"action": "revoke_tokens", "_selected_action": [host.pk]})
    host.refresh_from_db()
    assert not host.is_active

    old_hash = host.token_hash
    resp = admin_client.post(url, {"action": "rotate_tokens", "_selected_action": [host.pk]})
    host.refresh_from_db()
    assert host.is_active and host.token_hash != old_hash
    token = next(str(m) for m in get_messages(resp.wsgi_request) if "shown only once" in str(m)).rsplit(": ", 1)[1]
    assert RemoteHost.authenticate(token) == host


def test_admin_acknowledge_action(admin_client, device):
    alert = Alert.objects.create(device=device, kind="new_device", message="x")
    admin_client.post(
        reverse("admin:monitoring_alert_changelist"),
        {"action": "acknowledge_selected", "_selected_action": [alert.pk]},
    )
    alert.refresh_from_db()
    assert alert.acknowledged
