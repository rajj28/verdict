"""Core routes: the home page, /healthz for the container and /media for uploads."""
from django.urls import path

from core.views import healthz, home, media

urlpatterns = [
    path("", home, name="home"),
    path("healthz", healthz, name="healthz"),
    path("media/<path:path>", media, name="media"),
]
