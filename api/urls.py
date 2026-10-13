from django.urls import include, path
from rest_framework.routers import DefaultRouter

from api import agent_views, views

router = DefaultRouter()
router.register("devices", views.DeviceViewSet, basename="device")
router.register("alerts", views.AlertViewSet, basename="alert")
router.register("events", views.DeviceEventViewSet, basename="event")
router.register("scans", views.ScanRunViewSet, basename="scan")
router.register("tokens", views.ApiTokenViewSet, basename="token")
router.register("hosts", views.RemoteHostViewSet, basename="host")
router.register("checks", views.HostCheckViewSet, basename="check")

urlpatterns = [
    path("auth/session/", views.SessionView.as_view(), name="auth-session"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("stats/", views.StatsView.as_view(), name="stats"),
    path("scan/", views.ScanNowView.as_view(), name="scan-now"),
    path("meta/", views.MetaView.as_view(), name="meta"),
    path("agent/checkin/", agent_views.CheckinView.as_view(), name="agent-checkin"),
    path("agent/scanner/heartbeat/", agent_views.ScannerHeartbeatView.as_view(), name="scanner-heartbeat"),
    path("agent/scanner/scans/", agent_views.ScanStartView.as_view(), name="scanner-scan-start"),
    path("agent/scanner/scans/<int:pk>/", agent_views.ScanReportView.as_view(), name="scanner-scan-report"),
    path("", include(router.urls)),
]
