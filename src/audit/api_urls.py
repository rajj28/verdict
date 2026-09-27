"""Audit JSON API routes under /api/v1/."""
from django.urls import path

from audit.api import AuditListView

urlpatterns = [
    path("events/<slug:slug>/audit", AuditListView.as_view(), name="audit-list"),
]
