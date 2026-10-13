from django.contrib import admin, messages

from monitoring.models import Alert, HostCheck, HostEvent, RemoteHost
from monitoring.notifications import enabled_channels
from monitoring.tasks import send_alert_via_channel


def _show_token_once(request, host, token):
    messages.warning(
        request,
        f"Agent token for '{host.name}' (shown only once; store it now): {token}",
    )


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "severity", "subject_display", "acknowledged", "notified", "suppressed")
    list_filter = ("acknowledged", "kind", "severity", "notified", "suppressed")
    search_fields = ("message", "device__mac", "device__ip", "device__label", "remote_host__name")
    date_hierarchy = "created_at"
    list_select_related = ("device", "remote_host")
    readonly_fields = (
        "device",
        "remote_host",
        "kind",
        "severity",
        "message",
        "created_at",
        "acknowledged_at",
        "notified",
        "notified_at",
        "notified_channels",
        "suppressed",
        "notify_attempts",
        "notify_error",
    )
    actions = ["acknowledge_selected", "send_notification_now"]

    @admin.display(description="Subject")
    def subject_display(self, obj):
        return obj.subject

    @admin.action(description="Acknowledge selected alerts")
    def acknowledge_selected(self, request, queryset):
        count = 0
        for alert in queryset.filter(acknowledged=False):
            alert.acknowledge()
            count += 1
        self.message_user(request, f"Acknowledged {count} alert(s).")

    @admin.action(description="Send notification now (ignores cooldown)")
    def send_notification_now(self, request, queryset):
        channels = enabled_channels()
        if not channels:
            self.message_user(request, "No notification channels are enabled.", messages.ERROR)
            return
        for alert in queryset:
            # Explicit resend: clear prior delivery state so every channel sends again.
            Alert.objects.filter(pk=alert.pk).update(notified_channels=[], suppressed=False)
            for channel in channels:
                send_alert_via_channel.delay(alert.pk, channel)
        self.message_user(request, f"Queued {queryset.count()} alert(s) on {', '.join(channels)}.")

    def has_add_permission(self, request):
        return False


@admin.register(RemoteHost)
class RemoteHostAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "is_scanner", "is_active", "last_checkin", "expected_interval_seconds", "token_prefix")
    list_filter = ("status", "is_scanner", "is_active")
    search_fields = ("name", "reported_hostname")
    readonly_fields = (
        "token_prefix",
        "created_at",
        "last_checkin",
        "last_ip",
        "agent_version",
        "status",
        "reported_hostname",
        "cpu_percent",
        "mem_percent",
        "disk_percent",
        "uptime_seconds",
    )
    fieldsets = (
        (None, {"fields": ("name", "expected_interval_seconds", "is_active")}),
        ("LAN scanner", {"fields": ("is_scanner", "scan_subnet")}),
        ("Token", {"fields": ("token_prefix", "created_at")}),
        (
            "Last check-in",
            {
                "fields": (
                    "status",
                    "last_checkin",
                    "last_ip",
                    "agent_version",
                    "reported_hostname",
                    "cpu_percent",
                    "mem_percent",
                    "disk_percent",
                    "uptime_seconds",
                )
            },
        ),
    )
    actions = ["revoke_tokens", "rotate_tokens"]

    def save_model(self, request, obj, form, change):
        token = None if change else obj.set_new_token()
        super().save_model(request, obj, form, change)
        if token:
            _show_token_once(request, obj, token)

    @admin.action(description="Revoke token (deactivate agent)")
    def revoke_tokens(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"Revoked {count} agent token(s).")

    @admin.action(description="Rotate token (old token stops working)")
    def rotate_tokens(self, request, queryset):
        for host in queryset:
            token = host.set_new_token()
            host.is_active = True
            host.save(update_fields=["token_hash", "token_prefix", "is_active"])
            _show_token_once(request, host, token)


@admin.register(HostCheck)
class HostCheckAdmin(admin.ModelAdmin):
    list_display = ("host", "kind", "target", "port", "status", "enabled", "last_checked_at", "last_latency_ms")
    list_filter = ("kind", "status", "enabled")
    search_fields = ("host__name", "target")
    readonly_fields = ("status", "consecutive_failures", "last_checked_at", "last_latency_ms", "last_error")


@admin.register(HostEvent)
class HostEventAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "host", "event_type")
    list_filter = ("event_type",)
    search_fields = ("host__name",)
    date_hierarchy = "timestamp"
    readonly_fields = ("host", "event_type", "timestamp", "details")

    def has_add_permission(self, request):
        return False
