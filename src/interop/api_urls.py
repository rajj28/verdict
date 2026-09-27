"""Fixture import, JSON round trip and CSV exports.

JSON API routes under /api/v1/.
"""
from django.urls import path

from interop.api import EventExportView

urlpatterns = [
    path("events/<slug:slug>/exports/<str:kind>.csv", EventExportView.as_view(),
         name="event-export"),
]
