"""Rubric, judges, assignments, reviews and scores.

Server-rendered, read-only views. Every write goes through the JSON API: the
invite button is a data-api-* form and the rubric page is driven by
static/js/judge-console.js, which posts to the same review endpoints.
"""
import json
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode

from core.clock import now
from core.errors import ApiError
from events.models import Event, EventRole
from events.policy import is_judge, judge_role, judging_window_open, visible_events
from judging import policy, services
from judging.models import JudgeInvite, ReviewStatus
from projects.models import Project, ProjectStatus
from projects.policy import visible_answers, visible_images

SUBMITTED = "submitted"
DRAFT = "draft"
NOT_STARTED = "not-started"
STATUS_BADGES = {
    SUBMITTED: ("Submitted", "status-submitted"),
    DRAFT: ("Draft saved", "status-draft"),
    NOT_STARTED: ("Not started", "status-not-started"),
}


def _login_redirect(request):
    return redirect(f"/login?{urlencode({'next': request.get_full_path()})}")


def _refuse(error: ApiError):
    """Turn a policy refusal into the matching HTML response.

    The page asks judging.policy for the project exactly as the API does, so an
    unassigned or out-of-track project is a 403 here for the same reason it is
    there, and an unknown project is a 404 rather than a confirmation.
    """
    if error.status_code == 404:
        raise Http404(error.message)
    raise PermissionDenied(error.message)


def _queue_row(row) -> dict:
    """One assignment as the queue and the prev/next links show it."""
    review = row.review if hasattr(row, "review") else None
    if review is not None and review.status == ReviewStatus.SUBMITTED:
        status = SUBMITTED
    elif review is not None:
        status = DRAFT
    else:
        status = NOT_STARTED
    label, badge = STATUS_BADGES[status]
    return {
        "assignment": row,
        "event": row.event,
        "project": row.project,
        "review": review,
        "status": status,
        "status_label": label,
        "status_badge": badge,
        "url": reverse("judge-review", args=[row.event.slug, row.project.public_id]),
    }


def _score_label(value: int, index: int, count: int) -> str:
    """The radio label. Only the ends of the scale are named; the rest are numbers."""
    if index == 0:
        return f"{value} · lowest"
    if index == count - 1:
        return f"{value} · highest"
    return str(value)


def _criterion_rows(criteria, scores: dict) -> list[dict]:
    """Rubric criteria with their share of the total weight and the chosen value.

    The share is computed here rather than in the template so the page can show
    "25%" without any presentation arithmetic in the markup.
    """
    total = sum((criterion.weight for criterion in criteria), Decimal(0))
    rows = []
    for position, criterion in enumerate(criteria, start=1):
        options = list(range(criterion.min_score, criterion.max_score + 1))
        rows.append({
            "criterion": criterion,
            "key": criterion.key,
            "name": criterion.name,
            "description": criterion.description,
            "weight": criterion.weight,
            "weight_percent": (round(100 * float(criterion.weight) / float(total), 1)
                               if total else 0.0),
            "value": scores.get(criterion.key),
            "shortcut": position if position <= 9 else None,
            "options": [
                {"value": value, "label": _score_label(value, index, len(options))}
                for index, value in enumerate(options)
            ],
        })
    return rows


def _mask(email: str) -> str:
    """Enough of an address to recognise it, never enough to harvest one."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def _invite_problem(invite: JudgeInvite | None, user) -> str | None:
    """Why this judge invitation cannot be used here, or None when it can.

    Read-side twin of judging.services.accept_judge_invite, so the page explains
    the same reasons before the judge presses the button.
    """
    if invite is None:
        return "That judge invitation is not valid. Ask the organizer for a new link."
    if invite.revoked_at is not None:
        return "This judge invitation was withdrawn by the organizer."
    if invite.accepted_at is not None:
        return "This judge invitation has already been used."
    if invite.expires_at <= now():
        return "This judge invitation has expired."
    if not user.is_authenticated:
        return None
    if user.email.casefold() != invite.email.casefold():
        return (f"This invitation was sent to {_mask(invite.email)}. Sign in with that "
                f"address to accept it.")
    if EventRole.objects.filter(event=invite.event, user=user).exists():
        return ("You already hold a role in this event. One person holds one role per "
                "event, so a judge cannot also compete in or run the event they judge.")
    return None


def judge_console(request):
    """/judge: the caller's own queue, grouped by event, with progress.

    Everything on the page comes from policy.visible_assignments, which is the
    caller's own assignments and nothing else: no other judge's queue, name or
    score can reach this page (BUILD-SPEC section 3).
    """
    if not request.user.is_authenticated:
        return _login_redirect(request)
    if not is_judge(request.user):
        raise PermissionDenied("The judge console is for judges of at least one event.")
    rows = policy.visible_assignments(request.user).prefetch_related(
        "review__scores__criterion", "project__track", "project__team"
    )
    groups = []
    for row in rows:
        if not groups or groups[-1]["event"].pk != row.event.pk:
            groups.append({
                "event": row.event,
                "rows": [],
                "assigned": 0,
                "submitted": 0,
                # The first assignment that is not a submitted review is where the
                # judge left off, so "continue" is never a guess.
                "continue": None,
            })
        group = groups[-1]
        entry = _queue_row(row)
        group["rows"].append(entry)
        group["assigned"] += 1
        if entry["status"] == SUBMITTED:
            group["submitted"] += 1
        elif group["continue"] is None:
            group["continue"] = entry
    for group in groups:
        group["percent"] = (round(100 * group["submitted"] / group["assigned"])
                            if group["assigned"] else 0)
        group["window_open"] = judging_window_open(group["event"])
    return render(request, "judge/queue.html", {"groups": groups})


def judge_review(request, slug: str, public_id: str):
    """/judge/{slug}/review/{prj}: project evidence left, rubric right.

    Read-only like every page. The radios autosave through judge-console.js and
    the two buttons post to the same review endpoints through api-forms.js, so
    the write path stays the JSON API and the rules stay in the service.
    """
    if not request.user.is_authenticated:
        return _login_redirect(request)
    event: Event = get_object_or_404(visible_events(), slug=slug)
    try:
        project: Project = policy.judge_project(request.user, event, public_id)
    except ApiError as error:
        _refuse(error)
    criteria = policy.rubric_criteria(event)
    # The caller's own review only: a review never carries another judge's draft.
    review = policy.judge_reviews(request.user).filter(project=project).first()
    scores = {row.criterion.key: row.value for row in review.scores.all()} if review else {}
    queue = [_queue_row(row) for row in
             policy.visible_assignments(request.user, event).prefetch_related("review")]
    position = next((index for index, row in enumerate(queue)
                     if row["project"].pk == project.pk), None)
    window_open = judging_window_open(event)
    editable = (judge_role(request.user, event) is not None and window_open
                and project.status == ProjectStatus.SUBMITTED)
    return render(request, "judge/review.html", {
        "event": event,
        "project": project,
        "team": project.team,
        "track": project.track,
        "images": list(visible_images(project)),
        "answers": list(visible_answers(request.user, project)),
        "criterion_rows": _criterion_rows(criteria, scores),
        "review": review,
        "scores_json": json.dumps(scores, sort_keys=True),
        "comment": review.comment if review else "",
        "submitted": review is not None and review.status == ReviewStatus.SUBMITTED,
        "editable": editable,
        "window_open": window_open,
        "queue": queue,
        "position": position,
        "previous": queue[position - 1] if position else None,
        "next": (queue[position + 1]
                 if position is not None and position + 1 < len(queue) else None),
        "review_url": reverse("judge-review", args=[event.slug, project.public_id]),
        "draft_url": f"/api/v1/events/{event.slug}/judge/reviews/{project.public_id}",
        "submit_url": f"/api/v1/events/{event.slug}/judge/reviews/{project.public_id}/submit",
    })


def judge_pairwise(request, slug: str):
    """The comparison console only reads; verdicts and undo use JSON endpoints."""
    if not request.user.is_authenticated:
        return _login_redirect(request)
    event = get_object_or_404(visible_events(request.user), slug=slug)
    try:
        state = services.pairwise_state(request.user, event)
    except ApiError as error:
        _refuse(error)
    return render(request, "judge/pairwise.html", {"event": event, **state})


def command_center(request, slug: str):
    """/manage/{slug}/command-center: is judging going to finish in time?

    Read-only like every page: the table, the timeline bars and the proposal are
    rendered from the same payload the JSON API serves, and command-center.js
    re-reads that API every 30 s. The only write is the rebalance button, which
    posts through api-forms.js to the same endpoint.
    """
    if not request.user.is_authenticated:
        return _login_redirect(request)
    event: Event = get_object_or_404(visible_events(), slug=slug)
    try:
        payload = services.command_center(request.user, event)
    except ApiError as error:
        _refuse(error)
    return render(request, "manage/command_center.html", {
        "event": event,
        "command_center_json": json.dumps(payload, cls=DjangoJSONEncoder),
        "api_url": f"/api/v1/events/{event.slug}/command-center",
        "rebalance_url": f"/api/v1/events/{event.slug}/assignments/rebalance",
        "forecast": payload["forecast"],
        "proposal": payload["proposal"],
        "at_risk_count": payload["forecast"]["at_risk_count"],
        "reviews_left": sum(judge["remaining"] for judge in payload["forecast"]["judges"]),
        "judging_open": judging_window_open(event),
    })


def judge_invite(request):
    """/judge-invite?token=…: what the invitation offers, and the one button.

    The token arrives in the query string, never in a path, so the access log
    format (which records %(U)s only) never writes it to disk (BUILD-SPEC 16).
    """
    token = (request.GET.get("token") or "").strip()
    invite = (JudgeInvite.objects.filter(token=token).select_related("event")
              .prefetch_related("tracks").first() if token else None)
    event = invite.event if invite is not None else None
    problem = _invite_problem(invite, request.user)
    return render(request, "judge/invite.html", {
        "token": token,
        "invite": invite,
        "event": event,
        "invited_email": _mask(invite.email) if invite is not None else "",
        "tracks": invite.tracks.all() if invite is not None else [],
        "problem": problem,
        "signed_in": request.user.is_authenticated,
        "login_url": f"/login?{urlencode({'next': request.get_full_path()})}",
        "register_url": f"/register?{urlencode({'next': request.get_full_path()})}",
    })
