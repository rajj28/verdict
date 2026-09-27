"""Rubric, judges, assignments, reviews and scores.

JSON API routes under /api/v1/.
"""
from django.urls import path

from judging.api import EventJudgeScoresView, JudgeScoresView

urlpatterns = [
    path("judge/scores", JudgeScoresView.as_view(), name="judge-scores"),
    path("events/<slug:slug>/judges/<str:judge_id>/scores", EventJudgeScoresView.as_view(),
         name="event-judge-scores"),
]
