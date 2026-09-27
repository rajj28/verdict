"""Fixture import, JSON round trip and CSV exports.

JSON API routes under /api/v1/.
"""
from django.urls import path

from interop.api import EventExportView, EventJsonExportView, ImportView

urlpatterns = [
    path("events/<slug:slug>/exports/<str:kind>.csv", EventExportView.as_view(),
         name="event-export"),
    path("events/<slug:slug>/exports/event.json", EventJsonExportView.as_view(),
         name="event-export-json"),
    path("imports", ImportView.as_view(), name="fixture-import"),
]
