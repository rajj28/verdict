"""Organizer judging and results pages, plus the public results projection.

Read-only like every other HTML view (BUILD-SPEC section 2): the rubric editor,
batch assignment, review exclusions, publication and verification all post to the
JSON API through api-forms.js or the two small page scripts, so every rule stays
in the service layer and the page is never the control.
"""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from core.errors import ApiError
from events.models import Event, EventRole, Role, Track
from events.policy import can_manage, judging_window_open, visible_events
from interop.exports import EXPORT_KINDS
from judging import policy as judging_policy
from judging.models import JudgeInvite
from projects.models import Project, ProjectStatus
from results import engine, services
from results.policy import get_publication, latest_publication, visible_publications
from teams.models import Team

#: One line per CSV so an organizer knows what they get before downloading.
EXPORT_DESCRIPTIONS = {
    "participants": "Every account holding a role in the event, with display names and tracks.",
    "teams": "Teams, their members and how many projects each has submitted.",
    "projects": "Submitted projects with track, team, links, tags and revision number.",
    "judges": "Judge roles with their tracks and their declared conflicts.",
    "assignments": "Which judge reviews which project, and the batch that created it.",
    "reviews": "One row per review: criterion values, weighted score, status and comment.",
    "progress": "Coverage per judge, per project and per track as of the export.",
    "results": "The official ranking, raw and normalized scores, and the allocated prizes.",
    "audit": "The append-only log of every state change, with actor and timestamp.",
}

METHOD_LABELS = {
    "normalized": "Normalized (additive judge offsets with shrinkage)",
    "raw": "Raw mean of review scores",
    "pairwise": "Pairwise Bradley-Terry strength",
}

STATUS_BADGES = {
    "ranked": ("Ranked", "status-submitted"),
    "unranked_no_reviews": ("Unranked, no reviews", "status-not-started"),
    "withdrawn": ("Withdrawn", "status-withdrawn"),
    "disqualified": ("Disqualified", "status-disqualified"),
    "superseded": ("Superseded", "status-superseded"),
}

JUDGE_STATUS_BADGES = {
    "not started": ("Not started", "status-not-started"),
    "in progress": ("In progress", "status-in-progress"),
    "done": ("Done", "status-done"),
    "no assignments": ("No assignments", "status-closed"),
}


def _managed_event(request, slug: str) -> Event:
    """The event an organizer page is about, or a refusal.

    The check is events.policy.can_manage, the same predicate every API class
    uses, so a judge, a participant and an organizer of a different event all get
    403 here instead of an empty page. Anonymous readers never reach it: every
    page is login_required, so they are sent to the login form instead.
    """
    event = get_object_or_404(visible_events(), slug=slug)
    if not can_manage(request.user, event):
        raise PermissionDenied("Only organizers of this event can open this page.")
    return event


def _submitted_projects(event: Event):
    """Submitted projects of the event, the only ones the assignment service accepts.

    projects.policy.public_projects also requires a public gallery, which an
    organizer may have switched off; assigning judges is an organizer action and
    must not depend on that setting.
    """
    return (
        Project.objects.filter(event=event, status=ProjectStatus.SUBMITTED)
        .select_related("team", "track")
        .order_by("title", "public_id")
    )


def _rank_number(rank) -> int | None:
    """'3' or '=3' as 3, None when the row is unranked."""
    if rank in (None, "", "unranked"):
        return None
    try:
        return int(str(rank).lstrip("="))
    except ValueError:
        return None


def _badge(pairs: dict, key: str, fallback_label: str = "Unknown") -> tuple[str, str]:
    return pairs.get(key, (fallback_label, "status-closed"))


# ---------------------------------------------------------------------------
# Rubric
# ---------------------------------------------------------------------------

def _criterion_rows(criteria) -> list[dict]:
    """Rubric criteria with their share of the total weight.

    The share is computed here so the markup never does presentation
    arithmetic and the organizer sees "25.0%" instead of a raw weight.
    """
    total = sum((criterion.weight for criterion in criteria), Decimal("0"))
    rows = []
    for criterion in criteria:
        rows.append({
            "criterion": criterion,
            "key": criterion.key,
            "name": criterion.name,
            "description": criterion.description,
            "weight": criterion.weight,
            "weight_percent": (round(100 * float(criterion.weight) / float(total), 1)
                               if total else 0.0),
            "min_score": criterion.min_score,
            "max_score": criterion.max_score,
        })
    return rows


@login_required
def manage_rubric(request, slug: str):
    """/manage/{slug}/rubric: the criteria editor and the locked-state explanation."""
    event = _managed_event(request, slug)
    criteria = judging_policy.rubric_criteria(event)
    return render(request, "manage/rubric.html", {
        "event": event,
        "criterion_rows": _criterion_rows(criteria),
        "rubric_version": criteria[0].rubric.version if criteria else 0,
        "rubric_api": f"/api/v1/events/{event.slug}/rubric",
    })


# ---------------------------------------------------------------------------
# Judges
# ---------------------------------------------------------------------------

def _judge_rows(progress: dict) -> list[dict]:
    """progress() judge rows with the label and badge the table needs."""
    rows = []
    for row in progress["judges"]:
        label, badge = _badge(JUDGE_STATUS_BADGES, row["status"], row["status"].title())
        rows.append({**row, "status_label": label, "status_badge": badge})
    return rows


@login_required
def manage_judges(request, slug: str):
    """/manage/{slug}/judges: the judge directory, invitations and conflicts."""
    event = _managed_event(request, slug)
    progress = judging_policy.progress(event)
    invites = (
        JudgeInvite.objects.filter(event=event)
        .prefetch_related("tracks")
        .order_by("-created_at")[:25]
    )
    return render(request, "manage/judges.html", {
        "event": event,
        "judge_rows": _judge_rows(progress),
        "tracks": list(Track.objects.filter(event=event).order_by("position", "id")),
        "teams": list(Team.objects.filter(event=event).order_by("name", "id")),
        "conflicts": list(judging_policy.visible_conflicts(request.user, event)),
        "invites": invites,
        "judges_api": f"/api/v1/events/{event.slug}/judges",
        "invites_api": f"/api/v1/events/{event.slug}/judge-invites",
        "conflicts_api": f"/api/v1/events/{event.slug}/conflicts",
    })


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

@login_required
def manage_assignments(request, slug: str):
    """/manage/{slug}/assignments: batch assign, auto-assign preview and coverage.

    Filters live in the query string so a filtered table is a link an organizer
    can send to a co-organizer, and the auto-assign form keeps the same filters.
    """
    event = _managed_event(request, slug)
    judge_filter = (request.GET.get("judge") or "").strip()
    track_filter = (request.GET.get("track") or "").strip()
    project_filter = (request.GET.get("project") or "").strip()

    projects = _submitted_projects(event)
    if track_filter:
        projects = projects.filter(track__public_id=track_filter)

    assignments = judging_policy.visible_assignments(
        request.user, event, judge_public_id=judge_filter or None
    ).prefetch_related("review")
    if track_filter:
        assignments = assignments.filter(project__track__public_id=track_filter)
    if project_filter:
        assignments = assignments.filter(project__public_id=project_filter)

    progress = judging_policy.progress(event)
    track_names = {track.public_id: track.name
                   for track in Track.objects.filter(event=event)}
    coverage = [
        {**row, "track_name": track_names.get(row["track"], "")}
        for row in progress["projects"]
    ]
    judge_options = [
        {
            "public_id": judge.public_id,
            "name": judge.user.display_name or judge.user.email.split("@")[0],
            "tracks": sorted(track.name for track in judge.tracks.all()),
        }
        for judge in judging_policy.visible_judges(request.user, event)
    ]
    assignment_rows = list(assignments)
    return render(request, "manage/assignments.html", {
        "event": event,
        "judges": judge_options,
        "projects": list(projects),
        "assignments": assignment_rows,
        "assignment_count": len(assignment_rows),
        "coverage": coverage,
        "tracks": list(Track.objects.filter(event=event).order_by("position", "id")),
        "judge_filter": judge_filter,
        "track_filter": track_filter,
        "project_filter": project_filter,
        "assignments_api": f"/api/v1/events/{event.slug}/assignments",
        "auto_api": f"/api/v1/events/{event.slug}/assignments/auto",
        "fill_gaps": (request.GET.get("fill") or "") == "gaps",
    })


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

@login_required
def manage_progress(request, slug: str):
    """/manage/{slug}/progress: the live dashboard, refreshed by progress.js.

    The page is server-rendered and progress.js patches the numbers it receives
    from the progress API every 15 seconds, so the page is useful with scripting
    off and live with scripting on.
    """
    event = _managed_event(request, slug)
    data = judging_policy.progress(event)
    track_names = {track.public_id: track.name for track in Track.objects.filter(event=event)}
    project_rows = [
        {**row, "track_name": track_names.get(row["track"], "")}
        for row in data["projects"]
    ]
    projects = project_rows
    submitted = sum(row["submitted"] for row in projects)
    target = sum(row["target"] for row in projects)
    done_judges = sum(1 for row in data["judges"] if row["status"] == "done")
    return render(request, "manage/progress.html", {
        "event": event,
        "progress": {**data, "projects": project_rows},
        "judge_rows": _judge_rows(data),
        "progress_api": f"/api/v1/events/{event.slug}/progress",
        "judging_open": judging_window_open(event),
        "totals": {
            "judges": len(data["judges"]),
            "judges_done": done_judges,
            "projects": len(projects),
            "submitted": submitted,
            "target": target,
            "coverage_percent": round(100 * submitted / target, 1) if target else 100.0,
            "under_covered": sum(1 for row in projects if row["under_covered"]),
        },
    })


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

def _judge_names(event: Event) -> dict[str, str]:
    """Judge public_id -> display name, for the organizer-only tables."""
    return {
        role.public_id: (role.user.display_name or role.user.email.split("@")[0])
        for role in EventRole.objects.filter(event=event, role=Role.JUDGE)
        .select_related("user")
    }


def _ranking_rows(preview: dict) -> list[dict]:
    """Preview rows with the rank movement and the status badge resolved."""
    rows = []
    for row in preview["rows"]:
        raw_rank = _rank_number(row.get("rank_raw"))
        norm_rank = _rank_number(row.get("rank_norm"))
        delta = (raw_rank - norm_rank) if raw_rank is not None and norm_rank is not None else None
        label, badge = _badge(STATUS_BADGES, row.get("status"), row.get("status", ""))
        rows.append({
            **row,
            "delta": delta,
            "delta_class": ("delta-up" if delta and delta > 0 else
                            "delta-down" if delta and delta < 0 else ""),
            "delta_text": ("" if not delta else
                           (f"+{delta}" if delta > 0 else str(delta))),
            "status_label": label,
            "status_badge": badge,
            "rank_bt": row.get("rank_bt") or "",
        })
    return rows


def _judge_table_rows(preview: dict, names: dict[str, str]) -> list[dict]:
    """The judge table with the constant-scorer and single-review warnings attached."""
    diagnostics = preview.get("diagnostics", {})
    constant = set(diagnostics.get("constant_scorers") or [])
    single = set(diagnostics.get("single_review_judges") or [])
    rows = []
    for row in preview.get("judge_rows", []):
        judge_id = row["judge_id"]
        warnings = []
        if judge_id in constant:
            warnings.append(
                "Constant scorer: every criterion value identical across this judge's reviews. "
                "Kept, because the offset absorbs the level; excluded reviews would need a reason.")
        if judge_id in single:
            warnings.append("Single review: the offset is weakly estimated for this judge.")
        rows.append({**row, "name": names.get(judge_id, judge_id), "warnings": warnings})
    return rows


def _explanation(event: Event, project: Project, preview: dict, names: dict[str, str]) -> list[dict]:
    """Per-review breakdown for one project: s_r, the judge offset b_j and s_r − b_j.

    The scores come from the pure engine and the offsets from the same preview
    the Ranking tab shows, so the explanation can never disagree with it.
    """
    criteria = judging_policy.engine_criteria(event)
    if not criteria:
        return []
    offsets = {row["judge_id"]: row["offset"] for row in preview.get("judge_rows", [])}
    rows = []
    for review in judging_policy.included_reviews(event).filter(project=project):
        values = judging_policy.review_values(review)
        if any(criterion.key not in values for criterion in criteria):
            continue
        score = engine.review_score(values, criteria)
        offset = float(offsets.get(review.judge.public_id, 0.0))
        rows.append({
            "judge_id": review.judge.public_id,
            "name": names.get(review.judge.public_id, review.judge.public_id),
            "score": round(score, 2),
            "offset": round(offset, 2),
            "adjusted": round(score - offset, 2),
            "comment": review.comment,
        })
    rows.sort(key=lambda row: (-row["adjusted"], row["name"]))
    return rows


def _certificate(event: Event, preview: dict | None, inputs: dict) -> dict | None:
    """The winner-robustness certificate as plain words.

    The certificate is part of the preview payload, so that is where it is read
    from. Until the preview carries it, the page computes it from the very same
    canonical inputs with the pure engine, which keeps this page from being one
    step behind the engine that computed the ranking.
    """
    if not preview:
        return None
    for key in ("robustness", "certificate"):
        value = preview.get(key)
        if isinstance(value, dict) and value:
            return value
    lam = preview.get("lam")
    criteria = judging_policy.engine_criteria(event)
    included = inputs["included"]
    if not criteria or not included or lam is None or lam == "auto":
        return None
    reviews = [
        engine.ReviewInput(review_id=row["review_id"], judge_id=row["judge_id"],
                           project_id=row["project_id"], values=row["criteria"])
        for row in included
    ]
    return asdict(engine.robustness(reviews, criteria, lam=float(lam)))


@login_required
def manage_results(request, slug: str):
    """/manage/{slug}/results: preview, diagnostics, explanation and publication."""
    event = _managed_event(request, slug)
    preview = None
    preview_error = ""
    try:
        preview = services.preview(event)
    except ApiError as error:
        # A missing rubric or an event with nothing to rank is a state an
        # organizer fixes on another page, not a 500 on this one.
        preview_error = error.message
    inputs = services.collect_inputs(event)
    names = _judge_names(event)

    rows = _ranking_rows(preview) if preview else []
    unranked = [row for row in rows if row["status"] == "unranked_no_reviews"]
    titles = {row["project_id"]: row["title"] for row in rows}
    judge_rows = _judge_table_rows(preview, names) if preview else []

    selected = (request.GET.get("project") or "").strip()
    explanation = []
    explain_project = None
    if selected and preview:
        explain_project = _submitted_projects(event).filter(public_id=selected).first()
        if explain_project is not None:
            explanation = _explanation(event, explain_project, preview, names)

    under_reviewed = [
        {"project_id": project_id, "title": titles.get(project_id, project_id)}
        for project_id in ((preview.get("diagnostics", {}).get("under_reviewed") or [])
                           if preview else [])
    ]
    publications = visible_publications(request.user, event)
    return render(request, "manage/results.html", {
        "event": event,
        "issue_count": (len(under_reviewed) + len(inputs["excluded"])
                        + len(inputs["ineligible_projects"])),
        "preview": preview,
        "preview_error": preview_error,
        "method_label": METHOD_LABELS.get(event.ranking_method, event.ranking_method),
        "rows": rows,
        "judge_rows": judge_rows,
        "outliers": preview.get("outliers", []) if preview else [],
        "spread": preview.get("spread", {}) if preview else {},
        "certificate": _certificate(event, preview, inputs),
        "components": (preview.get("diagnostics", {}).get("n_components") if preview else None),
        "excluded_reviews": inputs["excluded"],
        "ineligible_projects": inputs["ineligible_projects"],
        "under_reviewed": under_reviewed,
        "unranked": unranked,
        "explanation": explanation,
        "explain_project": explain_project,
        "publications": publications,
        "latest_publication": publications[0] if publications else None,
        "judging_open": judging_window_open(event),
        "publish_api": f"/api/v1/events/{event.slug}/results/publish",
        "feedback_api": f"/api/v1/events/{event.slug}/feedback-release",
        "close_judging_api": f"/api/v1/events/{event.slug}/close-judging",
    })


@login_required
def decision_record(request, slug: str, pub_id: str):
    """/manage/{slug}/results/publications/{pub}: the decision record and Verify."""
    event = _managed_event(request, slug)
    try:
        publication = get_publication(event, pub_id)
    except ApiError as error:
        raise Http404(error.message) from error
    record = services.decision_record(publication)
    # The criterion values are stored as a dict per review; the table shows them
    # as one readable cell, built here so the markup stays plain.
    record["included_reviews"] = [
        {**row, "criteria_text": ", ".join(
            f"{key}={value}" for key, value in sorted((row.get("criteria") or {}).items())
        )}
        for row in record["included_reviews"]
    ]
    return render(request, "manage/decision_record.html", {
        "event": event,
        "publication": publication,
        "record": record,
        "verify_api": f"/api/v1/events/{event.slug}/results/publications/{pub_id}/verify",
    })


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

@login_required
def manage_exports(request, slug: str):
    """/manage/{slug}/exports: every CSV and the event.json round trip."""
    event = _managed_event(request, slug)
    return render(request, "manage/exports.html", {
        "event": event,
        "exports": [
            {
                "kind": kind,
                "description": EXPORT_DESCRIPTIONS.get(kind, ""),
                "url": f"/api/v1/events/{event.slug}/exports/{kind}.csv",
            }
            for kind in EXPORT_KINDS
        ],
        "json_url": f"/api/v1/events/{event.slug}/exports/event.json",
    })


# ---------------------------------------------------------------------------
# Public results
# ---------------------------------------------------------------------------

def results_public(request, slug: str):
    """/events/{slug}/results: the public projection of the latest publication.

    Everything on the page comes from the stored publication rows, which carry
    no judge identity, offset or comment (BUILD-SPEC sections 3 and 16).
    """
    event = get_object_or_404(visible_events(), slug=slug)
    publication = latest_publication(event)
    if publication is None:
        return render(request, "events/results_public.html", {"event": event})
    rows = publication.rows
    by_track: dict[str, list[dict]] = {}
    for row in rows:
        by_track.setdefault(row.get("track") or "", []).append(row)
    return render(request, "events/results_public.html", {
        "event": event,
        "publication": publication,
        "rows": rows,
        "ranked": [row for row in rows if row.get("status") == "ranked"],
        "unranked": [row for row in rows if row.get("status") != "ranked"],
        "by_track": [
            {"name": name, "rows": track_rows}
            for name, track_rows in sorted(by_track.items(), key=lambda item: item[0])
            if name
        ],
        "awards": publication.awards,
        "unawarded": publication.unawarded,
        "method_label": METHOD_LABELS.get(publication.method, publication.method),
    })
