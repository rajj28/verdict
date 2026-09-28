"""Audit JSON API routes under /api/v1/."""
from django.urls import path

from audit.api import (
    ArchivedAuditCheckpointView,
    ArchivedAuditVerifyView,
    AuditListView,
    EventAuditCheckpointView,
    EventAuditVerifyView,
    GlobalAuditCheckpointView,
    GlobalAuditVerifyView,
)

urlpatterns = [
    path("events/<slug:slug>/audit", AuditListView.as_view(), name="audit-list"),
    path("events/<slug:slug>/audit/verify", EventAuditVerifyView.as_view(), name="audit-verify"),
    path("events/<slug:slug>/audit/checkpoint", EventAuditCheckpointView.as_view(),
         name="audit-checkpoint"),
    path("admin/audit/verify", GlobalAuditVerifyView.as_view(), name="audit-global-verify"),
    path("admin/audit/checkpoint", GlobalAuditCheckpointView.as_view(),
         name="audit-global-checkpoint"),
    path("admin/audit/<str:chain_id>/verify", ArchivedAuditVerifyView.as_view(),
         name="audit-archived-verify"),
    path("admin/audit/<str:chain_id>/checkpoint", ArchivedAuditCheckpointView.as_view(),
         name="audit-archived-checkpoint"),
]
