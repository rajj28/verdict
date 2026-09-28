from django.urls import path

from core.outbox_api import AdminOutboxView, EventOutboxView

urlpatterns = [
    path("events/<slug:slug>/outbox", EventOutboxView.as_view(), name="event-outbox-api"),
    path("admin/outbox", AdminOutboxView.as_view(), name="admin-outbox-api"),
]
