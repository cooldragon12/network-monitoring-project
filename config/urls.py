from django.contrib import admin
from django.urls import path

admin.site.site_header = "Network Monitor"
admin.site.site_title = "Network Monitor"

urlpatterns = [
    path("admin/", admin.site.urls),
]
