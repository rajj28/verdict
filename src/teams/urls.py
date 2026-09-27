"""Teams, membership and invite links.

HTML routes (server-rendered pages). No trailing slashes.
"""
from django.urls import path

from teams import views

urlpatterns = [
    path("events/<slug:slug>/team", views.team_page, name="team-page"),
    # The token is a query parameter, never a path segment: the gunicorn access log
    # format records %(U)s, so a path token would be written to disk (section 16).
    path("invite", views.invite_page, name="invite"),
]
