"""Shared infrastructure: clock, ids, errors, csv, security headers."""
from core.integrity_api_urls import urlpatterns as integrity_urls
from core.outbox_api_urls import urlpatterns as outbox_urls

urlpatterns = [*integrity_urls, *outbox_urls]
