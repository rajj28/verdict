"""Events, tracks, prizes, custom questions and per-event roles.

JSON API routes under /api/v1/.
"""
from django.urls import path

from events import api

urlpatterns = [
    path("events", api.EventListCreate.as_view(), name="api-events"),
    path("events/<slug:slug>", api.EventDetail.as_view(), name="api-event-detail"),
    path("events/<slug:slug>/close-judging", api.CloseJudging.as_view(), name="api-event-close-judging"),
    path("events/<slug:slug>/close-submissions", api.CloseSubmissions.as_view(), name="api-event-close-submissions"),
    path("events/<slug:slug>/tracks", api.TrackListCreate.as_view(), name="api-event-tracks"),
    path("events/<slug:slug>/tracks/<str:public_id>", api.TrackDetail.as_view(), name="api-event-track-detail"),
    path("events/<slug:slug>/prizes", api.PrizeListCreate.as_view(), name="api-event-prizes"),
    path("events/<slug:slug>/prizes/<str:public_id>", api.PrizeDetail.as_view(), name="api-event-prize-detail"),
    path("events/<slug:slug>/questions", api.QuestionListCreate.as_view(), name="api-event-questions"),
    path("events/<slug:slug>/questions/<str:public_id>", api.QuestionDetail.as_view(), name="api-event-question-detail"),
    path("events/<slug:slug>/register", api.RegisterParticipant.as_view(), name="api-event-register"),
    path("events/<slug:slug>/organizers", api.OrganizerListCreate.as_view(), name="api-event-organizers"),
    path("events/<slug:slug>/organizers/<str:public_id>", api.OrganizerDetail.as_view(), name="api-event-organizer-detail"),
]
