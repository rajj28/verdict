"""Infrastructure pages: the home page, error pages, healthcheck and media."""
import mimetypes
import posixpath
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.models import Count
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string

from core.clock import now
from core.policy import visible_probe_runs
from events.models import EventRole, Role
from events.policy import is_organizer, judging_window_open, submission_window_open, visible_events
from judging.models import Review, ReviewStatus
from projects.models import Project, ProjectStatus
from projects.policy import visible_projects


def _phase(event, at) -> tuple[str, str]:
    """(badge class, human label) for an event at this moment."""
    if submission_window_open(event, at):
        return "status-open", "Submissions open"
    if judging_window_open(event, at):
        return "status-judging", "Judging"
    return "status-closed", "Closed"


def _acceptance_report() -> dict | None:
    """The committed run.py output, shown verbatim on the landing page."""
    path = settings.REPO_DIR / "acceptance-report.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = [line for line in text.splitlines() if line.startswith(("T1", "T2", "T3", "T4"))]
    note = next((line for line in text.splitlines()
                 if line.startswith("note: claimed but not verified:")), "")
    unverified = note.split(":", 2)[-1].split() if note else []
    return {
        "checks": [{"text": line, "passed": line.rstrip().endswith("PASS")} for line in lines],
        "passed": sum(1 for line in lines if line.rstrip().endswith("PASS")),
        "total": len(lines),
        # Prefer run.py's closing "claimed ..., verified ..." line over the header.
        "summary": next((line for line in text.splitlines()
                         if line.startswith("claimed ") and ", verified " in line),
                        next((line for line in text.splitlines() if line.startswith("claimed")), "")),
        # run.py has no T3/T4 checks, so claiming them always leaves this note;
        # say where those tiers are verified instead of leaving it unexplained.
        "hand_checked": bool(unverified) and set(unverified) <= {"T3", "T4"},
    }


def home(request):
    """The front page: the pitch, real numbers from the database, live events.

    Six queries in total: four aggregates, one small card list and one grouped
    count, so the page costs the same on an empty install and on a seeded demo.
    """
    at = now()
    events = visible_events(request.user)
    stats = {
        "events": events.count(),
        "projects": visible_projects().count(),
        "judges": EventRole.objects.filter(role=Role.JUDGE, event__in=events).count(),
        "reviews": Review.objects.filter(event__in=events, status=ReviewStatus.SUBMITTED).count(),
    }
    card_events = list(events.order_by("submissions_close_at")[:6])
    submitted = {
        event_id: total
        for event_id, total in Project.objects.filter(
            event_id__in=[event.id for event in card_events], status=ProjectStatus.SUBMITTED
        ).values_list("event_id").annotate(total=Count("id"))
    }
    cards = [
        {"event": event, "phase": _phase(event, at), "projects": submitted.get(event.id, 0)}
        for event in card_events
    ]
    return render(request, "home.html", {"stats": stats, "cards": cards, "now": at,
                                         "report": _acceptance_report()})


def error_403(request, exception=None):
    return render(request, "errors/403.html", {"detail": _reason(exception)}, status=403)


def error_404(request, exception=None):
    return render(request, "errors/404.html", {"detail": _reason(exception)}, status=404)


def error_500(request):
    # Rendered without the request context on purpose: the most common cause of a
    # 500 here is the database, and a context processor that needs it would fail
    # again inside the error page.
    return HttpResponse(render_to_string("errors/500.html", {"PORTAL_NAME": "VERDICT"}), status=500)


def _reason(exception) -> str:
    text = str(exception) if exception else ""
    return text if len(text) <= 200 else ""


def healthz(request):
    """Container healthcheck: proves the app is up *and* the database answers."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"ok": True})


def media(request, path: str):
    """Serve an uploaded image, but only to someone allowed to see its project.

    Media lives outside the static tree and is never served directly: the owning
    project is resolved first and projects.policy.visible_project decides
    (BUILD-SEC section 16). Everyone else gets a plain 404, not a 403, so the
    response never confirms that the file exists.
    """
    from projects.policy import project_owning_media, visible_project

    name = posixpath.normpath(path)
    if name.startswith(("/", "..")):
        raise Http404("No such file.")
    project = project_owning_media(name)
    if project is None or visible_project(request.user, project) is None:
        raise Http404("No such file.")
    full_path = (Path(settings.MEDIA_ROOT) / name).resolve()
    if not full_path.is_file() or Path(settings.MEDIA_ROOT).resolve() not in full_path.parents:
        raise Http404("No such file.")
    content_type = mimetypes.guess_type(full_path.name)[0] or "application/octet-stream"
    return FileResponse(full_path.open("rb"), content_type=content_type)


def _integrity_report(request, event=None):
    """Show the most recent persisted run for this administrator or event."""
    if not request.user.is_authenticated:
        return HttpResponseForbidden()
    if event is not None and not is_organizer(request.user, event):
        return HttpResponseForbidden()
    if event is None and not getattr(request.user, "is_admin", False):
        return HttpResponseForbidden()
    latest = visible_probe_runs(request.user, event).first()
    cases = latest.data.get("cases", []) if latest else []
    endpoint = "/api/v1/integrity/probe"
    payload = {"cases": cases, "event": event, "has_report": latest is not None}
    return render(request, "core/integrity.html", {
        **payload,
        "endpoint": endpoint,
        "event_slug": event.slug if event else "",
        "report_summary": latest.summary if latest else "No probe has been run yet.",
        "extensions": ("voting", "publication"),
    })


def integrity_admin(request):
    """Platform administrators can view and run every probe case."""
    return _integrity_report(request)


def integrity_event(request, slug: str):
    """Event organizers see runs scoped to the event they manage."""
    event = visible_events(request.user).filter(slug=slug).first()
    if event is None:
        return HttpResponse(status=404)
    return _integrity_report(request, event)
