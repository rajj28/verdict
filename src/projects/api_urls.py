"""Projects, revisions, images and answers.

JSON API routes under /api/v1/.

No trailing slashes anywhere: the acceptance checker POSTs to
``/api/v1/events/{slug}/projects`` and a redirect would turn that into a GET.
"""
from django.urls import path

from projects import api

urlpatterns = [
    path("projects", api.PublicGallery.as_view(), name="api-gallery"),
    path("events/<slug:slug>/projects", api.EventProjectListCreate.as_view(),
         name="event-projects"),
    path("events/<slug:slug>/projects/<str:public_id>", api.ProjectDetail.as_view(),
         name="api-event-project-detail"),
    path("events/<slug:slug>/projects/<str:public_id>/submit", api.ProjectSubmit.as_view(),
         name="api-event-project-submit"),
    path("events/<slug:slug>/projects/<str:public_id>/withdraw", api.ProjectWithdraw.as_view(),
         name="api-event-project-withdraw"),
    path("events/<slug:slug>/projects/<str:public_id>/disqualify", api.ProjectDisqualify.as_view(),
         name="api-event-project-disqualify"),
    path("events/<slug:slug>/projects/<str:public_id>/images", api.ProjectImageCreate.as_view(),
         name="api-event-project-images"),
    path("events/<slug:slug>/projects/<str:public_id>/images/<int:position>",
         api.ProjectImageDetail.as_view(), name="api-event-project-image"),
    path("events/<slug:slug>/projects/<str:public_id>/answers", api.ProjectAnswers.as_view(),
         name="api-event-project-answers"),
]
