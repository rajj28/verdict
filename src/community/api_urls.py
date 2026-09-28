"""JSON API routes for community voting and comments."""
from django.urls import path

from community.api import (
    BallotView,
    EmailVotingVerifyView,
    EventBallotDetailView,
    EmailVotingView,
    ModerateBallotView,
    ModerateCommentView,
    ProjectCommentsView,
    ResolveFlagView,
    VotesExportView,
    VotingConfigView,
    VotingManagementView,
    VotingResultsView,
)

urlpatterns = [
    path("events/<slug:slug>/votes/ballot", BallotView.as_view(), name="community-ballot"),
    path("events/<slug:slug>/votes/ballot/<str:public_id>",
         EventBallotDetailView.as_view(), name="community-ballot-detail"),
    path("events/<slug:slug>/votes/email", EmailVotingView.as_view(),
         name="community-vote-email"),
    path("events/<slug:slug>/votes/email/verify", EmailVotingVerifyView.as_view(),
         name="community-vote-email-verify"),
    path("events/<slug:slug>/voting/results", VotingResultsView.as_view(),
         name="community-voting-results"),
    path("events/<slug:slug>/voting/config", VotingConfigView.as_view(),
         name="community-voting-config"),
    path("events/<slug:slug>/voting/manage", VotingManagementView.as_view(),
         name="community-voting-manage"),
    path("events/<slug:slug>/voting/ballots/<str:public_id>/<str:action>",
         ModerateBallotView.as_view(), name="community-ballot-moderate"),
    path("events/<slug:slug>/voting/flags/<str:public_id>/resolve",
         ResolveFlagView.as_view(), name="community-flag-resolve"),
    path("events/<slug:slug>/exports/votes.csv", VotesExportView.as_view(),
         name="community-votes-export"),
    path("events/<slug:slug>/projects/<str:public_id>/comments",
         ProjectCommentsView.as_view(), name="community-project-comments"),
    path("events/<slug:slug>/comments/<str:public_id>/<str:action>",
         ModerateCommentView.as_view(), name="community-comment-moderate"),
]
