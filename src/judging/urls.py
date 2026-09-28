"""Rubric, judges, assignments, reviews and scores.

HTML routes (server-rendered pages).
"""
from django.urls import path

from judging import views

urlpatterns = [
    path("judge", views.judge_console, name="judge-console"),
    path("judge/<slug:slug>/pairwise", views.judge_pairwise, name="judge-pairwise"),
    path("judge/<slug:slug>/review/<str:public_id>", views.judge_review, name="judge-review"),
    # The token is a query parameter, never a path segment: the gunicorn access log
    # format records %(U)s, so a path token would be written to disk (section 16).
    path("judge-invite", views.judge_invite, name="judge-invite"),
    path("manage/<slug:slug>/command-center", views.command_center, name="manage-command-center"),
]
