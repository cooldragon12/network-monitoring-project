from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import mixins, status, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.models import ApiToken
from api.serializers import (
    AlertSerializer,
    ApiTokenSerializer,
    DeviceEventSerializer,
    DeviceSerializer,
    LoginSerializer,
    ScanRunSerializer,
)
from devices.models import Device, DeviceEvent, ScanRun
from devices.services import mark_known
from devices.tasks import ScanAlreadyPending, ScanQueueUnavailable, request_lan_scan, scan_requested
from monitoring.models import Alert

_TRUE = {"1", "true", "yes"}
_FALSE = {"0", "false", "no"}


def _bool_param(request, name):
    value = request.query_params.get(name)
    if value is None or value == "":
        return None
    value = value.lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValidationError({name: "must be true or false"})


def _filter_params(request, qs, int_params=(), str_params=()):
    for name in int_params:
        value = request.query_params.get(name)
        if value:
            if not value.isdigit():
                raise ValidationError({name: "must be an integer id"})
            qs = qs.filter(**{name: int(value)})
    for name in str_params:
        value = request.query_params.get(name)
        if value:
            qs = qs.filter(**{name: value})
    return qs


class LargePagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 500


# --- Auth -------------------------------------------------------------------------------


def _user_payload(user):
    return {"authenticated": True, "user": {"username": user.get_username(), "is_staff": user.is_staff}}


@method_decorator(ensure_csrf_cookie, name="dispatch")
class SessionView(APIView):
    """Current session state; also sets the CSRF cookie for the SPA."""

    permission_classes = [AllowAny]

    def get(self, request):
        if request.user.is_authenticated:
            return Response(_user_payload(request.user))
        return Response({"authenticated": False, "user": None})


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = authenticate(request, **data.validated_data)
        if user is None:
            return Response({"detail": "Invalid username or password."}, status=status.HTTP_400_BAD_REQUEST)
        login(request, user)
        return Response(_user_payload(user))


class LogoutView(APIView):
    authentication_classes = [SessionAuthentication]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ApiTokenViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """Manage your personal API tokens. Browser session only: tokens cannot mint tokens."""

    serializer_class = ApiTokenSerializer
    authentication_classes = [SessionAuthentication]
    pagination_class = None

    def get_queryset(self):
        return ApiToken.objects.filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance, token = ApiToken.issue(request.user, serializer.validated_data["name"])
        return Response({**ApiTokenSerializer(instance).data, "token": token}, status=status.HTTP_201_CREATED)


# --- Devices --------------------------------------------------------------------------


class DeviceViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """LAN devices. ``DELETE`` forgets a device (it is re-detected as new if seen again)."""

    serializer_class = DeviceSerializer
    pagination_class = LargePagination
    http_method_names = ["get", "patch", "post", "delete", "head", "options"]
    ordering_fields = {"ip", "mac", "label", "vendor", "first_seen", "last_seen", "is_online", "is_known"}

    def get_queryset(self):
        qs = Device.objects.annotate(open_alerts=Count("alerts", filter=Q(alerts__acknowledged=False)))
        if self.action != "list":
            return qs
        params = self.request.query_params
        status_param = params.get("status")
        if status_param == "online":
            qs = qs.filter(is_online=True)
        elif status_param == "offline":
            qs = qs.filter(is_online=False)
        elif status_param:
            raise ValidationError({"status": "must be online or offline"})
        for param, field in (("known", "is_known"), ("random", "is_random_mac")):
            value = _bool_param(self.request, param)
            if value is not None:
                qs = qs.filter(**{field: value})
        search = params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(mac__icontains=search)
                | Q(ip__icontains=search)
                | Q(label__icontains=search)
                | Q(hostname__icontains=search)
                | Q(vendor__icontains=search)
            )
        ordering = params.get("ordering", "")
        if ordering.lstrip("-") in self.ordering_fields:
            qs = qs.order_by(ordering, "mac")
        else:
            qs = qs.order_by("is_known", "-is_online", "ip")  # unknown devices first
        return qs

    def perform_update(self, serializer):
        becoming_known = serializer.validated_data.get("is_known") and not serializer.instance.is_known
        if becoming_known:
            serializer.validated_data.pop("is_known")
        device = serializer.save()
        if becoming_known:
            mark_known([device])

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        response.data = self.get_serializer(self.get_queryset().get(pk=kwargs["pk"])).data
        return response

    @action(detail=False, methods=["post"], url_path="mark-known")
    def mark_known_bulk(self, request):
        ids = request.data.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            raise ValidationError({"ids": "must be a list of device ids"})
        marked, acked = mark_known(Device.objects.filter(pk__in=ids))
        return Response({"marked": marked, "alerts_acknowledged": acked})

    @action(detail=True, methods=["get"])
    def events(self, request, pk=None):
        device = self.get_object()
        page = self.paginate_queryset(device.events.all())
        return self.get_paginated_response(DeviceEventSerializer(page, many=True).data)


# --- Alerts / events / scans -------------------------------------------------------------


class AlertViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AlertSerializer

    def get_queryset(self):
        qs = Alert.objects.select_related("device", "remote_host")
        acknowledged = _bool_param(self.request, "acknowledged")
        if acknowledged is not None:
            qs = qs.filter(acknowledged=acknowledged)
        return _filter_params(
            self.request, qs, int_params=("device", "remote_host"), str_params=("kind", "severity")
        )

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        alert = self.get_object()
        alert.acknowledge()
        return Response(self.get_serializer(alert).data)

    @action(detail=False, methods=["post"], url_path="acknowledge-all")
    def acknowledge_all(self, request):
        count = Alert.objects.filter(acknowledged=False).update(acknowledged=True, acknowledged_at=timezone.now())
        return Response({"acknowledged": count})


class DeviceEventViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DeviceEventSerializer

    def get_queryset(self):
        qs = DeviceEvent.objects.select_related("device")
        return _filter_params(self.request, qs, int_params=("device",), str_params=("event_type",))


class ScanRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ScanRunSerializer
    queryset = ScanRun.objects.all()


class ScanNowView(APIView):
    """Queue an immediate LAN scan for the scanner worker; returns at once."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "scan"

    def post(self, request):
        previous = ScanRun.objects.values_list("id", flat=True).first()
        try:
            request_lan_scan()
        except ScanAlreadyPending:
            return Response({"detail": "A scan is already queued or running."}, status=status.HTTP_409_CONFLICT)
        except ScanQueueUnavailable:
            return Response(
                {"detail": "Could not reach the task queue (is Redis running?)."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"queued": True, "after_scan_id": previous}, status=status.HTTP_202_ACCEPTED)


# --- Summary -------------------------------------------------------------------------


class StatsView(APIView):
    def get(self, request):
        devices = Device.objects.aggregate(
            total=Count("id"),
            online=Count("id", filter=Q(is_online=True)),
            unknown=Count("id", filter=Q(is_known=False)),
            unknown_online=Count("id", filter=Q(is_known=False, is_online=True)),
        )
        last_scan = ScanRun.objects.first()
        last_success = ScanRun.objects.filter(status=ScanRun.Status.SUCCESS).values_list("finished_at", flat=True).first()
        return Response(
            {
                "devices": devices,
                "open_alerts": Alert.objects.filter(acknowledged=False).count(),
                "last_scan": ScanRunSerializer(last_scan).data if last_scan else None,
                "last_successful_scan_at": last_success,
                "scan_interval_minutes": settings.SCANNER["INTERVAL_MINUTES"],
                "scan_requested": scan_requested(),
                "subnet": settings.SCANNER["SUBNET"] or None,
            }
        )


class MetaView(APIView):
    """Static choices for the UI."""

    def get(self, request):
        return Response(
            {
                "device_types": [{"value": v, "label": l} for v, l in Device.DeviceType.choices],
                "alert_kinds": [{"value": v, "label": l} for v, l in Alert.Kind.choices],
            }
        )
