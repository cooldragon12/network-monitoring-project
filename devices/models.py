from django.db import models
from django.utils import timezone


class Device(models.Model):
    class Source(models.TextChoices):
        LAN = "lan", "LAN scan"
        AGENT = "agent", "Agent"

    class DeviceType(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        ROUTER = "router", "Router / AP"
        COMPUTER = "computer", "Computer"
        PHONE = "phone", "Phone / tablet"
        SERVER = "server", "Server / NAS"
        IOT = "iot", "IoT / smart home"
        MEDIA = "media", "TV / media"
        PRINTER = "printer", "Printer"
        OTHER = "other", "Other"

    # Normalised lower-case "aa:bb:cc:dd:ee:ff".
    mac = models.CharField("MAC address", max_length=17, unique=True)
    ip = models.GenericIPAddressField("IP address", protocol="IPv4", null=True, blank=True)
    hostname = models.CharField(max_length=255, blank=True)
    vendor = models.CharField(max_length=255, blank=True, help_text="From MAC OUI lookup")
    label = models.CharField(max_length=100, blank=True, help_text="Your friendly name")
    device_type = models.CharField(max_length=20, choices=DeviceType.choices, default=DeviceType.UNKNOWN)
    is_known = models.BooleanField(default=False, help_text="Approved by you")
    is_online = models.BooleanField(default=False)
    # Randomized/private MAC (phone Wi-Fi privacy); may rotate for the same device.
    is_random_mac = models.BooleanField("random MAC", default=False)
    # Consecutive scans this device did not answer; drives online -> offline.
    missed_scans = models.PositiveIntegerField(default=0)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.LAN)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-is_online", "ip"]
        indexes = [
            models.Index(fields=["is_known"]),
            models.Index(fields=["is_online"]),
            models.Index(fields=["ip"]),
        ]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        return self.label or self.hostname or self.vendor or self.mac


class ScanRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    subnet = models.CharField(max_length=43)
    scanner = models.ForeignKey(
        "monitoring.RemoteHost", on_delete=models.SET_NULL, null=True, blank=True, related_name="scan_runs"
    )
    devices_found = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["-started_at"])]

    def __str__(self) -> str:
        return f"{self.subnet} @ {self.started_at:%Y-%m-%d %H:%M:%S} ({self.status})"

    @property
    def duration(self):
        return self.finished_at - self.started_at if self.finished_at else None


class DeviceEvent(models.Model):
    class EventType(models.TextChoices):
        FIRST_SEEN = "first_seen", "First seen"
        ONLINE = "online", "Came online"
        OFFLINE = "offline", "Went offline"
        IP_CHANGED = "ip_changed", "IP changed"
        NEW_DEVICE = "new_device", "New device"

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    timestamp = models.DateTimeField(default=timezone.now)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["device", "-timestamp"]),
            models.Index(fields=["event_type", "-timestamp"]),
        ]

    def __str__(self) -> str:
        return f"{self.device} {self.get_event_type_display()} @ {self.timestamp:%Y-%m-%d %H:%M:%S}"
