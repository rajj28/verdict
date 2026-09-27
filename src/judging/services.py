"""Judging business rules. Every write and permission check lives here."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import re

import audit.services
from accounts.models import User
from core.clock import now
from core.errors import ApiError
from core.ids import new_public_id
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from events.models import Event, EventRole, JudgingMode, Role, Track
from events.policy import judging_window_open
from judging import assign, policy
from judging.models import (
    Assignment, AssignmentBatch, AssignmentMethod, Criterion, CriterionScore, Rubric,
    Comparison, Conflict, ConflictSource, JudgeInvite, Review, ReviewExclusion, ReviewStatus,
)
from projects.models import Project, ProjectStatus
from teams.models import Team


def _authenticated(actor) -> None:
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise ApiError("not_authenticated", "Authentication is required.", status_code=401)


def _organizer(actor, event: Event) -> None:
    policy.require_manager(actor, event)


def _judge(actor, event: Event) -> EventRole:
    return policy.require_judge(actor, event)


def _cross_event() -> ApiError:
    return ApiError("cross_event", "Related judging records must belong to this event.",
                    status_code=400)


def _not_found(kind: str) -> ApiError:
    return ApiError(f"{kind}_not_found", f"No {kind.replace('_', ' ')} was found.",
                    status_code=404)


def _role(event: Event, public_id: str, *, judge_only: bool = True) -> EventRole:
    query = EventRole.objects.select_for_update().filter(event=event, public_id=public_id)
    if judge_only:
        query = query.filter(role=Role.JUDGE)
    role = query.first()
    if role is None:
        if EventRole.objects.filter(event=event, public_id=public_id).exists():
            raise _not_found("judge")
        if EventRole.objects.filter(public_id=public_id).exists():
            raise _cross_event()
        raise _not_found("judge")
    return role


def _track_ids(event: Event, track_ids) -> list[int]:
    values = list(track_ids or [])
    if any(isinstance(item, Track) and item.event_id != event.pk for item in values):
        raise _cross_event()
    ids = [item.pk if isinstance(item, Track) else item
           for item in values if isinstance(item, Track) or isinstance(item, int)]
    public_ids = [item for item in values if isinstance(item, str)]
    if any(not isinstance(item, (Track, str, int)) or isinstance(item, bool) for item in values):
        raise ApiError("invalid", "Tracks must be public ids.", fields={"tracks": ["Invalid track id."]})
    tracks = list(Track.objects.filter(pk__in=ids))
    tracks.extend(Track.objects.filter(public_id__in=public_ids).exclude(pk__in=ids))
    requested_count = len(set(
        [("pk", item.pk) if isinstance(item, Track) else ("public", item) if isinstance(item, str)
         else ("pk", item) for item in values]
    ))
    if any(track.event_id != event.pk for track in tracks):
        raise _cross_event()
    if len(tracks) != requested_count:
        raise ApiError("invalid", "One or more tracks could not be found.",
                       fields={"tracks": ["Use existing track public ids."]})
    return [track.pk for track in tracks]


def _name(user) -> str:
    return user.display_name or user.email.split("@")[0]


def _criteria_payload(criteria) -> list[dict]:
    return [
        {
            "key": item.key,
            "name": item.name,
            "description": item.description,
            "weight": str(item.weight),
            "min_score": item.min_score,
            "max_score": item.max_score,
            "position": item.position,
        }
        for item in criteria
    ]


@transaction.atomic
def replace_rubric(actor: User, event: Event, criteria: list[dict]) -> Rubric:
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    _organizer(actor, locked_event)
    if locked_event.scoring_locked_at is not None:
        raise ApiError("scoring_locked", "The rubric is locked after the first submitted review.",
                       status_code=409)
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 10:
        raise ApiError("invalid", "A rubric must contain 1 to 10 criteria.", fields={
            "criteria": ["Provide between 1 and 10 criteria."]
        })
    fields = {}
    if any(not isinstance(item, dict) for item in criteria):
        fields["criteria"] = ["Each criterion must be an object."]
    keys = [item.get("key") for item in criteria if isinstance(item, dict)]
    valid_keys = [key for key in keys if isinstance(key, str)]
    if len(set(valid_keys)) != len(valid_keys):
        fields["criteria"] = ["Criterion keys must be unique."]
    normalized = []
    for index, item in enumerate(criteria):
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        name = item.get("name")
        try:
            weight = Decimal(str(item.get("weight")))
            minimum = item.get("min_score", 1)
            maximum = item.get("max_score", 5)
            position = item.get("position", index)
            if isinstance(minimum, bool) or not isinstance(minimum, int):
                raise ValueError
            if isinstance(maximum, bool) or not isinstance(maximum, int):
                raise ValueError
            if isinstance(position, bool) or not isinstance(position, int) or position < 0:
                raise ValueError
            if (
                not weight.is_finite() or weight <= 0 or weight > Decimal("999.999")
                or weight != weight.quantize(Decimal("0.001"))
                or not 0 <= minimum <= 32767 or not 0 <= maximum <= 32767
                or maximum <= minimum
            ):
                raise ValueError
        except (InvalidOperation, TypeError, ValueError):
            fields[f"criteria.{index}"] = ["Weight must be positive and max_score must exceed min_score."]
            continue
        if (not isinstance(key, str) or not re.fullmatch(r"[-a-zA-Z0-9_]{1,60}", key)
                or not isinstance(name, str) or not name or len(name) > 120):
            fields[f"criteria.{index}"] = ["Each criterion needs a key and name."]
            continue
        description = item.get("description", "")
        if not isinstance(description, str):
            fields[f"criteria.{index}"] = ["Description must be text."]
            continue
        normalized.append({
            "key": key, "name": name, "description": description,
            "weight": weight, "min_score": minimum, "max_score": maximum, "position": position,
        })
    if fields or len(normalized) != len(criteria):
        raise ApiError("invalid", "Please correct the rubric criteria.", fields=fields)
    rubric, _ = Rubric.objects.get_or_create(event=locked_event)
    old = _criteria_payload(rubric.criteria.all().order_by("position", "id"))
    rubric.version += 1
    rubric.save(update_fields=["version", "updated_at"])
    rubric.criteria.all().delete()
    Criterion.objects.bulk_create([
        Criterion(rubric=rubric, **item) for item in normalized
    ])
    new = _criteria_payload(rubric.criteria.all().order_by("position", "id"))
    audit.services.record(
        actor, "judging.rubric_replaced", event=locked_event, target=locked_event,
        summary=f"{_name(actor)} replaced the rubric for {locked_event.name} (version {rubric.version}).",
        data={"old": old, "new": new, "version": rubric.version},
    )
    return rubric


@transaction.atomic
def add_judge(actor: User, event: Event, email: str, tracks: list) -> EventRole:
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    _organizer(actor, locked_event)
    track_pks = _track_ids(locked_event, tracks)
    user = User.objects.filter(email__iexact=(email or "").strip()).first()
    if user is None:
        raise ApiError("user_not_found", "No existing account has that email.", status_code=404,
                       fields={"email": ["No account found."]})
    existing = EventRole.objects.filter(event=locked_event, user=user).first()
    if existing is not None:
        raise ApiError("role_conflict", "This person already has a role in the event.", status_code=409)
    role = EventRole.objects.create(
        event=locked_event, user=user, role=Role.JUDGE, public_id=new_public_id("jdg"),
        added_by=actor,
    )
    role.tracks.set(track_pks)
    audit.services.record(
        actor, "judging.judge_added", event=locked_event, target=role,
        summary=f"{_name(actor)} added {_name(user)} as a judge for {locked_event.name}.",
        data={"judge": role.public_id, "tracks": [t.public_id for t in Track.objects.filter(pk__in=track_pks)]},
    )
    return role


@transaction.atomic
def update_judge_tracks(actor: User, event: Event, judge_id: str, tracks: list) -> EventRole:
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    _organizer(actor, locked_event)
    role = _role(locked_event, judge_id)
    track_pks = _track_ids(locked_event, tracks)
    old_tracks = list(role.tracks.values_list("public_id", flat=True))
    role.tracks.set(track_pks)
    new_tracks = list(role.tracks.values_list("public_id", flat=True))
    if set(old_tracks) != set(new_tracks):
        for assignment_row in Assignment.objects.select_for_update().filter(
            event=locked_event, judge=role
        ).exclude(
            project__track_id__in=track_pks
        ).select_related("project"):
            if Review.objects.filter(assignment=assignment_row, status=ReviewStatus.SUBMITTED).exists():
                continue
            audit.services.record(
                actor, "judging.assignment_removed_after_track_change", event=locked_event,
                target=assignment_row,
                summary=f"Removed {_name(role.user)}'s assignment for {assignment_row.project.title} "
                        "after changing judge tracks.",
                data={"judge": role.public_id, "project": assignment_row.project.public_id},
            )
            assignment_row.delete()
        audit.services.record(
            actor, "judging.judge_tracks_updated", event=locked_event, target=role,
            summary=f"{_name(actor)} updated {_name(role.user)}'s tracks for {locked_event.name}.",
            data={"old": sorted(old_tracks), "new": sorted(new_tracks)},
        )
    return role


@transaction.atomic
def remove_judge(actor: User, event: Event, judge_id: str) -> None:
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    _organizer(actor, locked_event)
    role = _role(locked_event, judge_id)
    if Review.objects.filter(judge=role, status=ReviewStatus.SUBMITTED).exists():
        raise ApiError("judge_has_reviews", "A judge with submitted reviews cannot be removed.",
                       status_code=409)
    assignment_count = Assignment.objects.filter(judge=role).count()
    audit.services.record(
        actor, "judging.judge_removed", event=locked_event, target=role,
        summary=f"{_name(actor)} removed judge {_name(role.user)} from {locked_event.name}.",
        data={"assignments_removed": assignment_count},
    )
    role.delete()


@transaction.atomic
def create_judge_invites(actor: User, event: Event, emails: list[str], tracks: list) -> list[dict]:
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    _organizer(actor, locked_event)
    track_pks = _track_ids(locked_event, tracks)
    if not isinstance(emails, list) or not emails:
        raise ApiError("invalid", "Provide at least one email address.", fields={"emails": ["Required."]})
    created = []
    for email in emails:
        normalized = str(email).strip().lower()
        try:
            validate_email(normalized)
        except ValidationError:
            raise ApiError("invalid", "Provide valid email addresses.", fields={"emails": ["Invalid email."]})
        invite = JudgeInvite.objects.create(
            event=locked_event, email=normalized, created_by=actor,
            expires_at=now() + timedelta(days=14),
        )
        invite.tracks.set(track_pks)
        url = f"/judge-invite?token={invite.token}"
        created.append({"token": invite.token, "email": invite.email, "url": url,
                        "expires_at": invite.expires_at})
        audit.services.record(
            actor, "judging.judge_invite_created", event=locked_event, target=locked_event,
            summary=f"{_name(actor)} invited a judge to {locked_event.name}.",
            data={"email": invite.email, "expires_at": invite.expires_at.isoformat()},
        )
    return created


@transaction.atomic
def accept_judge_invite(actor: User, token: str) -> EventRole:
    _authenticated(actor)
    invite = (JudgeInvite.objects.select_for_update().select_related("event")
              .filter(token=token).first())
    if invite is None:
        raise ApiError("invite_gone", "This judge invitation is invalid or no longer available.",
                       status_code=410)
    if invite.revoked_at is not None or invite.accepted_at is not None or invite.expires_at <= now():
        raise ApiError("invite_gone", "This judge invitation is revoked, expired or already used.",
                       status_code=410)
    if actor.email.casefold() != invite.email.casefold():
        raise ApiError("invite_email_mismatch", "Sign in with the email address this invitation was sent to.",
                       status_code=403)
    Event.objects.select_for_update().get(pk=invite.event_id)
    if EventRole.objects.filter(event=invite.event, user=actor).exists():
        raise ApiError("role_conflict", "This person already has a role in the event.", status_code=409)
    role = EventRole.objects.create(
        event=invite.event, user=actor, role=Role.JUDGE, public_id=new_public_id("jdg"),
        added_by=invite.created_by,
    )
    role.tracks.set(invite.tracks.all())
    invite.accepted_at = now()
    invite.accepted_by = actor
    invite.save(update_fields=["accepted_at", "accepted_by"])
    audit.services.record(
        actor, "judging.judge_invite_accepted", event=invite.event, target=role,
        summary=f"{_name(actor)} accepted a judge invitation for {invite.event.name}.",
        data={"judge": role.public_id},
    )
    return role


def _remove_unreviewed_assignment(actor, event: Event, role: EventRole, team: Team) -> int:
    assignments = Assignment.objects.select_for_update().filter(
        event=event, judge=role, project__team=team
    )
    count = 0
    for assignment in assignments.select_related("project"):
        if Review.objects.filter(assignment=assignment, status=ReviewStatus.SUBMITTED).exists():
            raise ApiError("review_exists", "A submitted review exists; an organizer must decide how to proceed.",
                           status_code=409)
        audit.services.record(
            actor, "judging.assignment_removed_for_conflict", event=event, target=assignment,
            summary=f"Removed {_name(role.user)}'s assignment for {assignment.project.title} after a conflict.",
            data={"reason": "conflict"},
        )
        assignment.delete()
        count += 1
    return count


@transaction.atomic
def declare_conflict(actor_judge: User, event: Event, team: Team | str, reason: str = "") -> Conflict:
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    role = _judge(actor_judge, locked_event)
    if isinstance(team, str):
        team_id = team
        team = Team.objects.filter(event=locked_event, public_id=team_id).first()
        if team is None:
            if Team.objects.filter(public_id=team_id).exists():
                raise _cross_event()
            raise _not_found("team")
    elif team.event_id != locked_event.pk:
        raise _cross_event()
    _remove_unreviewed_assignment(actor_judge, locked_event, role, team)
    conflict, created = Conflict.objects.get_or_create(
        event=locked_event, judge=role, team=team,
        defaults={"reason": reason[:300], "source": ConflictSource.DECLARED_BY_JUDGE,
                  "created_by": actor_judge},
    )
    if created:
        audit.services.record(
            actor_judge, "judging.conflict_declared", event=locked_event, target=team,
            summary=f"{_name(actor_judge)} declared a conflict with team {team.name} in {locked_event.name}.",
            data={"team": team.public_id, "reason": conflict.reason},
        )
    return conflict


@transaction.atomic
def add_conflict(actor: User, event: Event, judge_id: str, team: Team | str,
                 reason: str = "") -> Conflict:
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    _organizer(actor, locked_event)
    role = _role(locked_event, judge_id)
    if isinstance(team, str):
        team_id = team
        team = Team.objects.filter(event=locked_event, public_id=team_id).first()
        if team is None:
            if Team.objects.filter(public_id=team_id).exists():
                raise _cross_event()
            raise _not_found("team")
    elif team.event_id != locked_event.pk:
        raise _cross_event()
    _remove_unreviewed_assignment(actor, locked_event, role, team)
    conflict, created = Conflict.objects.get_or_create(
        event=locked_event, judge=role, team=team,
        defaults={"reason": reason[:300], "source": ConflictSource.ORGANIZER, "created_by": actor},
    )
    if created:
        audit.services.record(
            actor, "judging.conflict_added", event=locked_event, target=team,
            summary=f"{_name(actor)} recorded a conflict for {_name(role.user)} and team {team.name}.",
            data={"judge": role.public_id, "team": team.public_id, "reason": conflict.reason},
        )
    return conflict


def _assignment_ineligibility(event: Event, role: EventRole, project: Project) -> str | None:
    if role.role != Role.JUDGE:
        return "not_a_judge"
    if role.event_id != event.pk or project.event_id != event.pk:
        raise _cross_event()
    if project.team.event_id != event.pk:
        raise _cross_event()
    if project.track_id and project.track.event_id != event.pk:
        raise _cross_event()
    if role.tracks.exclude(event=event).exists():
        raise _cross_event()
    if project.status != ProjectStatus.SUBMITTED:
        return "project_not_submitted"
    if project.track_id is None or not role.tracks.filter(pk=project.track_id).exists():
        return "track_mismatch"
    if Conflict.objects.filter(event=event, judge=role, team=project.team).exists():
        return "conflict"
    if Assignment.objects.filter(event=event, judge=role, project=project).exists():
        return "already_assigned"
    return None


@transaction.atomic
def assign_batch(actor: User, event: Event, judge_ids: list, project_ids: list) -> dict:
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    _organizer(actor, locked_event)
    if not isinstance(judge_ids, (list, tuple)) or not isinstance(project_ids, (list, tuple)):
        raise ApiError("invalid", "Judges and projects must be lists.", fields={"judges": ["Expected lists."]})
    judges = []
    for item in judge_ids:
        role = item if isinstance(item, EventRole) else EventRole.objects.filter(
            event=locked_event, public_id=item
        ).first()
        if role is None:
            if EventRole.objects.filter(public_id=item).exists():
                raise _cross_event()
            raise _not_found("judge")
        if role.event_id != locked_event.pk:
            raise _cross_event()
        judges.append(role)
    projects = []
    for item in project_ids:
        project = item if isinstance(item, Project) else Project.objects.filter(
            event=locked_event, public_id=item
        ).first()
        if project is None:
            if Project.objects.filter(public_id=item).exists():
                raise _cross_event()
            raise _not_found("project")
        if project.event_id != locked_event.pk:
            raise _cross_event()
        projects.append(project)
    batch = AssignmentBatch.objects.create(event=locked_event, method=AssignmentMethod.MANUAL,
                                           created_by=actor)
    created, skipped = [], []
    for role in judges:
        for project in projects:
            reason = _assignment_ineligibility(locked_event, role, project)
            if reason is not None:
                skipped.append({"judge": role.public_id, "project": project.public_id,
                                "reason": reason})
                continue
            assignment_row = Assignment.objects.create(
                event=locked_event, judge=role, project=project, batch=batch,
            )
            created.append(assignment_row)
            audit.services.record(
                actor, "judging.assignment_created", event=locked_event, target=assignment_row,
                summary=f"{_name(actor)} assigned {_name(role.user)} to review {project.title}.",
                data={"judge": role.public_id, "project": project.public_id},
            )
    if not created:
        batch.delete()
    else:
        audit.services.record(
            actor, "judging.assignment_batch_created", event=locked_event, target=locked_event,
            summary=f"{_name(actor)} created a manual assignment batch for {locked_event.name}.",
            data={"created": len(created), "skipped": len(skipped)},
        )
    return {"created": created, "skipped": skipped}


@transaction.atomic
def remove_assignment(actor: User, event: Event, assignment_id: str) -> None:
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    _organizer(actor, locked_event)
    assignment_row = Assignment.objects.select_for_update().filter(
        event=locked_event, public_id=assignment_id
    ).select_related(
        "judge__user", "project"
    ).first()
    if assignment_row is None:
        if Assignment.objects.filter(public_id=assignment_id).exists():
            raise _cross_event()
        raise _not_found("assignment")
    if Review.objects.filter(assignment=assignment_row, status=ReviewStatus.SUBMITTED).exists():
        raise ApiError("review_exists", "An assignment with a submitted review cannot be removed.",
                       status_code=409)
    audit.services.record(
        actor, "judging.assignment_removed", event=locked_event, target=assignment_row,
        summary=f"{_name(actor)} removed {_name(assignment_row.judge.user)}'s assignment for "
                f"{assignment_row.project.title}.",
        data={},
    )
    assignment_row.delete()


def _assignment_inputs(event: Event):
    roles = list(EventRole.objects.filter(event=event, role=Role.JUDGE).prefetch_related(
        "tracks", "assignments__project"
    ))
    projects = list(Project.objects.filter(event=event, status=ProjectStatus.SUBMITTED)
                    .select_related("track", "team").prefetch_related("assignments__judge"))
    if any(track.event_id != event.pk for role in roles for track in role.tracks.all()):
        raise _cross_event()
    if any(project.track_id and project.track.event_id != event.pk for project in projects):
        raise _cross_event()
    if any(project.team.event_id != event.pk for project in projects):
        raise _cross_event()
    conflicts_by_judge: dict[int, set[str]] = {}
    for conflict in Conflict.objects.filter(event=event).select_related("judge", "team"):
        conflicts_by_judge.setdefault(conflict.judge_id, set()).add(conflict.team.public_id)
    judge_inputs = [
        assign.JudgeInput(
            judge_id=role.public_id,
            track_ids=frozenset(track.public_id for track in role.tracks.all()),
            conflict_team_ids=frozenset(conflicts_by_judge.get(role.pk, set())),
            existing_assignment_project_ids=frozenset(
                row.project.public_id for row in role.assignments.all()
                if row.event_id == event.pk and row.project.status == ProjectStatus.SUBMITTED
            ),
        )
        for role in roles
    ]
    project_inputs = [
        assign.ProjectInput(
            project_id=project.public_id,
            track_id=project.track.public_id if project.track_id else "",
            team_id=project.team.public_id,
            existing_reviewer_judge_ids=frozenset(
                row.judge.public_id for row in project.assignments.all()
                if row.event_id == event.pk and row.judge.role == Role.JUDGE
            ),
        )
        for project in projects
    ]
    role_map = {role.public_id: role for role in roles}
    project_map = {project.public_id: project for project in projects}
    return judge_inputs, project_inputs, role_map, project_map


@transaction.atomic
def auto_assign(actor: User, event: Event, target: int | None = None, max_load: int | None = None,
                seed: str | None = None, dry_run: bool = True) -> dict:
    _organizer(actor, event)
    if isinstance(target, bool) or (target is not None and (not isinstance(target, int) or target < 1)):
        raise ApiError("invalid", "Target must be a positive integer.", fields={"target": ["Must be positive."]})
    if isinstance(max_load, bool) or (max_load is not None and
                                      (not isinstance(max_load, int) or max_load < 1)):
        raise ApiError("invalid", "Max load must be a positive integer.",
                       fields={"max_load": ["Must be positive."]})
    if not isinstance(dry_run, bool):
        raise ApiError("invalid", "dry_run must be a boolean.", fields={"dry_run": ["Expected true or false."]})
    if seed is not None and not isinstance(seed, str):
        raise ApiError("invalid", "Seed must be a string.", fields={"seed": ["Expected text."]})
    target = target or event.reviews_per_project
    seed = seed or event.slug
    def plan(event_row):
        judge_inputs, project_inputs, role_map, project_map = _assignment_inputs(event_row)
        proposal = assign.propose_assignments(judge_inputs, project_inputs, target, max_load, seed)
        return proposal, role_map, project_map

    if dry_run:
        proposal, _roles, _projects = plan(event)
        return {
            "dry_run": True,
            "created": [],
            "proposed": [{"judge": row.judge_id, "project": row.project_id}
                         for row in proposal.new_assignments],
            "unfilled": [{"project": row.project_id, "target": row.target,
                          "assigned": row.assigned, "reason": row.reason} for row in proposal.unfilled],
        }
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    _organizer(actor, locked_event)
    proposal, role_map, project_map = plan(locked_event)
    batch = AssignmentBatch.objects.create(
        event=locked_event, method=AssignmentMethod.AUTO, created_by=actor,
        params={"target": target, "max_load": max_load, "seed": seed},
    )
    created = []
    for pair in proposal.new_assignments:
        role, project = role_map[pair.judge_id], project_map[pair.project_id]
        if _assignment_ineligibility(locked_event, role, project) is not None:
            raise ApiError("assignment_changed", "Eligibility changed while applying the assignment plan.",
                           status_code=409)
        assignment_row = Assignment.objects.create(
            event=locked_event, judge=role, project=project, batch=batch,
        )
        created.append(assignment_row)
        audit.services.record(
            actor, "judging.assignment_created", event=locked_event, target=assignment_row,
            summary=f"{_name(actor)} assigned {_name(role.user)} to review {project.title}.",
            data={"judge": role.public_id, "project": project.public_id, "method": "auto"},
        )
    audit.services.record(
        actor, "judging.assignment_batch_created", event=locked_event, target=locked_event,
        summary=f"{_name(actor)} created an automatic assignment batch for {locked_event.name}.",
        data={"created": len(created), "unfilled": len(proposal.unfilled), "params": batch.params},
    )
    return {
        "dry_run": False,
        "created": created,
        "proposed": [{"judge": row.judge_id, "project": row.project_id}
                     for row in proposal.new_assignments],
        "unfilled": [{"project": row.project_id, "target": row.target,
                      "assigned": row.assigned, "reason": row.reason} for row in proposal.unfilled],
        "batch": batch,
    }


def _review_context(actor, event: Event, project: Project):
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    role = _judge(actor, locked_event)
    locked_project = Project.objects.select_for_update().filter(
        pk=project.pk, event=locked_event
    ).first()
    if locked_project is None:
        raise _cross_event()
    assignment_row = Assignment.objects.select_for_update().filter(
        event=locked_event, judge=role, project=locked_project
    ).first()
    if assignment_row is None:
        raise ApiError("forbidden", "You are not assigned to review this project.", status_code=403)
    if locked_project.track_id is None or not role.tracks.filter(pk=locked_project.track_id).exists():
        raise ApiError("forbidden", "This project is outside your assigned tracks.", status_code=403)
    if locked_project.track.event_id != locked_event.pk:
        raise _cross_event()
    if locked_project.team.event_id != locked_event.pk:
        raise _cross_event()
    if Conflict.objects.filter(event=locked_event, judge=role, team=locked_project.team).exists():
        raise ApiError("forbidden", "A conflict of interest prevents this review.", status_code=403)
    if not judging_window_open(locked_event):
        raise ApiError("judging_closed", "The judging window is closed.", status_code=403)
    if locked_project.status != ProjectStatus.SUBMITTED:
        raise ApiError("project_not_submitted", "Only submitted projects can be reviewed.", status_code=403)
    return locked_event, role, assignment_row, locked_project


def _validated_scores(event: Event, scores, *, require_all: bool) -> tuple[list[Criterion], dict]:
    criteria = list(Criterion.objects.filter(rubric__event=event).select_related("rubric")
                    .order_by("position", "id"))
    if not isinstance(scores, dict):
        raise ApiError("invalid", "Scores must be an object.", fields={"scores": ["Expected an object."]})
    by_key = {criterion.key: criterion for criterion in criteria}
    fields = {}
    values = {}
    for key, value in scores.items():
        criterion = by_key.get(key)
        if criterion is None:
            fields[f"scores.{key}"] = ["Unknown criterion."]
        elif isinstance(value, bool) or not isinstance(value, int):
            fields[f"scores.{key}"] = ["Score must be an integer."]
        elif not criterion.min_score <= value <= criterion.max_score:
            fields[f"scores.{key}"] = [f"Score must be between {criterion.min_score} and {criterion.max_score}."]
        else:
            values[criterion.pk] = value
    if require_all:
        for criterion in criteria:
            if criterion.key not in scores:
                fields[f"scores.{criterion.key}"] = ["This criterion is required."]
    if not criteria:
        fields["scores"] = ["The event does not have a scoring rubric."]
    if fields:
        raise ApiError("invalid", "Please correct the review scores.", fields=fields)
    return criteria, values


def _save_review(actor, event: Event, project: Project, scores, comment: str,
                 *, submit: bool) -> Review:
    locked_event, role, assignment_row, project = _review_context(actor, event, project)
    criteria, score_values = _validated_scores(locked_event, scores, require_all=submit)
    if not isinstance(comment, str) or len(comment) > 2000:
        raise ApiError("invalid", "Comment must be at most 2000 characters.",
                       fields={"comment": ["Ensure this field has no more than 2000 characters."]})
    review = Review.objects.select_for_update().filter(assignment=assignment_row).first()
    old = None
    if review is None:
        review = Review(
            event=locked_event, assignment=assignment_row, judge=role, project=project,
            rubric_version=criteria[0].rubric.version, project_revision=project.revision,
        )
    else:
        old = {
            "status": review.status,
            "scores": {row.criterion.key: row.value for row in review.scores.select_related("criterion")},
            "comment": review.comment,
        }
    review.comment = comment
    review.rubric_version = criteria[0].rubric.version
    review.project_revision = project.revision
    if submit:
        review.status = ReviewStatus.SUBMITTED
        review.submitted_at = now()
        if locked_event.scoring_locked_at is None:
            locked_event.scoring_locked_at = now()
            locked_event.save(update_fields=["scoring_locked_at", "updated_at"])
    else:
        review.status = ReviewStatus.DRAFT
        review.submitted_at = None
    review.save()
    for criterion in criteria:
        if criterion.pk in score_values:
            CriterionScore.objects.update_or_create(
                review=review, criterion=criterion, defaults={"value": score_values[criterion.pk]},
            )
    action = "judging.review_submitted" if submit else "judging.review_draft_saved"
    verb = "submitted a review of" if submit else "saved a draft review of"
    audit.services.record(
        actor, action, event=locked_event, target=review,
        summary=f"{_name(actor)} {verb} {project.title}.",
        data={"old": old, "new": {"status": review.status,
                                  "scores": {c.key: score_values[c.pk] for c in criteria if c.pk in score_values},
                                  "comment": comment}},
    )
    return review


@transaction.atomic
def check_review_access(actor: User, event: Event, project: Project) -> None:
    """Reject unauthorized, out-of-track, and closed writes before parsing score input."""
    _review_context(actor, event, project)


@transaction.atomic
def save_review_draft(actor: User, event: Event, project: Project, scores: dict,
                      comment: str = "") -> Review:
    return _save_review(actor, event, project, scores, comment, submit=False)


@transaction.atomic
def submit_review(actor: User, event: Event, project: Project, scores: dict,
                  comment: str = "") -> Review:
    return _save_review(actor, event, project, scores, comment, submit=True)


def _pairwise_context(actor: User, event: Event) -> tuple[Event, EventRole]:
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    role = _judge(actor, locked_event)
    if locked_event.judging_mode not in (JudgingMode.PAIRWISE, JudgingMode.BOTH):
        raise ApiError("pairwise_disabled", "Pairwise comparisons are not enabled for this event.",
                       status_code=409)
    if not judging_window_open(locked_event):
        raise ApiError("judging_closed", "The judging window is closed.", status_code=403)
    return locked_event, role


@transaction.atomic
def next_pair(actor: User, event: Event) -> tuple[Project, Project] | None:
    """Select the next un-compared pair from this judge's assigned projects."""
    locked_event, role = _pairwise_context(actor, event)
    projects = list(Project.objects.filter(
        event=locked_event,
        status=ProjectStatus.SUBMITTED,
        assignments__event=locked_event,
        assignments__judge=role,
        track__in=role.tracks.all(),
    ).exclude(
        team__conflicts__judge=role,
    ).select_related("team", "track").prefetch_related("answers__question").distinct()
     .order_by("public_id"))
    if any(project.team.event_id != locked_event.pk
           or (project.track_id and project.track.event_id != locked_event.pk) for project in projects):
        raise _cross_event()
    compared = {
        frozenset((left_id, right_id))
        for left_id, right_id in Comparison.objects.filter(event=locked_event, judge=role)
        .values_list("left_id", "right_id")
    }
    for index, left in enumerate(projects):
        for right in projects[index + 1:]:
            if frozenset((left.pk, right.pk)) not in compared:
                return left, right
    return None


@transaction.atomic
def save_comparison(actor: User, event: Event, left: Project | str, right: Project | str,
                    winner: Project | str | None = None) -> Comparison:
    locked_event, role = _pairwise_context(actor, event)

    def resolve(project: Project | str) -> Project:
        if isinstance(project, Project):
            if project.event_id != locked_event.pk:
                raise _cross_event()
            resolved = Project.objects.select_for_update().filter(
                pk=project.pk, event=locked_event
            ).first()
        else:
            resolved = Project.objects.select_for_update().filter(
                event=locked_event, public_id=project
            ).first()
            if resolved is None and Project.objects.filter(public_id=project).exists():
                raise _cross_event()
        if resolved is None:
            raise _not_found("project")
        return resolved

    left_project, right_project = resolve(left), resolve(right)
    if left_project.pk == right_project.pk:
        raise ApiError("invalid", "A comparison requires two different projects.",
                       fields={"right": ["Choose a different project."]})
    if winner is not None and not isinstance(winner, (Project, str)):
        raise ApiError("invalid", "Winner must be a project id or null.",
                       fields={"winner": ["Choose the left project, right project, or null."]})
    winner_project = resolve(winner) if isinstance(winner, (Project, str)) else None
    if winner_project is not None and winner_project.pk not in (left_project.pk, right_project.pk):
        raise ApiError("invalid", "The winner must be one of the compared projects.",
                       fields={"winner": ["Choose the left project, right project, or null."]})
    for project in (left_project, right_project):
        if project.status != ProjectStatus.SUBMITTED:
            raise ApiError("project_not_submitted", "Only submitted projects can be compared.", status_code=403)
        if project.track_id is None or not role.tracks.filter(pk=project.track_id).exists():
            raise ApiError("forbidden", "Both projects must be in one of your assigned tracks.",
                           status_code=403)
        if project.track.event_id != locked_event.pk or project.team.event_id != locked_event.pk:
            raise _cross_event()
        if not Assignment.objects.filter(
            event=locked_event, judge=role, project=project
        ).exists():
            raise ApiError("forbidden", "You must be assigned to both projects to compare them.",
                           status_code=403)
        if Conflict.objects.filter(event=locked_event, judge=role, team=project.team).exists():
            raise ApiError("forbidden", "A conflict of interest prevents this comparison.",
                           status_code=403)
    existing = Comparison.objects.filter(event=locked_event, judge=role).filter(
        Q(left=left_project, right=right_project) | Q(left=right_project, right=left_project)
    ).exists()
    if existing:
        raise ApiError("comparison_exists", "These projects have already been compared by this judge.",
                       status_code=409)
    comparison = Comparison.objects.create(
        event=locked_event, judge=role, left=left_project, right=right_project, winner=winner_project,
    )
    verdict = "skipped" if winner_project is None else (
        f"preferred {winner_project.title}"
    )
    audit.services.record(
        actor, "judging.comparison_created", event=locked_event, target=left_project,
        summary=f"{_name(actor)} {verdict} in a comparison with {left_project.title} and {right_project.title}.",
        data={"left": left_project.public_id, "right": right_project.public_id,
              "winner": winner_project.public_id if winner_project else None},
    )
    return comparison


@transaction.atomic
def exclude_review(actor: User, review: Review, reason: str) -> ReviewExclusion:
    locked_event = Event.objects.select_for_update().get(pk=review.event_id)
    _organizer(actor, locked_event)
    locked_review = Review.objects.select_for_update().filter(pk=review.pk, event=locked_event).first()
    if locked_review is None:
        raise _not_found("review")
    if locked_review.status != ReviewStatus.SUBMITTED:
        raise ApiError("review_not_submitted", "Only submitted reviews can be excluded.", status_code=409)
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 300:
        raise ApiError("invalid", "Provide a reason of at most 300 characters.",
                       fields={"reason": ["Required; maximum 300 characters."]})
    exclusion, created = ReviewExclusion.objects.get_or_create(
        review=locked_review, defaults={"reason": reason.strip(), "created_by": actor},
    )
    if created:
        audit.services.record(
            actor, "judging.review_excluded", event=locked_event, target=locked_review,
            summary=f"{_name(actor)} excluded a review of {locked_review.project.title}: {reason.strip()}.",
            data={"reason": reason.strip(), "publication_exists": locked_event.result_publications.exists()},
        )
    return exclusion


@transaction.atomic
def include_review(actor: User, review: Review) -> Review:
    locked_event = Event.objects.select_for_update().get(pk=review.event_id)
    _organizer(actor, locked_event)
    locked_review = Review.objects.select_for_update().filter(pk=review.pk, event=locked_event).first()
    if locked_review is None:
        raise _not_found("review")
    exclusion = ReviewExclusion.objects.filter(review=locked_review).first()
    if exclusion is not None:
        reason = exclusion.reason
        exclusion.delete()
        audit.services.record(
            actor, "judging.review_included", event=locked_event, target=locked_review,
            summary=f"{_name(actor)} included the review of {locked_review.project.title}.",
            data={"previous_exclusion_reason": reason,
                  "publication_exists": locked_event.result_publications.exists()},
        )
    return locked_review
