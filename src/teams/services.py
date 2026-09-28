"""Teams, membership and invite links.

Writes and business rules live here. Views and API classes only call in here.

Check order is the same everywhere: authenticated, the event exists, the
submission window, then the rule that is specific to the call (BUILD-SEC
sections 2 and 5).
"""
from datetime import timedelta

import audit.services
from core.clock import now
from core.errors import ApiError
from core.ids import new_public_id
from django.db import transaction
from events.models import Event, EventRole, Role
from projects.models import ProjectStatus
# One window check, one envelope: importing the checker keeps a closed event
# answering the same 403 window_closed here as it does in projects.services.
from projects.services import check_submission_window
from teams.models import Team, TeamInvite, TeamMember
from teams.policy import is_team_member, team_of

INVITE_LIFETIME = timedelta(days=7)
NAME_MAX = 120


def _require_authenticated(actor):
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise ApiError("not_authenticated", "Sign in to take part in this event.", status_code=401)


def _require_no_other_role(actor, event: Event) -> None:
    """One role per person per event, so a judge can never also compete here."""
    existing = EventRole.objects.filter(event=event, user=actor).only("role").first()
    if existing is not None and existing.role != Role.PARTICIPANT:
        raise ApiError(
            "role_conflict",
            f"Your role in {event.name} is {existing.get_role_display().lower()}, so you "
            f"cannot also take part in it.",
            status_code=409,
        )


def _ensure_participant(actor, event: Event) -> EventRole:
    """Register the actor as a participant, or hand back the row that exists."""
    existing = EventRole.objects.filter(event=event, user=actor).first()
    if existing is not None:
        return existing
    role = EventRole.objects.create(
        event=event, user=actor, role=Role.PARTICIPANT,
        public_id=new_public_id("par"), added_by=actor,
    )
    audit.services.record(
        actor, "event.participant_registered", event=event, target=role,
        summary=f"{actor.display_name or actor.email.split('@')[0]} registered for {event.name}.",
    )
    return role


def _clean_name(name: str) -> str:
    clean = (name or "").strip()
    fields: dict[str, list[str]] = {}
    if not clean:
        fields["name"] = ["Give your team a name."]
    elif len(clean) > NAME_MAX:
        fields["name"] = [f"Keep the name under {NAME_MAX} characters."]
    if fields:
        raise ApiError("invalid", "Please correct the highlighted fields.", fields=fields)
    return clean


def _locked_event(event: Event) -> Event:
    return Event.objects.select_for_update().get(pk=event.pk)


def _locked_team(team_pk: int) -> Team | None:
    """Lock the team's event, then the team: the order every team write takes.

    projects.services locks in the same order, so two writers never wait on
    each other crosswise, and the event lock is what stops two teams of one
    event admitting the same person at once. None when the team is gone.
    """
    event_id = Team.objects.filter(pk=team_pk).values_list("event_id", flat=True).first()
    if event_id is None:
        return None
    event = Event.objects.select_for_update().get(pk=event_id)
    team = Team.objects.select_for_update().filter(pk=team_pk).first()
    if team is not None:
        team.event = event
    return team


def _lock_team_or_404(team: Team) -> Team:
    locked = _locked_team(team.pk)
    if locked is None:
        raise ApiError("team_not_found", f"{team.name} no longer exists.", status_code=404)
    return locked


@transaction.atomic
def create_team(actor, event: Event, name: str) -> Team:
    """Create a team with the actor as its owner, registering them if needed."""
    _require_authenticated(actor)
    if event is None:
        raise ApiError("event_not_found", "That event does not exist.", status_code=404)
    locked = _locked_event(event)
    check_submission_window(locked)
    _require_no_other_role(actor, locked)
    if team_of(actor, locked) is not None:
        raise ApiError(
            "already_in_team",
            "You are already in a team for this event. Leave it before creating another.",
            status_code=409,
        )
    clean = _clean_name(name)
    if Team.objects.filter(event=locked, name__iexact=clean).exists():
        raise ApiError("invalid", f"{clean!r} is already taken in {locked.name}.",
                       fields={"name": ["Another team already uses this name."]})
    _ensure_participant(actor, locked)
    team = Team.objects.create(event=locked, name=clean, created_by=actor)
    TeamMember.objects.create(team=team, user=actor, event=locked, is_owner=True)
    audit.services.record(
        actor, "team.created", event=locked, target=team,
        summary=f"Created team {clean} in {locked.name}.",
        data={"members": 1},
    )
    return team


def invite_link(invite: TeamInvite) -> str:
    """The link a team shares.

    The token is a query parameter, never a path segment: the access log format
    records %(U)s only, so a path token would land in the log (BUILD-SEC 16).
    """
    return f"/invite?token={invite.token}"


def _invite_problem(invite: TeamInvite, at) -> str | None:
    """Why this invite cannot be used, or None when it can."""
    if invite.revoked_at is not None:
        return "This invite link was replaced or withdrawn."
    if invite.max_uses is not None and invite.use_count >= invite.max_uses:
        return "This invite link has been used as often as it allows."
    if invite.expires_at <= at:
        return "This invite link has expired."
    return None


def invite_problem(invite: TeamInvite) -> str | None:
    """Read-side twin of the accept check, so the page explains the same reasons."""
    return _invite_problem(invite, now())


@transaction.atomic
def rotate_invite(actor, team: Team) -> TeamInvite:
    """Mint a fresh invite link and revoke the one it replaces."""
    locked = _lock_team_or_404(team)
    at = now()
    # A closed window freezes the roster, so no link is minted for it; the old
    # one expired at the close and accept_invite refuses every link after it.
    check_submission_window(locked.event, at)
    if not is_team_member(actor, locked):
        raise ApiError("not_a_member", f"Only members of {locked.name} can share its invite link.",
                       status_code=403)
    # Rotating is the revocation: the old link stops working immediately, so a
    # link pasted into a chat cannot be used after the team owner changes their mind.
    TeamInvite.objects.filter(team=locked, revoked_at__isnull=True).update(revoked_at=at)
    # An invite cannot outlive the window it exists for.
    expires_at = min(at + INVITE_LIFETIME, locked.event.submissions_close_at)
    invite = TeamInvite.objects.create(team=locked, created_by=actor, expires_at=expires_at)
    audit.services.record(
        actor, "team.invite_rotated", event=locked.event, target=locked,
        summary=f"Rotated the invite link for {locked.name}; the previous link no longer works.",
        data={"expires_at": expires_at.isoformat()},
    )
    return invite


@transaction.atomic
def accept_invite(actor, token: str) -> TeamMember:
    """Join the team an invite points at, under one lock and one transaction."""
    _require_authenticated(actor)
    # The first read only finds the team. The event, the team and then the
    # invite are locked in that order and the invite is read again under its
    # lock, so a link rotated or used up while this request waited is refused.
    found = TeamInvite.objects.filter(token=(token or "").strip()).only("team").first()
    team = _locked_team(found.team_id) if found is not None else None
    invite = (TeamInvite.objects.select_for_update().filter(pk=found.pk).first()
              if team is not None else None)
    if invite is None:
        raise ApiError("invite_invalid", "That invite link is not valid.", status_code=410)
    at = now()
    problem = _invite_problem(invite, at)
    if problem is not None:
        raise ApiError("invite_invalid", problem, status_code=410)
    event = team.event
    check_submission_window(event, at)
    _require_no_other_role(actor, event)
    if team_of(actor, event) is not None:
        raise ApiError("already_in_team", "You are already in a team for this event.",
                       status_code=409)
    if team.memberships.count() >= event.max_team_size:
        raise ApiError(
            "team_full",
            f"{team.name} already has its {event.max_team_size} members.",
            status_code=409,
        )
    _ensure_participant(actor, event)
    member = TeamMember.objects.create(team=team, user=actor, event=event, is_owner=False)
    TeamInvite.objects.filter(pk=invite.pk).update(use_count=invite.use_count + 1)
    audit.services.record(
        actor, "team.member_joined", event=event, target=team,
        summary=f"Joined {team.name} with an invite link.",
        data={"member": actor.public_id},
    )
    return member


@transaction.atomic
def leave_team(actor, team: Team) -> None:
    """Leave a team, handing ownership on; the last member out takes an empty team with them."""
    _require_authenticated(actor)
    locked = _lock_team_or_404(team)
    member = locked.memberships.filter(user=actor).first()
    check_submission_window(locked.event)
    if member is None:
        raise ApiError("not_a_member", f"You are not a member of {locked.name}.", status_code=403)
    remaining = locked.memberships.exclude(pk=member.pk).order_by("joined_at", "id")
    heir = remaining.select_related("user").first()
    if heir is None:
        _remove_empty_team(locked, actor)
        return
    if member.is_owner:
        heir.is_owner = True
        heir.save(update_fields=["is_owner"])
    member.delete()
    audit.services.record(
        actor, "team.member_left", event=locked.event, target=locked,
        summary=(f"Left {locked.name}."
                 + (f" Ownership passed to {heir.user.display_name or heir.user.email}."
                    if member.is_owner else "")),
        data={"owner_transferred": member.is_owner},
    )


def _remove_empty_team(team: Team, actor) -> None:
    """Delete a team whose last member left, but never one that holds a record."""
    submitted = team.projects.filter(status=ProjectStatus.SUBMITTED).first()
    if submitted is not None:
        raise ApiError(
            "withdraw_first",
            f"{team.name} still has a submitted project. Withdraw it before leaving.",
            status_code=409,
        )
    if team.projects.exclude(status=ProjectStatus.DRAFT).exists():
        raise ApiError(
            "team_not_deletable",
            f"{team.name} has withdrawn or disqualified projects, and that history is kept. "
            f"Ask an organizer to remove the record.",
            status_code=409,
        )
    name = team.name
    event = team.event
    team.delete()
    audit.services.record(
        actor, "team.deleted", event=event, target=event,
        summary=f"Deleted the empty team {name} after its last member left.",
    )


@transaction.atomic
def remove_member(actor, team: Team, user) -> None:
    """The owner removes somebody else from the team."""
    locked = _lock_team_or_404(team)
    check_submission_window(locked.event)
    owner = locked.memberships.filter(user=actor, is_owner=True).first()
    if owner is None:
        raise ApiError("not_team_owner", f"Only the owner of {locked.name} can remove members.",
                       status_code=403)
    if user is not None and getattr(user, "pk", None) == actor.pk:
        raise ApiError(
            "owner_cannot_be_removed",
            "Owners leave with the leave action, which passes ownership to the earliest "
            "remaining member.",
            status_code=400,
        )
    target = locked.memberships.filter(user=user).first()
    if target is None:
        raise ApiError("member_not_found", f"{user} is not a member of {locked.name}.",
                       status_code=404)
    label = target.user.display_name or target.user.email.split("@")[0]
    target.delete()
    audit.services.record(
        actor, "team.member_removed", event=locked.event, target=locked,
        summary=f"Removed {label} from {locked.name}.",
        data={"member": user.public_id},
    )
