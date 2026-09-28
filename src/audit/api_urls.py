"""Audit JSON API routes under /api/v1/."""
from django.urls import path

from audit.api import AuditCheckpointView, AuditListView, AuditVerifyView

urlpatterns = [
    path("events/<slug:slug>/audit", AuditListView.as_view(), name="audit-list"),
    path("events/<slug:slug>/audit/verify", AuditVerifyView.as_view(), name="audit-verify"),
    path("events/<slug:slug>/audit/checkpoint", AuditCheckpointView.as_view(), name="audit-checkpoint"),
    path("admin/audit/verify", AuditVerifyView.as_view(), name="audit-global-verify"),
    path("admin/audit/checkpoint", AuditCheckpointView.as_view(), name="audit-global-checkpoint"),
    path("admin/audit/<str:chain_id>/verify", AuditVerifyView.as_view(), name="audit-archived-verify"),
    path("admin/audit/<str:chain_id>/checkpoint", AuditCheckpointView.as_view(), name="audit-archived-checkpoint"),
]
