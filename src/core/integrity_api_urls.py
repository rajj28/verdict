"""Integrity probe JSON API routes."""
from django.urls import path

from core.integrity_api import IntegrityProbeView

urlpatterns = [
    path("integrity/probe", IntegrityProbeView.as_view(), name="integrity-probe"),
]
