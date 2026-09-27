"""Projects, revisions, images and answers.

Server-rendered, read-only views. Every write goes through the JSON API.
"""
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render

from events.models import Track
from projects.policy import visible_projects

PAGE_SIZE = 48  # the checker reads page one, so the newest titles must not hide there


def gallery(request):
    """The public gallery: submitted projects, searchable and filterable by track.

    Read-only and anonymous-friendly on purpose (BUILD-SEC section 3): the whole
    queryset is one scoped query, so pagination and search never run per row.
    """
    query = (request.GET.get("q") or "").strip()
    track = (request.GET.get("track") or "").strip()
    projects = visible_projects()
    if query:
        projects = projects.filter(Q(title__icontains=query) | Q(summary__icontains=query))
    if track:
        projects = projects.filter(track__public_id=track)
    projects = projects.order_by("title", "id")
    page = Paginator(projects, PAGE_SIZE).get_page(request.GET.get("page"))
    tracks = (
        Track.objects.filter(event__gallery_public=True)
        .values_list("public_id", "name")
        .order_by("name", "public_id")
        .distinct()
    )
    return render(request, "projects/gallery.html", {
        "page_obj": page,
        "projects": page.object_list,
        "q": query,
        "track": track,
        "tracks": tracks,
        "result_count": page.paginator.count,
    })
