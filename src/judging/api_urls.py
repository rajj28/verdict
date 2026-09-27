"""Rubric, judges, assignments, reviews and scores.

JSON API routes under /api/v1/.
"""
from django.urls import path

from judging.api import (
    AcceptJudgeInviteView, AutoAssignmentsView, EventAssignmentDetailView, EventAssignmentsView,
    EventConflictsView, EventJudgeDetailView, EventJudgesView, EventProgressView, EventRubricView,
    JudgeAssignmentsView, JudgeComparisonView, JudgeConflictView, JudgeInvitesView, JudgeReviewView,
    JudgeScoresView, ReviewExclusionView, SubmitJudgeReviewView, EventJudgeScoresView,
    NextPairView,
)

urlpatterns = [
    path("judge/scores", JudgeScoresView.as_view(), name="judge-scores"),
    path("events/<slug:slug>/judges/<str:judge_id>/scores", EventJudgeScoresView.as_view(),
         name="event-judge-scores"),
    path("events/<slug:slug>/rubric", EventRubricView.as_view(), name="judging-rubric"),
    path("events/<slug:slug>/judges", EventJudgesView.as_view(), name="judges"),
    path("events/<slug:slug>/judges/<str:judge_id>", EventJudgeDetailView.as_view(), name="judge-detail"),
    path("events/<slug:slug>/judge-invites", JudgeInvitesView.as_view(), name="judge-invites"),
    path("judge-invites/<str:token>/accept", AcceptJudgeInviteView.as_view(), name="judge-invite-accept"),
    path("events/<slug:slug>/conflicts", EventConflictsView.as_view(), name="conflicts"),
    path("events/<slug:slug>/judge/conflicts", JudgeConflictView.as_view(), name="judge-conflict"),
    path("events/<slug:slug>/assignments", EventAssignmentsView.as_view(), name="event-assignments"),
    path("events/<slug:slug>/assignments/auto", AutoAssignmentsView.as_view(), name="auto-assignments"),
    path("events/<slug:slug>/assignments/<str:assignment_id>", EventAssignmentDetailView.as_view(),
         name="assignment-detail"),
    path("judge/assignments", JudgeAssignmentsView.as_view(), name="judge-assignments"),
    path("events/<slug:slug>/judge/reviews/<str:prj_id>", JudgeReviewView.as_view(), name="judge-review"),
    path("events/<slug:slug>/judge/reviews/<str:prj_id>/submit", SubmitJudgeReviewView.as_view(),
         name="judge-review-submit"),
    path("events/<slug:slug>/judge/pairs/next", NextPairView.as_view(), name="judge-pair-next"),
    path("events/<slug:slug>/judge/comparisons", JudgeComparisonView.as_view(),
         name="judge-comparisons"),
    path("events/<slug:slug>/progress", EventProgressView.as_view(), name="event-progress"),
    path("events/<slug:slug>/reviews/<str:review_id>/exclusion", ReviewExclusionView.as_view(),
         name="review-exclusion"),
]
