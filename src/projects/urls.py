"""Projects, revisions, images and answers.

HTML routes (server-rendered pages). No trailing slashes: /projects is the
acceptance checker's gallery route and must answer directly.
"""
from django.urls import path

from projects import views

urlpatterns = [
    # Exact, no trailing slash: /projects is the checker's gallery route.
    path("projects", views.gallery, name="gallery"),
    path("events/<slug:slug>/projects", views.event_gallery, name="event-projects"),
    path("events/<slug:slug>/projects/<str:public_id>", views.project_detail,
         name="project-detail"),
    path("events/<slug:slug>/submission", views.submission_editor, name="submission-editor"),
]
