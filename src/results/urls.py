"""Results HTML routes."""
from django.urls import path

from results.views import results_public

urlpatterns = [
    path("events/<slug:slug>/results", results_public, name="results-page"),
]
