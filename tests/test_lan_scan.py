from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from devices.models import Device, DeviceEvent
from devices.services import process_scan_results
from monitoring.models import Alert
from scanner.arp_scan import DiscoveredHost

pytestmark = pytest.mark.django_db

SUBNET = "192.168.31.0/24"
PI = DiscoveredHost(mac="b8:27:eb:aa:bb:cc", ip="192.168.31.20", vendor="Raspberry Pi Foundation")
NAS = DiscoveredHost(mac="00:11:32:00:00:01", ip="192.168.31.3", vendor="Synology Incorporated")
PHONE = DiscoveredHost(mac="da:a1:19:00:00:01", ip="192.168.31.100", locally_administered=True)


def scan_once(*hosts, offline_after=3, now=None):
    return process_scan_results(hosts, SUBNET, offline_after=offline_after, now=now)


def events(device, event_type):
    return DeviceEvent.objects.filter(device=device, event_type=event_type)


# --- New-device detection ---------------------------------------------------------

def test_new_mac_creates_unknown_device_and_alert():
    summary = scan_once(PI)

    device = Device.objects.get(mac=PI.mac)
    assert not device.is_known
    assert device.is_online
    assert device.ip == PI.ip
    assert device.vendor == "Raspberry Pi Foundation"
    assert device.source == Device.Source.LAN
    assert summary.new_devices == [PI.mac]

    alert = Alert.objects.get(device=device)
    assert alert.kind == Alert.Kind.NEW_DEVICE
    assert alert.severity == Alert.Severity.WARNING
    assert PI.mac in alert.message and "Raspberry Pi" in alert.message
    assert events(device, DeviceEvent.EventType.NEW_DEVICE).count() == 1


def test_known_mac_does_not_alert():
    Device.objects.create(mac=PI.mac, ip=PI.ip, is_known=True, is_online=True)
    summary = scan_once(PI)
    assert summary.new_devices == []
    assert Alert.objects.count() == 0


def test_unknown_device_alerts_only_once():
    scan_once(PI)
    scan_once(PI)
    scan_once(PI)
    assert Alert.objects.count() == 1
    assert Device.objects.count() == 1


def test_random_mac_gets_softer_alert():
    scan_once(PHONE)
    device = Device.objects.get(mac=PHONE.mac)
    assert device.is_random_mac
    alert = Alert.objects.get(device=device)
    assert alert.kind == Alert.Kind.NEW_RANDOM_MAC
    assert alert.severity == Alert.Severity.INFO


def test_preregistered_known_device_first_scan_no_alert():
    # A device you added by hand (e.g. before it ever joined) is not "new".
    Device.objects.create(mac=NAS.mac, label="NAS", is_known=True)
    scan_once(NAS)
    assert Alert.objects.count() == 0
    assert Device.objects.get(mac=NAS.mac).is_online


# --- Online / offline transitions ---------------------------------------------------

def test_offline_after_m_missed_scans():
    scan_once(PI, NAS)
    pi = Device.objects.get(mac=PI.mac)

    scan_once(NAS)
    scan_once(NAS)
    pi.refresh_from_db()
    assert pi.is_online and pi.missed_scans == 2
    assert not events(pi, DeviceEvent.EventType.OFFLINE).exists()

    summary = scan_once(NAS)
    pi.refresh_from_db()
    assert not pi.is_online
    assert summary.went_offline == [PI.mac]
    assert events(pi, DeviceEvent.EventType.OFFLINE).count() == 1

    # Staying away does not create more offline events.
    scan_once(NAS)
    assert events(pi, DeviceEvent.EventType.OFFLINE).count() == 1


def test_offline_threshold_is_configurable():
    scan_once(PI, NAS)
    scan_once(NAS, offline_after=1)
    assert not Device.objects.get(mac=PI.mac).is_online


def test_missed_counter_resets_when_seen():
    scan_once(PI, NAS)
    scan_once(NAS)
    scan_once(NAS)
    scan_once(PI, NAS)
    pi = Device.objects.get(mac=PI.mac)
    assert pi.is_online and pi.missed_scans == 0


def test_comes_back_online():
    t0 = timezone.now()
    scan_once(PI, NAS, now=t0)
    for i in range(1, 4):
        scan_once(NAS, now=t0 + timedelta(minutes=2 * i))
    summary = scan_once(PI, NAS, now=t0 + timedelta(minutes=10))

    pi = Device.objects.get(mac=PI.mac)
    assert pi.is_online and pi.missed_scans == 0
    assert pi.last_seen == t0 + timedelta(minutes=10)
    assert summary.came_online == [PI.mac]
    online = events(pi, DeviceEvent.EventType.ONLINE).get()
    assert online.details["ip"] == PI.ip
    assert Alert.objects.filter(device=pi).count() == 1  # only the original new_device


def test_devices_outside_scanned_subnet_are_not_marked_missing():
    other = Device.objects.create(mac="00:00:00:00:00:99", ip="192.168.1.50", is_online=True)
    agent = Device.objects.create(
        mac="00:00:00:00:00:98", ip="192.168.31.99", is_online=True, source=Device.Source.AGENT
    )
    for _ in range(5):
        scan_once(PI)
    other.refresh_from_db()
    agent.refresh_from_db()
    assert other.is_online and other.missed_scans == 0
    assert agent.is_online and agent.missed_scans == 0


# --- IP / metadata changes -----------------------------------------------------------

def test_ip_change_creates_event():
    scan_once(PI)
    moved = DiscoveredHost(mac=PI.mac, ip="192.168.31.21", vendor=PI.vendor)
    summary = scan_once(moved)

    pi = Device.objects.get(mac=PI.mac)
    assert pi.ip == "192.168.31.21"
    assert summary.ip_changed == [PI.mac]
    event = events(pi, DeviceEvent.EventType.IP_CHANGED).get()
    assert event.details == {"old_ip": "192.168.31.20", "new_ip": "192.168.31.21"}


def test_same_ip_no_change_event():
    scan_once(PI)
    scan_once(PI)
    assert not DeviceEvent.objects.filter(event_type=DeviceEvent.EventType.IP_CHANGED).exists()


def test_hostname_updated_but_label_and_vendor_preserved():
    Device.objects.create(mac=PI.mac, ip=PI.ip, label="Garage Pi", vendor="Custom vendor", is_online=True)
    scan_once(DiscoveredHost(mac=PI.mac, ip=PI.ip, hostname="pi.lan", vendor="Raspberry Pi Foundation"))
    pi = Device.objects.get(mac=PI.mac)
    assert pi.hostname == "pi.lan"
    assert pi.label == "Garage Pi"
    assert pi.vendor == "Custom vendor"


# --- Admin action ---------------------------------------------------------------------

def test_admin_mark_known_acknowledges_new_device_alerts(admin_client):
    scan_once(PI, PHONE)
    pi = Device.objects.get(mac=PI.mac)
    phone = Device.objects.get(mac=PHONE.mac)

    admin_client.post(
        reverse("admin:devices_device_changelist"),
        {"action": "mark_known", "_selected_action": [pi.pk]},
    )
    pi.refresh_from_db()
    phone.refresh_from_db()
    assert pi.is_known and not phone.is_known
    assert Alert.objects.get(device=pi).acknowledged
    assert not Alert.objects.get(device=phone).acknowledged
