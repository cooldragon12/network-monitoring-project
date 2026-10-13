from rest_framework import serializers

from api.models import ApiToken
from devices.models import Device, DeviceEvent, ScanRun
from monitoring.models import Alert


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
