"""Projects, revisions, images and answers.

Server-rendered, read-only views. Every write goes through the JSON API.
"""
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import urlencode

from core.clock import now
from events.models import CustomQuestion, Track
from events.policy import submission_window_open, visible_events
from projects.models import ACTIVE_STATUSES
from projects.policy import (SORT_NEWEST, SORT_TITLE, SORTS, filter_gallery, gallery_facets,
                             public_projects, visible_answers, visible_images, visible_project_by_id)
from teams.policy import team_members, team_of

PAGE_SIZE = 48  # the checker reads page one, so the newest titles must not hide there


def _chosen_sort(request) -> str:
    sort = (request.GET.get("sort") or "").strip()
    return sort if sort in SORTS else SORT_TITLE


def _gallery(request, event=None):
    """One gallery, scoped to an event or not.

    Everything the page needs is three queries: the filter facets, the count the
    paginator needs and the page itself. The relations on a card (event, team,
    track) are select_related by the policy queryset, so 48 cards stay 48 rows in
    one round trip.
    """
    sort = _chosen_sort(request)
    projects = filter_gallery(
        public_projects(event),
        q=request.GET.get("q", ""), track=request.GET.get("track", ""),
        tag=request.GET.get("tag", ""), sort=sort,
    )
    page = Paginator(projects, PAGE_SIZE).get_page(request.GET.get("page"))
    event_options, track_options = gallery_facets(event)
    query = {
        "q": (request.GET.get("q") or "").strip(),
        "track": (request.GET.get("track") or "").strip(),
        "tag": (request.GET.get("tag") or "").strip(),
        "sort": sort,
        # On the unscoped route the event lives in the query string, so the
        # pagination links have to carry it; on the event route the path does.
        "event": event.slug if event is not None and request.path == "/projects" else "",
    }
    return render(request, "projects/gallery.html", {
        "page_obj": page,
        "projects": page.object_list,
        "result_count": page.paginator.count,
        "events": event_options,
        "tracks": track_options,
        "event": event,
        "sort": sort,
        "sort_newest": SORT_NEWEST,
        "querystring": urlencode({key: value for key, value in query.items() if value}),
        **query,
    })


def gallery(request):
    """The public gallery at /projects: submitted projects, searchable and filterable.

    Read-only and anonymous-friendly on purpose (BUILD-SEC section 3).
    """
    slug = (request.GET.get("event") or "").strip()
    event = None
    if slug:
        event = get_object_or_404(visible_events(), slug=slug)
    return _gallery(request, event)


def event_gallery(request, slug: str):
    """The same gallery scoped to one event, reached from the event page."""
    return _gallery(request, get_object_or_404(visible_events(), slug=slug))


def project_detail(request, slug: str, public_id: str):
    """/events/{slug}/projects/{id}: the public face of one project."""
    event = get_object_or_404(visible_events(), slug=slug)
    project = visible_project_by_id(request.user, event, public_id)
    if project is None:
        # A draft answers 404 here, not 403: the page must not confirm it exists.
        raise Http404("No such project.")
    context = {
        "event": event,
        "project": project,
        "team_members": list(team_members(project.team)),
        "images": list(visible_images(project)),
        "answers": list(visible_answers(request.user, project)),
        "breadcrumbs": [
            {"label": "Gallery", "url": "/projects"},
            {"label": event.name, "url": f"/events/{event.slug}/"},
            {"label": project.title},
        ],
    }
    return render(request, "projects/detail.html", context)


def _login_redirect(request):
    return redirect(f"/login?{urlencode({'next': request.get_full_path()})}")


def submission_editor(request, slug: str):
    """/events/{slug}/submission: the one page that writes a project.

    Read-only like every other page: the form posts to the project endpoints
    through api-forms.js, and the checklist below is refreshed by
    static/js/submission-editor.js as the writer types.
    """
    if not request.user.is_authenticated:
        return _login_redirect(request)
    event = get_object_or_404(visible_events(), slug=slug)
    team = team_of(request.user, event)
    project = None
    if team is not None:
        project = (team.projects.filter(status__in=ACTIVE_STATUSES)
                   .select_related("track").order_by("-id").first())
    answers = {}
    images = []
    revisions = []
    if project is not None:
        answers = {answer.question.public_id: answer.value
                   for answer in project.answers.all()}
        images = list(visible_images(project))
        revisions = list(project.revisions.all())
    questions = list(CustomQuestion.objects.filter(event=event, is_active=True))
    # The template gets plain rows rather than a lookup filter: one dict per
    # question carries the value next to the control that shows it.
    question_rows = [
        {
            "public_id": question.public_id,
            "prompt": question.prompt,
            "help_text": question.help_text,
            "kind": question.kind,
            "choices": question.choices,
            "required": question.required,
            "is_public": question.is_public,
            "value": answers.get(question.public_id, ""),
        }
        for question in questions
    ]
    return render(request, "projects/editor.html", {
        "event": event,
        "team": team,
        "project": project,
        "tracks": list(Track.objects.filter(event=event)),
        "question_rows": question_rows,
        "images": images,
        "revisions": revisions,
        "window_open": submission_window_open(event),
        "now": now(),
        "breadcrumbs": [
            {"label": event.name, "url": f"/events/{event.slug}/"},
            {"label": "Submission"},
        ],
    })
