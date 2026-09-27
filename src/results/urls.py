"""Results and organizer judging HTML routes.

The public results page lives here because the publication is the only thing on
it; the /manage/ pages are the read-only faces of the judging and results APIs.
"""
from django.urls import path

from results import views

urlpatterns = [
    path("events/<slug:slug>/results", views.results_public, name="results-page"),
    path("manage/<slug:slug>/rubric", views.manage_rubric, name="manage-rubric"),
    path("manage/<slug:slug>/judges", views.manage_judges, name="manage-judges"),
    path("manage/<slug:slug>/assignments", views.manage_assignments, name="manage-assignments"),
    path("manage/<slug:slug>/progress", views.manage_progress, name="manage-progress"),
    path("manage/<slug:slug>/results", views.manage_results, name="manage-results"),
    # The decision record is a page of its own: the verification action needs a
    # stable, linkable place in the publication history.
    path("manage/<slug:slug>/results/publications/<str:pub_id>", views.decision_record,
         name="manage-decision-record"),
    path("manage/<slug:slug>/exports", views.manage_exports, name="manage-exports"),
]
