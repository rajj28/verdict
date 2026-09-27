"""Teams, membership and invite links.

JSON API routes under /api/v1/. No trailing slashes (BUILD-SEC section 16).
"""
from django.urls import path

from teams import api

urlpatterns = [
    path("events/<slug:slug>/teams", api.TeamListCreate.as_view(), name="api-event-teams"),
    path("events/<slug:slug>/teams/<str:public_id>", api.TeamDetail.as_view(),
         name="api-event-team-detail"),
    path("events/<slug:slug>/teams/<str:public_id>/invite", api.TeamInviteView.as_view(),
         name="api-event-team-invite"),
    path("events/<slug:slug>/teams/<str:public_id>/leave", api.TeamLeaveView.as_view(),
         name="api-event-team-leave"),
    path("events/<slug:slug>/teams/<str:public_id>/members/<str:user_public_id>",
         api.TeamMemberDetailView.as_view(), name="api-event-team-member"),
    path("invites/<str:token>/accept", api.InviteAcceptView.as_view(), name="api-invite-accept"),
]
