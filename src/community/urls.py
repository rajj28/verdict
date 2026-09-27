"""Server-rendered pages for community voting."""
from django.urls import path

from community import views

urlpatterns = [
    path("events/<slug:slug>/vote", views.voting_page, name="community-vote"),
    path("events/<slug:slug>/vote/verify", views.email_verification_page,
         name="community-vote-verify"),
    path("events/<slug:slug>/voting/results", views.voting_results_page,
         name="community-voting-results-page"),
    path("manage/<slug:slug>/voting", views.voting_management_page,
         name="community-voting-manage-page"),
]
