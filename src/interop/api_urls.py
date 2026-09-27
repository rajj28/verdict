"""Fixture import, JSON round trip and CSV exports.

JSON API routes under /api/v1/.
"""
from django.urls import path

from interop.api import (
    CertificateExportView,
    EventExportView,
    EventJsonExportView,
    ImportView,
    JudgeRecordBulkIssueView,
    JudgeRecordRevokeView,
    RecordVerifyView,
    WebhookCollectionView,
    WebhookDeliveryReplayView,
    WebhookEndpointDetailView,
    WebhookEndpointTestView,
)

urlpatterns = [
    path("events/<slug:slug>/exports/<str:kind>.csv", EventExportView.as_view(),
         name="event-export"),
    path("events/<slug:slug>/exports/event.json", EventJsonExportView.as_view(),
         name="event-export-json"),
    path("events/<slug:slug>/certificates.csv", CertificateExportView.as_view(),
         name="certificate-export"),
    path("events/<slug:slug>/webhooks", WebhookCollectionView.as_view(), name="webhook-collection"),
    path("events/<slug:slug>/webhooks/<str:public_id>", WebhookEndpointDetailView.as_view(),
         name="webhook-detail"),
    path("events/<slug:slug>/webhooks/<str:public_id>/test", WebhookEndpointTestView.as_view(),
         name="webhook-test"),
    path("events/<slug:slug>/webhook-deliveries/<str:public_id>/replay",
         WebhookDeliveryReplayView.as_view(), name="webhook-delivery-replay"),
    path("events/<slug:slug>/judge-records", JudgeRecordBulkIssueView.as_view(),
         name="judge-record-bulk-issue"),
    path("events/<slug:slug>/judge-records/<str:record_id>/revoke",
         JudgeRecordRevokeView.as_view(), name="judge-record-revoke"),
    path("records/verify", RecordVerifyView.as_view(), name="record-verify"),
    path("imports", ImportView.as_view(), name="fixture-import"),
]
