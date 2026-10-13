from rest_framework import serializers

from api.models import ApiToken
from devices.models import Device, DeviceEvent, ScanRun
from monitoring.models import Alert, HostCheck, HostEvent, RemoteHost
from scanner.arp_scan import ScanError, validate_subnet


class DeviceSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(read_only=True)
    device_type_display = serializers.CharField(source="get_device_type_display", read_only=True)
    open_alerts = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Device
        fields = [
            "id",
            "mac",
            "ip",
            "hostname",
            "vendor",
            "label",
            "display_name",
            "device_type",
            "device_type_display",
            "notes",
            "is_known",
            "is_online",
            "is_random_mac",
            "missed_scans",
            "first_seen",
            "last_seen",
            "source",
            "open_alerts",
        ]
        read_only_fields = [
            "mac",
            "ip",
            "hostname",
            "vendor",
            "is_online",
            "is_random_mac",
            "missed_scans",
            "first_seen",
            "last_seen",
            "source",
        ]


class AlertSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    subject_name = serializers.SerializerMethodField()

    class Meta:
        model = Alert
        fields = [
            "id",
            "kind",
            "kind_display",
            "severity",
            "message",
            "created_at",
            "acknowledged",
            "acknowledged_at",
            "notified",
            "suppressed",
            "device",
            "remote_host",
            "subject_name",
        ]
        read_only_fields = fields

    def get_subject_name(self, obj) -> str:
        return str(obj.subject)


class DeviceEventSerializer(serializers.ModelSerializer):
    event_type_display = serializers.CharField(source="get_event_type_display", read_only=True)
    device_name = serializers.CharField(source="device.display_name", read_only=True)

    class Meta:
        model = DeviceEvent
        fields = ["id", "device", "device_name", "event_type", "event_type_display", "timestamp", "details"]
        read_only_fields = fields


class ScanRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScanRun
        fields = ["id", "started_at", "finished_at", "subnet", "devices_found", "status", "error"]
        read_only_fields = fields


class ApiTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiToken
        fields = ["id", "name", "token_prefix", "created_at", "last_used_at"]
        read_only_fields = ["id", "token_prefix", "created_at", "last_used_at"]

    def validate_name(self, value):
        user = self.context["request"].user
        if ApiToken.objects.filter(user=user, name=value).exists():
            raise serializers.ValidationError("You already have a token with this name.")
        return value


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False, style={"input_type": "password"})


class HostCheckSerializer(serializers.ModelSerializer):
    description = serializers.CharField(source="describe", read_only=True)

    class Meta:
        model = HostCheck
        fields = [
            "id",
            "host",
            "kind",
            "target",
            "port",
            "interval_seconds",
            "timeout_seconds",
            "failure_threshold",
            "enabled",
            "description",
            "status",
            "consecutive_failures",
            "last_checked_at",
            "last_latency_ms",
            "last_error",
        ]
        read_only_fields = [
            "status",
            "consecutive_failures",
            "last_checked_at",
            "last_latency_ms",
            "last_error",
        ]
        extra_kwargs = {
            "interval_seconds": {"min_value": 10, "max_value": 86400},
            "timeout_seconds": {"min_value": 1, "max_value": 30},
            "failure_threshold": {"min_value": 1, "max_value": 20},
            "port": {"min_value": 1, "max_value": 65535},
        }

    def validate(self, attrs):
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        target = attrs.get("target", getattr(self.instance, "target", "")).strip()
        port = attrs.get("port", getattr(self.instance, "port", None))
        if kind == HostCheck.Kind.HTTP:
            if not target.startswith(("http://", "https://")):
                raise serializers.ValidationError({"target": "HTTP checks need a full http:// or https:// URL."})
        else:
            if "/" in target or ":" in target:
                raise serializers.ValidationError({"target": "Use a hostname or IP address (port goes in 'port')."})
            if kind == HostCheck.Kind.TCP and not port:
                raise serializers.ValidationError({"port": "TCP checks need a port."})
        attrs["target"] = target
        return attrs


class RemoteHostSerializer(serializers.ModelSerializer):
    checks = HostCheckSerializer(many=True, read_only=True)
    open_alerts = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = RemoteHost
        fields = [
            "id",
            "name",
            "is_scanner",
            "scan_subnet",
            "is_active",
            "expected_interval_seconds",
            "status",
            "last_checkin",
            "last_ip",
            "agent_version",
            "reported_hostname",
            "cpu_percent",
            "mem_percent",
            "disk_percent",
            "uptime_seconds",
            "token_prefix",
            "created_at",
            "checks",
            "open_alerts",
        ]
        read_only_fields = [
            "status",
            "last_checkin",
            "last_ip",
            "agent_version",
            "reported_hostname",
            "cpu_percent",
            "mem_percent",
            "disk_percent",
            "uptime_seconds",
            "token_prefix",
            "created_at",
        ]
        extra_kwargs = {"expected_interval_seconds": {"min_value": 10, "max_value": 86400}}

    def validate_scan_subnet(self, value):
        value = value.strip()
        if not value:
            return ""
        try:
            return str(validate_subnet(value))
        except ScanError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class HostEventSerializer(serializers.ModelSerializer):
    event_type_display = serializers.CharField(source="get_event_type_display", read_only=True)

    class Meta:
        model = HostEvent
        fields = ["id", "host", "event_type", "event_type_display", "timestamp", "details"]
        read_only_fields = fields
