"""Events, tracks, prizes, custom questions and per-event roles.

Server-rendered, read-only views. Every write goes through the JSON API.
"""
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render

from audit.models import AuditEvent
from events.models import Event, EventRole, Role
from events.policy import can_manage, judging_window_open, role_of, submission_window_open, visible_events
from judging.models import ReviewStatus
from projects.models import ProjectStatus


def _event_queryset(user):
    return visible_events(user).prefetch_related("tracks", "prizes", "questions", "result_publications")


def _phase(event: Event) -> str:
    if event.result_publications.exists():
        return "results published"
    if submission_window_open(event):
        return "submissions open"
    if judging_window_open(event):
        return "judging"
    return "upcoming"


def _phase_class(phase: str) -> str:
    return {
        "results published": "status-done",
        "submissions open": "status-open",
        "judging": "status-in-progress",
        "upcoming": "status-not-started",
    }[phase]


def event_list(request):
    events = _event_queryset(request.user).order_by("name", "slug")
    rows = [{"event": event, "phase": _phase(event), "phase_class": _phase_class(_phase(event))}
            for event in events]
    return render(request, "events/list.html", {"event_rows": rows})


def event_detail(request, slug: str):
    event = get_object_or_404(_event_queryset(request.user), slug=slug)
    role = role_of(request.user, event)
    return render(request, "events/detail.html", {
        "event": event,
        "role": role,
        "submission_window_open": submission_window_open(event),
        "has_results": event.result_publications.exists(),
    })


def event_new(request):
    if not request.user.is_authenticated or (not request.user.is_admin and not request.user.is_host):
        raise PermissionDenied
    return render(request, "events/new.html")


def _managed_event(request, slug: str) -> Event:
    event = get_object_or_404(_event_queryset(request.user), slug=slug)
    if not can_manage(request.user, event):
        raise PermissionDenied
    return event


def _overview_context(event: Event) -> dict:
    submitted = event.projects.filter(status=ProjectStatus.SUBMITTED)
    submitted_reviews = event.reviews.filter(status=ReviewStatus.SUBMITTED)
    under_reviewed = submitted.annotate(review_count=Count("reviews", filter=Q(reviews__status=ReviewStatus.SUBMITTED))).filter(
        review_count__lt=event.reviews_per_project
    )
    import_events = AuditEvent.objects.filter(event=event, action="import.completed")
    checklist = [
        ("Event configured", bool(event.name and event.submissions_close_at)),
        ("Tracks", event.tracks.exists()),
        ("Rubric", hasattr(event, "rubric") and event.rubric.criteria.exists()),
        ("Judges invited", event.roles.filter(role=Role.JUDGE).exists()),
        ("Assignments", event.assignments.exists()),
        ("Judging progress", submitted_reviews.exists()),
        ("Results published", event.result_publications.exists()),
    ]
    return {
        "checklist": checklist,
        "stats": {
            "participants": event.roles.filter(role=Role.PARTICIPANT).count(),
            "teams": event.teams.count(),
            "submitted_projects": submitted.count(),
            "drafts": event.projects.filter(status=ProjectStatus.DRAFT).count(),
            "judges": event.roles.filter(role=Role.JUDGE).count(),
            "reviews_submitted": submitted_reviews.count(),
        },
        "issues": {
            "superseded": event.projects.filter(status=ProjectStatus.SUPERSEDED).select_related("team", "track")[:10],
            "under_reviewed": under_reviewed.select_related("team", "track")[:10],
            "import_reports": import_events[:3],
        },
    }


def manage_overview(request, slug: str):
    event = _managed_event(request, slug)
    context = {"event": event, "submission_window_open": submission_window_open(event)}
    context.update(_overview_context(event))
    return render(request, "manage/overview.html", context)


def manage_settings(request, slug: str):
    event = _managed_event(request, slug)
    return render(request, "manage/settings.html", {
        "event": event,
        "submission_window_open": submission_window_open(event),
        "scoring_locked": event.scoring_locked_at is not None,
    })


def manage_setup(request, slug: str):
    event = _managed_event(request, slug)
    return render(request, "manage/setup.html", {
        "event": event,
        "tracks": event.tracks.all(),
        "prizes": event.prizes.select_related("track"),
        "questions": event.questions.all(),
    })
