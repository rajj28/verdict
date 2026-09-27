"""Projects, revisions, images and answers.

HTML routes (server-rendered pages).
"""
from django.urls import path

from projects.views import gallery

urlpatterns = [
    # Exact, no trailing slash: /projects is the checker's gallery route.
    path("projects", gallery, name="gallery"),
]
