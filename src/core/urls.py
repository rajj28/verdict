"""Core routes: the home page, /healthz for the container and /media for uploads."""
from django.urls import path

from core.views import healthz, home, integrity_admin, integrity_event, media

urlpatterns = [
    path("", home, name="home"),
    path("healthz", healthz, name="healthz"),
    path("media/<path:path>", media, name="media"),
    path("admin-panel/integrity", integrity_admin, name="integrity-admin"),
    path("manage/<slug:slug>/integrity", integrity_event, name="integrity-event"),
]
