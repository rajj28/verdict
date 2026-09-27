"""Results JSON API routes under /api/v1/."""
from django.urls import path

from results.api import (
    FeedbackReleaseView,
    ProjectFeedbackView,
    PublicationDetailView,
    PublicationVerifyView,
    ResultsPreviewView,
    ResultsPublicView,
    ResultsPublishView,
)

urlpatterns = [
    # Preview (organizer) and public results
    path("events/<slug:slug>/results/preview", ResultsPreviewView.as_view(), name="results-preview"),
    path("events/<slug:slug>/results/publish", ResultsPublishView.as_view(), name="results-publish"),
    path("events/<slug:slug>/results", ResultsPublicView.as_view(), name="results-public"),
    # Decision record and verification
    path(
        "events/<slug:slug>/results/publications/<str:pub_id>",
        PublicationDetailView.as_view(),
        name="publication-detail",
    ),
    path(
        "events/<slug:slug>/results/publications/<str:pub_id>/verify",
        PublicationVerifyView.as_view(),
        name="publication-verify",
    ),
    # Feedback release toggle
    path("events/<slug:slug>/feedback-release", FeedbackReleaseView.as_view(), name="feedback-release"),
    # Per-project feedback endpoint (used by the project detail page)
    path(
        "events/<slug:slug>/projects/<str:project_id>/feedback",
        ProjectFeedbackView.as_view(),
        name="project-feedback",
    ),
]
