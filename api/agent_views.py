"""Endpoints used by agents (metrics agent and LAN scanner agent).

Authenticated only by a per-host bearer token (RemoteHost; stored hashed).
User sessions and personal API tokens are not accepted here, and agent
tokens are not accepted anywhere else.
"""

from django.contrib.auth.models import AnonymousUser
from django.shortcuts import get_object_or_404
from rest_framework import authentication, exceptions, permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from config.tokens import parse_bearer
from devices.models import ScanRun
from devices.scanning import ReportError, finish_scan, heartbeat_response, start_scan
from monitoring.agents import record_checkin
from monitoring.models import RemoteHost


class AgentTokenAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        token = parse_bearer(request.META.get("HTTP_AUTHORIZATION", ""))
        if token is None:
            return None
        host = RemoteHost.authenticate(token)
        if host is None:
            raise exceptions.AuthenticationFailed("Invalid or revoked agent token.")
        return AnonymousUser(), host

    def authenticate_header(self, request):
        return "Bearer"


class IsAgent(permissions.BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.auth, RemoteHost)


class IsScannerAgent(IsAgent):
    message = "This host is not enabled as a scanner (tick 'is scanner' on the host)."

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.auth.is_scanner


class AgentRateThrottle(SimpleRateThrottle):
    """Rate limit per host (not per IP: several agents may share a NAT)."""

    scope = "agent"

    def get_cache_key(self, request, view):
        if isinstance(request.auth, RemoteHost):
            return self.cache_format % {"scope": self.scope, "ident": f"host-{request.auth.pk}"}
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class AgentView(APIView):
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = [IsAgent]
    throttle_classes = [AgentRateThrottle]

    def checkin(self, request, **fields):
        return record_checkin(request.auth, ip=self.request.META.get("REMOTE_ADDR"), **fields)


class _Pct(serializers.FloatField):
    def __init__(self, **kwargs):
        super().__init__(min_value=0, max_value=100, required=False, allow_null=True, **kwargs)


class CheckinSerializer(serializers.Serializer):
    hostname = serializers.CharField(max_length=255, required=False, allow_blank=True)
    agent_version = serializers.CharField(max_length=50, required=False, allow_blank=True)
    cpu_percent = _Pct()
    mem_percent = _Pct()
    disk_percent = _Pct()
    uptime_seconds = serializers.IntegerField(min_value=0, required=False, allow_null=True)


class CheckinView(AgentView):
    """POST /api/agent/checkin/: metrics agent heartbeat."""

    def post(self, request):
        data = CheckinSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = dict(data.validated_data)
        host = self.checkin(
            request, hostname=v.pop("hostname", ""), version=v.pop("agent_version", ""), metrics=v
        )
        return Response(
            {"status": "ok", "host": host.name, "expected_interval_seconds": host.expected_interval_seconds}
        )


class ScannerHeartbeatSerializer(serializers.Serializer):
    hostname = serializers.CharField(max_length=255, required=False, allow_blank=True)
    agent_version = serializers.CharField(max_length=50, required=False, allow_blank=True)


class ScannerHeartbeatView(AgentView):
    """POST /api/agent/scanner/heartbeat/: returns whether to scan now, and how."""

    permission_classes = [IsScannerAgent]

    def post(self, request):
        data = ScannerHeartbeatSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        host = self.checkin(
            request,
            hostname=data.validated_data.get("hostname", ""),
            version=data.validated_data.get("agent_version", ""),
        )
        return Response(heartbeat_response(host))


class ScanStartSerializer(serializers.Serializer):
    subnet = serializers.CharField(max_length=43)


class ScanStartView(AgentView):
    """POST /api/agent/scanner/scans/: open a scan run before sweeping."""

    permission_classes = [IsScannerAgent]

    def post(self, request):
        data = ScanStartSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            run = start_scan(request.auth, data.validated_data["subnet"])
        except ReportError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"id": run.pk, "subnet": run.subnet}, status=status.HTTP_201_CREATED)


class ScanReportSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["success", "failed"])
    error = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    hosts = serializers.ListField(child=serializers.DictField(), required=False, max_length=4096)
    duration_seconds = serializers.FloatField(required=False, min_value=0)


class ScanReportView(AgentView):
    """POST /api/agent/scanner/scans/{id}/: results of a sweep."""

    permission_classes = [IsScannerAgent]

    def post(self, request, pk):
        run = get_object_or_404(ScanRun, pk=pk, scanner=request.auth)
        data = ScanReportSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        try:
            summary = finish_scan(
                run, ok=v["status"] == "success", raw_hosts=v.get("hosts", []), error=v.get("error", "")
            )
        except ReportError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        run.refresh_from_db()
        body = {"id": run.pk, "status": run.status, "devices_found": run.devices_found, "error": run.error}
        if summary:
            body["new_devices"] = len(summary.new_devices)
        return Response(body)
