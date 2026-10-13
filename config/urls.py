from django.contrib import admin
from django.urls import include, path, re_path

from config.views import spa_index

admin.site.site_header = "Network Monitor"
admin.site.site_title = "Network Monitor"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("api.urls")),
    # Everything else is a client-side route of the React app.
    re_path(r"^(?!api/|admin/|static/).*$", spa_index, name="spa"),
]
