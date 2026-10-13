from django.contrib import admin

from devices.models import Device, DeviceEvent, ScanRun
from monitoring.models import Alert


class DeviceEventInline(admin.TabularInline):
    model = DeviceEvent
    fields = ("timestamp", "event_type", "details")
    readonly_fields = fields
    extra = 0
    can_delete = False
    ordering = ("-timestamp",)
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).order_by("-timestamp")


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "mac",
        "ip",
        "vendor",
        "device_type",
        "is_known",
        "is_online",
        "is_random_mac",
        "last_seen",
    )
    list_display_links = ("display_name", "mac")
    list_filter = ("is_known", "is_online", "is_random_mac", "source", "device_type")
    search_fields = ("mac", "ip", "label", "hostname", "vendor")
    readonly_fields = ("first_seen", "last_seen", "missed_scans", "is_random_mac", "source")
    fieldsets = (
        (None, {"fields": ("label", "device_type", "is_known", "notes")}),
        ("Network", {"fields": ("mac", "ip", "hostname", "vendor", "is_random_mac", "source")}),
        ("Status", {"fields": ("is_online", "missed_scans", "first_seen", "last_seen")}),
    )
    inlines = [DeviceEventInline]
    actions = ["mark_known"]

    @admin.action(description="Mark selected as known")
    def mark_known(self, request, queryset):
        devices = list(queryset.filter(is_known=False))
        queryset.filter(pk__in=[d.pk for d in devices]).update(is_known=True)
        acked = 0
        for alert in Alert.objects.filter(
            device__in=devices,
            acknowledged=False,
            kind__in=[Alert.Kind.NEW_DEVICE, Alert.Kind.NEW_RANDOM_MAC],
        ):
            alert.acknowledge()
            acked += 1
        self.message_user(
            request, f"Marked {len(devices)} device(s) as known; acknowledged {acked} new-device alert(s)."
        )

    @admin.display(description="Name", ordering="label")
    def display_name(self, obj):
        return obj.display_name


@admin.register(ScanRun)
class ScanRunAdmin(admin.ModelAdmin):
    list_display = ("started_at", "subnet", "status", "devices_found", "duration")
    list_filter = ("status", "subnet")
    date_hierarchy = "started_at"
    readonly_fields = ("started_at", "finished_at", "subnet", "devices_found", "status", "error")

    def has_add_permission(self, request):
        return False


@admin.register(DeviceEvent)
class DeviceEventAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "device", "event_type")
    list_filter = ("event_type",)
    search_fields = ("device__mac", "device__ip", "device__label", "device__hostname")
    date_hierarchy = "timestamp"
    list_select_related = ("device",)
    readonly_fields = ("device", "event_type", "timestamp", "details")

    def has_add_permission(self, request):
        return False
