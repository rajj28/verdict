"""Core routes: /healthz for the container and /media for authorized uploads."""
from django.urls import path

from core.views import healthz, media

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("media/<path:path>", media, name="media"),
]
