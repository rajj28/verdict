"""CSV exports for the organizer.

One stage of the judging pipeline per file, built with core.csvutil so a cell
that starts with =, +, -, @ or a tab is neutralised before a spreadsheet can
execute it. Scores come from results.engine, the one implementation of the
judging maths, so a CSV can never disagree with the published results.

Supported export kinds:
  participants, teams, projects, judges, assignments, reviews,
  progress, results, audit, event.json (JSON, not CSV)
"""
from __future__ import annotations

import json

from core.csvutil import write_csv
from core.errors import ApiError
from events.models import Event, EventRole, Role
from judging.models import Assignment, Comparison, ReviewStatus
from judging.policy import (
    engine_criteria,
    engine_reviews,
    included_reviews,
    review_values,
    rubric_criteria,
    scored_reviews,
)
from projects.models import Project, ProjectStatus
from results import engine
from teams.models import Team, TeamMember

RESULTS_STATUSES = (ProjectStatus.SUBMITTED,)
EXPORT_KINDS = (
    "participants",
    "teams",
    "projects",
    "judges",
    "assignments",
    "reviews",
    "progress",
    "results",
    "audit",
    "pairwise",
)


def _track_name(project: Project) -> str:
    return project.track.name if project.track_id else ""


def _score_text(score: float | None) -> str:
    return "" if score is None else f"{score:.2f}"


def _lam_for_event(event: Event) -> float | str:
    """Return a numeric lambda or 'auto' when the event uses adaptive selection."""
    if event.shrinkage_lambda is None:
        return "auto"
    return float(event.shrinkage_lambda)


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------

def participants_csv(event: Event) -> str:
    """Every participant (EventRole) with their team membership."""
    memberships = list(
        TeamMember.objects.filter(event=event)
        .select_related("user", "team")
        .order_by("team__name", "user__display_name")
    )
    header = ["participant_id", "display_name", "team_id", "team_name", "is_owner", "joined_at"]
    rows = []
    for m in memberships:
        role = EventRole.objects.filter(event=event, user=m.user, role=Role.PARTICIPANT).first()
        pid = role.public_id if role else ""
        rows.append([
            pid,
            m.user.display_name or m.user.email.split("@")[0],
            m.team.public_id,
            m.team.name,
            "yes" if m.is_owner else "no",
            m.joined_at.isoformat() if m.joined_at else "",
        ])
    return write_csv(header, rows)


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

def teams_csv(event: Event) -> str:
    """Every team with member count and project status."""
    teams = list(
        Team.objects.filter(event=event)
        .prefetch_related("memberships__user", "projects")
        .order_by("name", "id")
    )
    header = ["team_id", "team_name", "member_count", "project_id", "project_status"]
    rows = []
    for team in teams:
        project = (
            team.projects.filter(status__in=["draft", "submitted"])
            .order_by("-created_at")
            .first()
        )
        rows.append([
            team.public_id,
            team.name,
            team.memberships.count(),
            project.public_id if project else "",
            project.status if project else "none",
        ])
    return write_csv(header, rows)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def projects_csv(event: Event) -> str:
    """All projects with all custom-question answers (one column per question)."""
    questions = list(event.questions.order_by("position", "id"))
    projects = list(
        Project.objects.filter(event=event)
        .select_related("team", "track")
        .prefetch_related("answers__question")
        .order_by("title", "id")
    )
    header = [
        "project_id", "title", "team_id", "team_name", "track",
        "status", "revision", "submitted_at", "tags", "repo_url",
        "demo_video_url", "live_url",
    ] + [q.prompt for q in questions]
    rows = []
    for project in projects:
        answers_by_q = {a.question_id: a.value for a in project.answers.all()}
        rows.append([
            project.public_id,
            project.title,
            project.team.public_id,
            project.team.name,
            _track_name(project),
            project.status,
            project.revision,
            project.last_submitted_at.isoformat() if project.last_submitted_at else "",
            ", ".join(project.tech_tags or []),
            project.repo_url,
            project.demo_video_url,
            project.live_url,
        ] + [answers_by_q.get(q.pk, "") for q in questions])
    return write_csv(header, rows)


# ---------------------------------------------------------------------------
# Judges
# ---------------------------------------------------------------------------

def judges_csv(event: Event) -> str:
    """Every judge role with their tracks, assignment and submission counts."""
    roles = list(
        EventRole.objects.filter(event=event, role=Role.JUDGE)
        .select_related("user")
        .prefetch_related("tracks", "assignments__review")
        .order_by("public_id")
    )
    header = ["judge_id", "display_name", "tracks", "assigned", "submitted", "drafts"]
    rows = []
    for role in roles:
        asgs = list(role.assignments.all())
        submitted = sum(
            1 for a in asgs
            if hasattr(a, "review") and a.review.status == ReviewStatus.SUBMITTED
        )
        drafts = sum(
            1 for a in asgs
            if hasattr(a, "review") and a.review.status == ReviewStatus.DRAFT
        )
        rows.append([
            role.public_id,
            role.user.display_name or role.user.email.split("@")[0],
            "; ".join(sorted(t.name for t in role.tracks.all())),
            len(asgs),
            submitted,
            drafts,
        ])
    return write_csv(header, rows)


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

def assignments_csv(event: Event) -> str:
    """Every assignment with judge and project ids."""
    asgs = list(
        Assignment.objects.filter(event=event)
        .select_related("judge__user", "project__team", "project__track", "batch")
        .order_by("judge__public_id", "project__title", "id")
    )
    header = ["assignment_id", "judge_id", "judge_name", "project_id", "project_title",
              "track", "batch_id", "batch_method"]
    rows = []
    for asg in asgs:
        rows.append([
            asg.public_id,
            asg.judge.public_id,
            asg.judge.user.display_name or asg.judge.user.email.split("@")[0],
            asg.project.public_id,
            asg.project.title,
            _track_name(asg.project),
            asg.batch_id or "",
            asg.batch.method if asg.batch_id else "",
        ])
    return write_csv(header, rows)


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

def reviews_csv(event: Event) -> str:
    """Every review of the event with its criterion values, drafts included."""
    criteria = rubric_criteria(event)
    reviews = (
        event.reviews.select_related("judge", "judge__user", "project", "project__track")
        .prefetch_related("scores__criterion")
        .order_by("judge__public_id", "project__title", "id")
    )
    header = [
        "review_id", "judge_id", "judge_name", "project_id", "project_title", "track", "status",
    ] + [c.key for c in criteria] + ["weighted_score", "comment"]
    rows = []
    for review, score in scored_reviews(reviews):
        values = review_values(review)
        rows.append(
            [
                review.public_id,
                review.judge.public_id,
                review.judge.user.display_name or review.judge.user.email.split("@")[0],
                review.project.public_id,
                review.project.title,
                _track_name(review.project),
                review.status,
            ]
            + [values.get(c.key, "") for c in criteria]
            + [_score_text(score), review.comment]
        )
    return write_csv(header, rows)


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

def progress_csv(event: Event) -> str:
    """Per-judge progress: assigned, submitted, remaining."""
    from judging.policy import progress as judging_progress
    data = judging_progress(event)
    header = ["judge_id", "name", "tracks", "assigned", "submitted", "drafts",
              "remaining", "last_activity", "status"]
    rows = []
    for row in data["judges"]:
        rows.append([
            row["judge"],
            row["name"],
            "; ".join(row["tracks"]),
            row["assigned"],
            row["submitted"],
            row["drafts"],
            row["remaining"],
            row["last_activity"].isoformat() if row["last_activity"] else "",
            row["status"],
        ])
    return write_csv(header, rows)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

def _ranked_projects(event: Event) -> list[Project]:
    """Submitted projects only: withdrawn, disqualified, superseded and drafts are out."""
    return list(
        Project.objects.filter(event=event, status__in=RESULTS_STATUSES)
        .select_related("team", "track")
        .order_by("title", "id")
    )


def results_csv(event: Event) -> str:
    """Ranked projects with review count, raw weighted mean and normalized score.

    Uses the same engine path as the official publication so the CSV is
    always consistent with what was published. Column order matches the
    acceptance-checker contract: rank, project_id, title, team, track,
    reviews, raw_mean, status.
    """
    if event.ranking_method == "pairwise":
        from results.services import preview
        data = preview(event)
        return write_csv(
            ["rank", "project_id", "title", "team", "track", "reviews", "raw_mean", "status",
             "live_strength", "comparisons"],
            [[row["rank"] or "unranked", row["project_id"], row["title"], row["team"],
              row["track"] or "", row["n_reviews"], _score_text(row["raw_mean"]), row["status"],
              _score_text(row["live_strength"]), row["n_comparisons"]] for row in data["rows"]],
        )
    projects = _ranked_projects(event)
    by_public_id = {project.public_id: project for project in projects}
    reviews = [
        review for review in included_reviews(event)
        if review.project.public_id in by_public_id
    ]
    pairs = scored_reviews(reviews)
    review_inputs = engine_reviews(pairs)
    lam_val = _lam_for_event(event)
    # Same fallback as preview and publish: 'auto' lambda needs at least one review,
    # and an event with none yet exports every project as unranked instead of failing.
    if lam_val == "auto" and not review_inputs:
        lam_val = 2.0
    result = engine.evaluate(
        review_inputs,
        engine_criteria(event),
        lam=lam_val,
        target=event.reviews_per_project,
        method=event.ranking_method,
        projects=list(by_public_id),
    )
    rows = []
    for project in projects:
        public_id = project.public_id
        mean, count = result.raw.get(public_id, (None, 0))
        rank_primary = result.rank.get(public_id)
        status = "ranked" if rank_primary else "unranked_no_reviews"
        rows.append([
            rank_primary or "unranked",
            public_id,
            project.title,
            project.team.name,
            _track_name(project),
            count,
            _score_text(mean),
            status,
        ])
    rows.sort(key=lambda row: (_rank_order(row[0]), row[2].casefold(), row[1]))
    return write_csv(
        ["rank", "project_id", "title", "team", "track", "reviews", "raw_mean", "status"],
        rows,
    )


def _rank_order(rank_str: str) -> tuple[int, int]:
    """"3" sorts before "=3" for the same position, unranked rows go last."""
    if rank_str == "unranked":
        return (2, 0)
    return (0, int(rank_str.lstrip("=")))


def pairwise_csv(event: Event) -> str:
    """Organizer history keeps abstentions and retractions explicit."""
    comparisons = Comparison.objects.filter(event=event).select_related("judge", "left", "right", "winner")
    return write_csv(
        ["comparison_id", "judge_id", "left", "right", "winner", "created_at", "retracted_at"],
        [[item.public_id, item.judge.public_id, item.left.public_id, item.right.public_id,
          item.winner.public_id if item.winner_id else "", item.created_at.isoformat(),
          item.retracted_at.isoformat() if item.retracted_at else ""] for item in comparisons],
    )


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def audit_csv(event: Event) -> str:
    """Audit log for the event: every state-change in chronological order."""
    from audit.models import AuditEvent

    events = list(
        AuditEvent.objects.filter(event=event)
        .select_related("actor")
        .order_by("created_at", "id")
    )
    header = ["created_at", "actor", "action", "target_type", "target_id", "summary"]
    rows = []
    for ae in events:
        rows.append([
            ae.created_at.isoformat(),
            ae.actor_label,
            ae.action,
            ae.target_type,
            ae.target_id,
            ae.summary,
        ])
    return write_csv(header, rows)


# ---------------------------------------------------------------------------
# event.json round trip
# ---------------------------------------------------------------------------

def event_json(event: Event) -> str:
    """fixtures-shaped JSON export of one event.

    Can be re-imported via interop.importer.import_fixture into a new event
    (slug suffix appended to avoid collisions). Emails are included for
    judge round-trips; password hashes, token hashes and secrets are never
    exported.
    """
    from judging.models import Review as JudgingReview

    # Event header
    fixture_event = {
        "id": event.source_id or event.slug,
        "name": event.name,
        "submissions_close": event.submissions_close_at.isoformat(),
    }

    # Tracks
    tracks = [
        {
            "id": t.public_id,
            "name": t.name,
            "description": t.description,
        }
        for t in event.tracks.order_by("position", "id")
    ]

    # Judges (role = judge)
    judge_roles = list(
        EventRole.objects.filter(event=event, role=Role.JUDGE)
        .select_related("user")
        .prefetch_related("tracks")
        .order_by("public_id")
    )
    judges = [
        {
            "id": jr.public_id,
            "name": jr.user.display_name or jr.user.email.split("@")[0],
            "email": jr.user.email,
            "tracks": [t.public_id for t in jr.tracks.all()],
        }
        for jr in judge_roles
    ]

    # Teams and members
    teams_qs = list(
        Team.objects.filter(event=event)
        .prefetch_related("memberships__user")
        .order_by("public_id")
    )
    teams = [
        {
            "id": team.public_id,
            "name": team.name,
            "members": [
                m.user.email
                for m in team.memberships.order_by("is_owner desc".split()[0], "joined_at", "id")
            ],
        }
        for team in teams_qs
    ]

    # Projects (submitted only for a useful round-trip)
    projects_qs = list(
        Project.objects.filter(event=event, status=ProjectStatus.SUBMITTED)
        .select_related("team", "track")
        .order_by("public_id")
    )
    projects = [
        {
            "id": p.public_id,
            "title": p.title,
            "summary": p.summary,
            "description": p.description,
            "team": p.team.public_id,
            "track": p.track.public_id if p.track_id else None,
            "repo_url": p.repo_url,
            "demo_video_url": p.demo_video_url,
            "live_url": p.live_url,
            "tech_tags": p.tech_tags or [],
            "submitted_at": p.last_submitted_at.isoformat() if p.last_submitted_at else None,
        }
        for p in projects_qs
    ]

    # Scores (submitted reviews only)
    reviews_qs = list(
        JudgingReview.objects.filter(event=event, status=ReviewStatus.SUBMITTED)
        .select_related("judge", "project")
        .prefetch_related("scores__criterion")
        .order_by("id")
    )
    scores = []
    for review in reviews_qs:
        criteria_values = {
            score.criterion.key: score.value for score in review.scores.all()
        }
        scores.append({
            "judge": review.judge.public_id,
            "project": review.project.public_id,
            "criteria": criteria_values,
            "comment": review.comment,
            "submitted_at": review.submitted_at.isoformat() if review.submitted_at else None,
        })

    # The rubric the scores are on, so a re-import keeps its criteria, weights and ranges.
    rubric = [
        {
            "key": c.key,
            "name": c.name,
            "description": c.description,
            "weight": str(c.weight),
            "min_score": c.min_score,
            "max_score": c.max_score,
        }
        for c in rubric_criteria(event)
    ]

    export = {
        "event": fixture_event,
        "rubric": {"criteria": rubric},
        "tracks": tracks,
        "judges": judges,
        "teams": teams,
        "projects": projects,
        "scores": scores,
    }
    return json.dumps(export, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def export_csv(event: Event, kind: str) -> str:
    """Dispatch by kind; an unknown kind raises a 404 ApiError."""
    _dispatch = {
        "participants": participants_csv,
        "teams": teams_csv,
        "projects": projects_csv,
        "judges": judges_csv,
        "assignments": assignments_csv,
        "reviews": reviews_csv,
        "progress": progress_csv,
        "results": results_csv,
        "audit": audit_csv,
        "pairwise": pairwise_csv,
    }
    if kind not in _dispatch:
        raise ApiError(
            "unknown_export",
            f"No export called {kind!r}.",
            status_code=404,
            fields={"kind": [f"Available: {', '.join(sorted(_dispatch))}."]},
        )
    return _dispatch[kind](event)
