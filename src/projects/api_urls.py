"""Projects, revisions, images and answers.

JSON API routes under /api/v1/.

No trailing slashes anywhere: the acceptance checker POSTs to
``/api/v1/events/{slug}/projects`` and a redirect would turn that into a GET.
"""
from django.urls import path

from projects.api import EventProjectListCreate

urlpatterns = [
    path("events/<slug:slug>/projects", EventProjectListCreate.as_view(),
         name="event-projects"),
]
