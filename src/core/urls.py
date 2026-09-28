"""Core routes: the home page, /healthz for the container and /media for uploads."""
from django.urls import path

from core.calibration_views import calibration
from core.views import healthz, home, integrity_admin, integrity_event, media
from core.outbox_views import admin_outbox, event_outbox
from core.tour import tour_calibration, tour_page

urlpatterns = [
    path("", home, name="home"),
    path("tour", tour_page, name="tour"),
    path("tour/<slug:slug>", tour_page, name="tour-event"),
    path("manage/<slug:slug>/calibration", tour_calibration, name="tour-calibration"),
    path("healthz", healthz, name="healthz"),
    path("media/<path:path>", media, name="media"),
    path("admin-panel/integrity", integrity_admin, name="integrity-admin"),
    path("manage/<slug:slug>/integrity", integrity_event, name="integrity-event"),
    path("manage/<slug:slug>/outbox", event_outbox, name="event-outbox"),
    # The calibration check compares the showcase's planted truth with the engine.
    path("manage/<slug:slug>/calibration", calibration, name="manage-calibration"),
    path("admin-panel/outbox", admin_outbox, name="admin-outbox"),
]
