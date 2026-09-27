"""Root URL configuration: every app's HTML urls at "", every api_urls under /api/v1/."""
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerSplitView

from accounts.api_urls import urlpatterns as accounts_api
from accounts.urls import urlpatterns as accounts_html
from audit.api_urls import urlpatterns as audit_api
from audit.urls import urlpatterns as audit_html
from community.api_urls import urlpatterns as community_api
from community.urls import urlpatterns as community_html
from core.urls import urlpatterns as core_html
from events.api_urls import urlpatterns as events_api
from events.urls import urlpatterns as events_html
from interop.api_urls import urlpatterns as interop_api
from interop.urls import urlpatterns as interop_html
from judging.api_urls import urlpatterns as judging_api
from judging.urls import urlpatterns as judging_html
from projects.api_urls import urlpatterns as projects_api
from projects.urls import urlpatterns as projects_html
from results.api_urls import urlpatterns as results_api
from results.urls import urlpatterns as results_html
from teams.api_urls import urlpatterns as teams_api
from teams.urls import urlpatterns as teams_html


def api_root(request):
    return JsonResponse({"name": "VERDICT API", "version": "1.0.0"})


urlpatterns = [
    path("api/v1/", api_root, name="api-root"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    # Split view: the Swagger bootstrap is a separate file, so the page needs no
    # inline <script> and stays within our Content-Security-Policy.
    path("api/docs/", SpectacularSwaggerSplitView.as_view(url_name="schema"), name="api-docs"),
    path("api/v1/", include(events_api)),
    path("api/v1/", include(teams_api)),
    path("api/v1/", include(projects_api)),
    path("api/v1/", include(judging_api)),
    path("api/v1/", include(results_api)),
    path("api/v1/", include(accounts_api)),
    path("api/v1/", include(audit_api)),
    path("api/v1/", include(interop_api)),
    path("api/v1/", include(community_api)),
    path("", include(core_html)),
    path("", include(events_html)),
    path("", include(teams_html)),
    path("", include(projects_html)),
    path("", include(judging_html)),
    path("", include(results_html)),
    path("", include(accounts_html)),
    path("", include(audit_html)),
    path("", include(interop_html)),
    path("", include(community_html)),
]

# The layout stays the same on an error page, so the brand and the nav survive a
# 404 and a reader can navigate away instead of hitting back.
handler403 = "core.views.error_403"
handler404 = "core.views.error_404"
handler500 = "core.views.error_500"
