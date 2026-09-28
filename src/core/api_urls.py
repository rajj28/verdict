"""Shared infrastructure: clock, ids, errors, csv, security headers."""
from django.urls import path

from core.integrity_api_urls import urlpatterns as integrity_urls
from core.outbox_api_urls import urlpatterns as outbox_urls
from core.tour import TourResetView, TourRoleView, TourStartView, TourStepView

urlpatterns = [
    *integrity_urls,
    *outbox_urls,
    path("tour/start", TourStartView.as_view(), name="tour-start"),
    path("tour/role", TourRoleView.as_view(), name="tour-role"),
    path("tour/reset", TourResetView.as_view(), name="tour-reset"),
    path("tour/step", TourStepView.as_view(), name="tour-step"),
]
