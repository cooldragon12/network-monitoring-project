from django.contrib import admin

from api.models import ApiToken


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "token_prefix", "created_at", "last_used_at")
    list_filter = ("user",)
    search_fields = ("name", "user__username", "token_prefix")
    readonly_fields = ("user", "name", "token_prefix", "created_at", "last_used_at")

    def has_add_permission(self, request):
        # Tokens are created from the dashboard so the plaintext can be shown once.
        return False
