from django.db import models
from django.db.models import Q
from django.utils import timezone

from config.tokens import TOKEN_PREFIX_LEN, generate_token, hash_token, token_prefix


class RemoteHost(models.Model):
    class Status(models.TextChoices):
        UNKNOWN = "unknown", "Unknown (never checked in)"
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"

    name = models.CharField(max_length=100, unique=True)
    # Also runs the LAN scanner agent (python -m scanner agent) with this token.
    is_scanner = models.BooleanField(
        default=False, help_text="This host runs the LAN scanner agent and may report scans"
    )
    scan_subnet = models.CharField(
        max_length=43, blank=True, help_text="Subnet this scanner sweeps; empty = SCAN_SUBNET or auto-detect"
    )
    scan_requested_at = models.DateTimeField(null=True, blank=True, editable=False)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    # First characters of the token, safe to display, to tell tokens apart.
    token_prefix = models.CharField(max_length=TOKEN_PREFIX_LEN, editable=False)
    is_active = models.BooleanField(default=True, help_text="Untick to revoke the agent's token")
    created_at = models.DateTimeField(default=timezone.now)
    last_checkin = models.DateTimeField(null=True, blank=True)
    expected_interval_seconds = models.PositiveIntegerField(
        default=120, help_text="How often the host checks in; offline after twice this without one"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNKNOWN)
    last_ip = models.GenericIPAddressField(null=True, blank=True, help_text="Address of the last check-in")
    agent_version = models.CharField(max_length=50, blank=True)

    # Latest metrics reported by the agent.
    reported_hostname = models.CharField(max_length=255, blank=True)
    cpu_percent = models.FloatField(null=True, blank=True)
    mem_percent = models.FloatField(null=True, blank=True)
    disk_percent = models.FloatField(null=True, blank=True)
    uptime_seconds = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def set_new_token(self) -> str:
        """Generate a token, store only its hash, and return the plaintext once."""
        token = generate_token()
        self.token_hash = hash_token(token)
        self.token_prefix = token_prefix(token)
        return token

    @classmethod
    def authenticate(cls, token: str) -> "RemoteHost | None":
        """Return the active host owning ``token``, or None."""
        if not token:
            return None
        return cls.objects.filter(token_hash=hash_token(token), is_active=True).first()


class Alert(models.Model):
    class Kind(models.TextChoices):
        NEW_DEVICE = "new_device", "New device"
        # Softer variant for randomized/private MACs (phones rotating addresses).
        NEW_RANDOM_MAC = "new_random_mac", "New device (random MAC)"
        DEVICE_OFFLINE = "device_offline", "Device offline"
        HOST_OFFLINE = "host_offline", "Host offline"
        CHECK_FAILED = "check_failed", "Check failing"

    class Severity(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    # Exactly one subject: a LAN device or a remote host.
    device = models.ForeignKey(
        "devices.Device", on_delete=models.CASCADE, related_name="alerts", null=True, blank=True
    )
    remote_host = models.ForeignKey(
        RemoteHost, on_delete=models.CASCADE, related_name="alerts", null=True, blank=True
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.WARNING)
    message = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    acknowledged = models.BooleanField(default=False)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    # Notification delivery state.
    notified = models.BooleanField(default=False, help_text="Delivered on at least one channel")
    notified_at = models.DateTimeField(null=True, blank=True)
    notified_channels = models.JSONField(default=list, blank=True)
    suppressed = models.BooleanField(default=False, help_text="Not sent: duplicate within cooldown")
    notify_attempts = models.PositiveIntegerField(default=0)
    notify_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["acknowledged", "-created_at"]),
            models.Index(fields=["kind", "-created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(device__isnull=False, remote_host__isnull=True)
                    | Q(device__isnull=True, remote_host__isnull=False)
                ),
                name="alert_exactly_one_subject",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.get_kind_display()}] {self.subject}"

    @property
    def subject(self):
        return self.device or self.remote_host

    def acknowledge(self) -> None:
        if not self.acknowledged:
            self.acknowledged = True
            self.acknowledged_at = timezone.now()
            self.save(update_fields=["acknowledged", "acknowledged_at"])


class HostEvent(models.Model):
    class EventType(models.TextChoices):
        ONLINE = "online", "Came online"
        OFFLINE = "offline", "Went offline"
        CHECK_FAILED = "check_failed", "Check failing"
        CHECK_RECOVERED = "check_recovered", "Check recovered"

    host = models.ForeignKey(RemoteHost, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    timestamp = models.DateTimeField(default=timezone.now)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["host", "-timestamp"])]

    def __str__(self) -> str:
        return f"{self.host} {self.get_event_type_display()} @ {self.timestamp:%Y-%m-%d %H:%M:%S}"


class HostCheck(models.Model):
    """Server-side probe of a host: ICMP ping, TCP connect, or HTTP GET."""

    class Kind(models.TextChoices):
        PING = "ping", "Ping"
        TCP = "tcp", "TCP port"
        HTTP = "http", "HTTP(S)"

    class Status(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        UP = "up", "Up"
        DOWN = "down", "Down"

    host = models.ForeignKey(RemoteHost, on_delete=models.CASCADE, related_name="checks")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    # Hostname/IP for ping and tcp; full http(s):// URL for http.
    target = models.CharField(max_length=500)
    port = models.PositiveIntegerField(null=True, blank=True)
    interval_seconds = models.PositiveIntegerField(default=60)
    timeout_seconds = models.PositiveIntegerField(default=5)
    # Consecutive failures before the check counts as down (and alerts).
    failure_threshold = models.PositiveIntegerField(default=2)
    enabled = models.BooleanField(default=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNKNOWN)
    consecutive_failures = models.PositiveIntegerField(default=0)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_latency_ms = models.FloatField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["host__name", "kind", "target"]

    def __str__(self) -> str:
        return f"{self.host}: {self.describe()}"

    def describe(self) -> str:
        if self.kind == self.Kind.TCP:
            return f"tcp {self.target}:{self.port}"
        return f"{self.kind} {self.target}"
