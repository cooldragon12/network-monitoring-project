from django.contrib import admin, messages

from monitoring.models import Alert, RemoteHost


def _show_token_once(request, host, token):
    messages.warning(
        request,
        f"Agent token for '{host.name}' (shown only once; store it now): {token}",
    )


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "severity", "subject_display", "acknowledged", "notified")
    list_filter = ("acknowledged", "kind", "severity", "notified")
    search_fields = ("message", "device__mac", "device__ip", "device__label", "remote_host__name")
    date_hierarchy = "created_at"
    list_select_related = ("device", "remote_host")
    readonly_fields = ("device", "remote_host", "kind", "severity", "message", "created_at", "acknowledged_at", "notified")
    actions = ["acknowledge_selected"]

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

    def has_add_permission(self, request):
        return False


@admin.register(RemoteHost)
class RemoteHostAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "is_active", "last_checkin", "expected_interval_seconds", "token_prefix")
    list_filter = ("status", "is_active")
    search_fields = ("name", "reported_hostname")
    readonly_fields = (
        "token_prefix",
        "created_at",
        "last_checkin",
        "status",
        "reported_hostname",
        "cpu_percent",
        "mem_percent",
        "disk_percent",
        "uptime_seconds",
    )
    fieldsets = (
        (None, {"fields": ("name", "expected_interval_seconds", "is_active")}),
        ("Token", {"fields": ("token_prefix", "created_at")}),
        (
            "Last check-in",
            {
                "fields": (
                    "status",
                    "last_checkin",
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
