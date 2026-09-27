"""Project writes: revision snapshots, digests and canonical JSON.

Kept separate from the importer so submissions made through the API and
revisions created at import time produce byte-identical receipts.
"""
import hashlib
import json

import audit.services
from core.clock import now
from core.errors import ApiError
from django.db import transaction
from events.models import Event, Track
from events.policy import submission_window_open
from projects.models import ACTIVE_STATUSES, Project, ProjectRevision, ProjectStatus
from teams.models import Team, TeamMember

TITLE_MAX = 120
SUMMARY_MAX = 200


def canonical_json(payload) -> str:
    """Stable JSON for hashing: sorted keys, no whitespace, no datetimes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def snapshot_digest(snapshot: dict) -> str:
    """sha256 of the canonical snapshot; the receipt a revision is identified by."""
    return hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()


def build_snapshot(project, answers: dict | None = None) -> dict:
    """All public and private field values plus answers, for a revision snapshot."""
    payload = project.snapshot()
    if answers is None:
        answers = {str(a.question_id): a.value for a in project.answers.all()}
    payload["answers"] = {key: answers[key] for key in sorted(answers)}
    return payload


def create_revision(project, *, snapshot: dict | None = None, actor=None) -> ProjectRevision:
    """Append a revision and advance project.revision.

    The caller owns the transaction and the submission-window check.
    """
    number = (project.revision or 0) + 1
    payload = snapshot if snapshot is not None else build_snapshot(project)
    revision = ProjectRevision.objects.create(
        project=project,
        number=number,
        snapshot=payload,
        digest=snapshot_digest(payload),
        created_by=actor,
    )
    Project.objects.filter(pk=project.pk).update(revision=number)
    project.revision = number
    return revision


def _iso(value) -> str:
    return value.isoformat().replace("+00:00", "Z") if value else ""


def check_submission_window(event: Event, at=None) -> None:
    """The window check, in the order BUILD-SEC section 5 demands.

    It runs after authentication and before any validation, so a closed event
    answers 403 window_closed even for a request that would also be invalid.
    """
    if submission_window_open(event, at):
        return
    at = at or now()
    if event.submissions_open_at is not None and at < event.submissions_open_at:
        raise ApiError(
            "window_not_open",
            f"Submissions for {event.name} open at {_iso(event.submissions_open_at)}.",
            status_code=403,
        )
    raise ApiError(
        "window_closed",
        f"Submissions for {event.name} closed at {_iso(event.submissions_close_at)}.",
        status_code=403,
    )


def team_of(actor, event: Event) -> Team | None:
    """The one team this person belongs to in this event (a DB constraint)."""
    membership = TeamMember.objects.filter(event=event, user=actor).only("team_id").first()
    return membership.team if membership is not None else None


def resolve_track(event: Event, track_public_id: str | None):
    """A track of this event by public_id, or None. Cross-event ids are refused."""
    if not track_public_id:
        return None
    track = Track.objects.filter(event=event, public_id=track_public_id).first()
    if track is None and Track.objects.filter(public_id=track_public_id).exists():
        raise ApiError("cross_event", "That track belongs to a different event.",
                       status_code=400, fields={"track": ["Unknown track in this event."]})
    return track


def _validate_text(data: dict) -> tuple[str, str]:
    title = (data.get("title") or "").strip()
    summary = (data.get("summary") or "").strip()
    fields: dict[str, list[str]] = {}
    if not title:
        fields["title"] = ["A title is required."]
    elif len(title) > TITLE_MAX:
        fields["title"] = [f"Keep the title under {TITLE_MAX} characters."]
    if len(summary) > SUMMARY_MAX:
        fields["summary"] = [f"Keep the summary under {SUMMARY_MAX} characters."]
    if fields:
        raise ApiError("invalid", "Please correct the highlighted fields.", fields=fields)
    return title, summary


@transaction.atomic
def create_project(actor, event: Event, data: dict) -> Project:
    """Start the caller's team's draft project.

    Check order is the contract: authenticated, event exists, submission window,
    participant with a team, one active project per team, field lengths
    (BUILD-SEC sections 2 and 5).
    """
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise ApiError("not_authenticated", "Sign in to create a project.", status_code=401)
    if event is None:
        raise ApiError("event_not_found", "That event does not exist.", status_code=404)
    check_submission_window(event)

    team = team_of(actor, event)
    if team is None:
        raise ApiError(
            "not_a_participant",
            f"Join a team in {event.name} before creating a project.",
            status_code=403,
        )
    # Lock the team, then re-check: two tabs creating a draft at once must not
    # both pass the one-active-project rule.
    team = Team.objects.select_for_update().get(pk=team.pk)
    if Project.objects.filter(team=team, status__in=ACTIVE_STATUSES).exists():
        raise ApiError(
            "team_has_project",
            f"{team.name} already has an active project. Withdraw it before starting another.",
            status_code=409,
        )

    title, summary = _validate_text(data)
    track = resolve_track(event, data.get("track"))
    project = Project.objects.create(
        event=event,
        team=team,
        track=track,
        title=title,
        summary=summary,
        description=(data.get("description") or "").strip(),
        repo_url=(data.get("repo_url") or "").strip(),
        live_url=(data.get("live_url") or "").strip(),
        demo_video_url=(data.get("demo_video_url") or "").strip(),
        tech_tags=list(data.get("tech_tags") or []),
        status=ProjectStatus.DRAFT,
    )
    audit.services.record(
        actor, "project.created", event=event, target=project,
        summary=f"{actor.display_name or actor.email.split('@')[0]} started the draft {title} "
                f"for {team.name}.",
        data={"team": team.public_id, "status": ProjectStatus.DRAFT},
    )
    return project
