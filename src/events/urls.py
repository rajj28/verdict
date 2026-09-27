"""Events, tracks, prizes, custom questions and per-event roles.

HTML routes (server-rendered pages).
"""
from django.urls import path

from events import views

urlpatterns = [
    path("events/", views.event_list, name="events"),
    path("events/new", views.event_new, name="event-new"),
    path("events/<slug:slug>/", views.event_detail, name="event-detail"),
    path("manage/<slug:slug>/", views.manage_overview, name="manage-overview"),
    path("manage/<slug:slug>/settings", views.manage_settings, name="manage-settings"),
    path("manage/<slug:slug>/setup", views.manage_setup, name="manage-setup"),
    path("manage/<slug:slug>/tracks-prizes-questions", views.manage_setup,
         name="manage-tracks-prizes-questions"),
]
