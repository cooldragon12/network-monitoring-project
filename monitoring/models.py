import hashlib
import secrets

from django.db import models
from django.db.models import Q
from django.utils import timezone

# Agent tokens are 256-bit random values, so a fast SHA-256 is sufficient (no
# brute-force risk as with passwords) and lets us look tokens up by hash.
TOKEN_BYTES = 32
TOKEN_PREFIX_LEN = 8


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RemoteHost(models.Model):
    class Status(models.TextChoices):
        UNKNOWN = "unknown", "Unknown (never checked in)"
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"

    name = models.CharField(max_length=100, unique=True)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    # First characters of the token, safe to display, to tell tokens apart.
    token_prefix = models.CharField(max_length=TOKEN_PREFIX_LEN, editable=False)
    is_active = models.BooleanField(default=True, help_text="Untick to revoke the agent's token")
    created_at = models.DateTimeField(default=timezone.now)
    last_checkin = models.DateTimeField(null=True, blank=True)
    expected_interval_seconds = models.PositiveIntegerField(default=300)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNKNOWN)

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
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self.token_hash = hash_token(token)
        self.token_prefix = token[:TOKEN_PREFIX_LEN]
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
        HOST_OFFLINE = "host_offline", "Remote host offline"

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
