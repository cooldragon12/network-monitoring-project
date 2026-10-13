from django.urls import include, path
from rest_framework.routers import DefaultRouter

from api import views

router = DefaultRouter()
router.register("devices", views.DeviceViewSet, basename="device")
router.register("alerts", views.AlertViewSet, basename="alert")
router.register("events", views.DeviceEventViewSet, basename="event")
router.register("scans", views.ScanRunViewSet, basename="scan")
router.register("tokens", views.ApiTokenViewSet, basename="token")

urlpatterns = [
    path("auth/session/", views.SessionView.as_view(), name="auth-session"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("stats/", views.StatsView.as_view(), name="stats"),
    path("meta/", views.MetaView.as_view(), name="meta"),
    path("", include(router.urls)),
]
