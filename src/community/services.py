"""Voting and commenting business rules; write paths and auditing live here."""
import hashlib
import hmac
import random
import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.mail import send_mail
from django.db import models, transaction

import audit.services
from community import policy
from community.models import (
    AbuseFlag,
    Ballot,
    BallotItem,
    Comment,
    Voter,
    VoterKind,
    VotingAccess,
    VotingConfig,
    VotingStyle,
)
from core.clock import now
from core.errors import ApiError
from events.models import Event, EventRole, Role
from events.policy import is_organizer
from projects.models import Project
from projects.policy import public_projects

EMAIL_SALT = "community.email-voter"
EMAIL_TICKET_MAX_AGE = 60 * 60 * 24
IP_BALLOTS_PER_HOUR = 30
CHANGES_PER_HOUR = 10
BURST_VOTERS = 10


def normalize_email(email: str) -> str:
    """Normalize case and Gmail plus-addresses; the latter is a heuristic only."""
    value = email.strip().lower()
    if "@" not in value:
        raise ApiError("invalid", "Enter a valid email address.", status_code=400,
                       fields={"email": ["Enter a valid email address."]})
    local, domain = value.rsplit("@", 1)
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def _secret_hash(value: str) -> str:
    return hashlib.sha256((value + settings.SECRET_KEY).encode("utf-8")).hexdigest()


def _request_ip_hash(request) -> str:
    return audit.services.hash_ip(request.META.get("REMOTE_ADDR")) or ""


def _get_config(event: Event) -> VotingConfig:
    try:
        return event.voting_config
    except VotingConfig.DoesNotExist:
        raise ApiError("voting_unavailable", "Voting is not configured for this event.",
                       status_code=404) from None


def _require_window(event: Event) -> None:
    current = now()
    if (event.voting_open_at is None or event.voting_close_at is None
            or not event.voting_open_at <= current < event.voting_close_at):
        raise ApiError("voting_closed", "Voting is not open.", status_code=403)
    if not event.gallery_public:
        raise ApiError("gallery_unpublished", "Voting requires a published gallery.",
                       status_code=403)


def _ensure_can_vote(user: Any, event: Event) -> None:
    if not getattr(user, "is_authenticated", False):
        return
    if is_organizer(user, event) or EventRole.objects.filter(
        event=event, user=user, role=Role.JUDGE
    ).exists():
        raise ApiError("role_conflict", "Judges and organizers cannot vote in this event.",
                       status_code=409)


def _throttle_ip(event: Event, ip_hash: str) -> None:
    if ip_hash and Voter.objects.filter(
        event=event, ip_hash=ip_hash, created_at__gte=now() - timedelta(hours=1)
    ).count() >= IP_BALLOTS_PER_HOUR:
        raise ApiError("throttled", "Too many ballots were created from this network.",
                       status_code=429)


def _record_new_voter(voter: Voter, request: Any) -> None:
    if not voter.ip_hash:
        return
    ten_minutes_ago = now() - timedelta(minutes=10)
    count = Voter.objects.filter(
        event=voter.event, ip_hash=voter.ip_hash, created_at__gte=ten_minutes_ago
    ).count()
    if count > BURST_VOTERS and not AbuseFlag.objects.filter(
        event=voter.event, kind="ip_burst", subject=voter.ip_hash,
        created_at__gte=ten_minutes_ago,
    ).exists():
        flag = AbuseFlag.objects.create(
            event=voter.event,
            kind="ip_burst",
            subject=voter.ip_hash,
            detail={"new_voters_10m": count},
        )
        audit.services.record(
            None, "community.abuse_flag.created", event=voter.event, target=flag,
            summary="A burst of voters from one network was flagged.",
            data={"kind": flag.kind, "count": count}, request=request,
        )


def _make_voter(event: Event, kind: VoterKind, request: Any,
                **identity: Any) -> tuple[Voter, Ballot]:
    ip_hash = _request_ip_hash(request)
    _throttle_ip(event, ip_hash)
    voter = Voter.objects.create(
        event=event, kind=kind, ip_hash=ip_hash, **identity
    )
    ballot = Ballot.objects.create(voter=voter)
    _record_new_voter(voter, request)
    audit.services.record(
        request.user if getattr(request.user, "is_authenticated", False) else None,
        "community.ballot.created",
        event=event, target=ballot, summary="A community ballot was created.",
        data={"voter": voter.public_id, "kind": kind}, request=request,
    )
    return voter, ballot


def _existing_voter(event: Event, **identity: Any) -> Voter | None:
    return Voter.objects.filter(event=event, **identity).select_for_update().first()


def _reject_duplicate(voter: Voter | None) -> None:
    if voter is not None and hasattr(voter, "ballot"):
        raise ApiError("duplicate_voter", "A ballot already exists for this voter.",
                       status_code=409, fields={"ballot": [voter.ballot.public_id]})


def _get_or_create_ballot(event: Event, user: Any, request: Any, *,
                          link_token: str = "", device_token: str = "",
                          email_ticket: str = "") -> Ballot:
    config = _get_config(event)
    _require_window(event)
    _ensure_can_vote(user, event)

    if config.access == VotingAccess.AUTHENTICATED:
        if not getattr(user, "is_authenticated", False):
            raise ApiError("not_authenticated", "Sign in to vote.", status_code=401)
        identity = {"user": user}
        kind = VoterKind.USER
        voter = _existing_voter(event, **identity)
        _reject_duplicate(voter)
        return _make_voter(event, kind, request, **identity)[1]

    if config.access == VotingAccess.OPEN_LINK:
        if not link_token or not secrets_compare(link_token, config.link_token):
            raise ApiError("invalid_link", "A valid voting link is required.", status_code=403)
        if not device_token:
            raise ApiError("device_required", "A voting device cookie is required.",
                           status_code=400)
        identity = {"device_hash": _secret_hash(device_token)}
        voter = _existing_voter(event, **identity)
        _reject_duplicate(voter)
        return _make_voter(event, VoterKind.DEVICE, request, **identity)[1]

    if not email_ticket:
        raise ApiError("email_verification_required", "Verify your email before voting.",
                       status_code=401)
    try:
        payload = signing.loads(email_ticket, salt=EMAIL_SALT, max_age=EMAIL_TICKET_MAX_AGE)
    except signing.BadSignature as exc:
        raise ApiError("invalid_verification", "This email verification link is invalid or expired.",
                       status_code=400) from exc
    if payload.get("event") != event.slug:
        raise ApiError("invalid_verification", "This verification link is for another event.",
                       status_code=400)
    email_hash = str(payload.get("email_hash", ""))
    if not email_hash:
        raise ApiError("invalid_verification", "This email verification link is invalid.",
                       status_code=400)
    voter = _existing_voter(event, email_hash=email_hash)
    _reject_duplicate(voter)
    return _make_voter(
        event, VoterKind.EMAIL, request, email_hash=email_hash,
        verified_at=now(),
    )[1]


def secrets_compare(first: str, second: str) -> bool:
    """Compare capability tokens in constant time."""
    return hmac.compare_digest(first, second)


def request_email_verification(event: Event, email: str, request: Any) -> None:
    with transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        config = _get_config(locked_event)
        _require_window(locked_event)
        if config.access != VotingAccess.EMAIL:
            raise ApiError("invalid_access_mode", "This event does not use email voting.",
                           status_code=400)
        _throttle_ip(locked_event, _request_ip_hash(request))
        normalized = normalize_email(email)
        email_hash = _secret_hash(normalized)
        existing = _existing_voter(locked_event, email_hash=email_hash)
        _reject_duplicate(existing)
        ticket = signing.dumps(
            {"event": locked_event.slug, "email_hash": email_hash}, salt=EMAIL_SALT
        )
        link = request.build_absolute_uri(
            f"/events/{locked_event.slug}/vote/verify?token={ticket}"
        )
        send_mail(
            subject=f"Verify your vote for {locked_event.name}",
            message=f"Open this link to verify your email and vote: {link}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[normalized],
            fail_silently=False,
        )
        audit.services.record(
            None, "community.voter.verification_sent", event=locked_event,
            summary="An email voting verification link was sent.",
            data={"email_hash": email_hash[:16]}, request=request,
        )


def configure_voting(event: Event, user: Any, data: dict[str, Any],
                     request: Any) -> VotingConfig:
    """Create or change voting rules only before any voter identity is recorded."""
    with transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        policy.require_manager(user, locked_event)
        config = VotingConfig.objects.filter(event=locked_event).first()
        allowed = {
            "access", "style", "credits", "max_votes_per_project", "rotate_link",
            "voting_open_at", "voting_close_at",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ApiError("invalid", "Unsupported voting configuration field.",
                           fields={key: ["Unknown field."] for key in sorted(unknown)})
        if data.get("access", config.access if config else VotingAccess.OPEN_LINK) not in VotingAccess.values:
            raise ApiError("invalid", "Choose a supported voting access mode.",
                           fields={"access": ["Invalid access mode."]})
        if data.get("style", config.style if config else VotingStyle.SINGLE) not in VotingStyle.values:
            raise ApiError("invalid", "Choose a supported ballot style.",
                           fields={"style": ["Invalid ballot style."]})
        for field in ("credits", "max_votes_per_project"):
            value = data.get(field, getattr(config, field, 16 if field == "credits" else 1))
            if type(value) is not int or value < 1:
                raise ApiError("invalid", f"{field} must be a positive integer.",
                               fields={field: ["Enter a positive integer."]})
        effective_style = data.get("style", config.style if config else VotingStyle.SINGLE)
        effective_limit = data.get(
            "max_votes_per_project", getattr(config, "max_votes_per_project", 1)
        )
        if effective_style == VotingStyle.SINGLE and effective_limit != 1:
            raise ApiError("invalid", "Single voting allows one vote per project.",
                           fields={"max_votes_per_project": ["Set the limit to one."]})
        opens_at = data.get("voting_open_at", locked_event.voting_open_at)
        closes_at = data.get("voting_close_at", locked_event.voting_close_at)
        if ((opens_at is None) != (closes_at is None)
                or (opens_at is not None and opens_at >= closes_at)):
            raise ApiError("invalid", "Voting needs either no dates or an ordered open/close pair.",
                           fields={"voting_close_at": ["The close must be after the open."]})
        voters_exist = Voter.objects.filter(event=locked_event).exists()
        if voters_exist:
            immutable_fields = {"access", "style", "credits", "max_votes_per_project"}
            if (config is not None and any(
                    data.get(key, getattr(config, key)) != getattr(config, key)
                    for key in immutable_fields if key in data
            )):
                raise ApiError("voting_already_started",
                               "Voting rules cannot change after the first voter is recorded.",
                               status_code=409)
            if opens_at is None or closes_at is None:
                raise ApiError("voting_already_started",
                               "Voting dates cannot be cleared after the first ballot.",
                               status_code=409)
        if config is None:
            config = VotingConfig(event=locked_event)
        for field in ("access", "style", "credits", "max_votes_per_project"):
            if field in data:
                setattr(config, field, data[field])
        if data.get("rotate_link") is True:
            config.link_token = secrets.token_urlsafe(32)
        config.save()
        window_changes = {
            field: value for field, value in (
                ("voting_open_at", opens_at), ("voting_close_at", closes_at)
            ) if value != getattr(locked_event, field)
        }
        if window_changes:
            locked_event.voting_open_at = opens_at
            locked_event.voting_close_at = closes_at
            locked_event.save(update_fields=[
                "voting_open_at", "voting_close_at", "updated_at"
            ])
        audit.services.record(
            user, "community.voting.configured", event=locked_event, target=locked_event,
            summary="Voting configuration was updated.",
            data={
                "access": config.access,
                "style": config.style,
                "credits": config.credits,
                "max_votes_per_project": config.max_votes_per_project,
                "link_rotated": data.get("rotate_link") is True,
                "voting_open_at": opens_at.isoformat() if opens_at else None,
                "voting_close_at": closes_at.isoformat() if closes_at else None,
            }, request=request,
        )
        return config


def ballot_order(ballot: Ballot) -> list[Project]:
    """Shuffle the current public project list from the ballot's stored seed."""
    projects = list(public_projects(ballot.voter.event).order_by("public_id"))
    random.Random(ballot.order_seed).shuffle(projects)
    return projects


def create_ballot(event: Event, user: Any, request: Any, *, link_token: str = "",
                  device_token: str = "", email_ticket: str = "") -> Ballot:
    """Create exactly one ballot for an identity; duplicates are explicit conflicts."""
    with transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        return _get_or_create_ballot(
            locked_event, user, request, link_token=link_token,
            device_token=device_token, email_ticket=email_ticket,
        )


def current_ballot(event: Event, user: Any, request: Any, *, link_token: str = "",
                   device_token: str = "", email_ticket: str = "") -> Ballot:
    """Resolve the caller's existing ballot without revealing another identity."""
    config = _get_config(event)
    voter = _voter_for_existing_ballot(
        event, config, user, request, link_token=link_token,
        device_token=device_token, email_ticket=email_ticket,
    )
    ballot = Ballot.objects.filter(voter=voter).first()
    if ballot is None:
        raise ApiError("ballot_not_found", "No ballot exists for this voter.", status_code=404)
    return ballot


def _voter_for_existing_ballot(event: Event, config: VotingConfig, user: Any,
                               request: Any, *,
                               link_token: str,
                               device_token: str, email_ticket: str) -> Voter:
    _require_window(event)
    _ensure_can_vote(user, event)
    if config.access == VotingAccess.AUTHENTICATED:
        if not getattr(user, "is_authenticated", False):
            raise ApiError("not_authenticated", "Sign in to vote.", status_code=401)
        identity = {"user": user}
    elif config.access == VotingAccess.OPEN_LINK:
        if not link_token or not secrets_compare(link_token, config.link_token):
            raise ApiError("invalid_link", "A valid voting link is required.", status_code=403)
        if not device_token:
            raise ApiError("device_required", "A voting device cookie is required.",
                           status_code=400)
        identity = {"device_hash": _secret_hash(device_token)}
    else:
        if not email_ticket:
            raise ApiError("email_verification_required", "Verify your email before voting.",
                           status_code=401)
        try:
            payload = signing.loads(email_ticket, salt=EMAIL_SALT,
                                    max_age=EMAIL_TICKET_MAX_AGE)
        except signing.BadSignature as exc:
            raise ApiError("invalid_verification", "This email verification link is invalid or expired.",
                           status_code=400) from exc
        if payload.get("event") != event.slug or not payload.get("email_hash"):
            raise ApiError("invalid_verification", "This verification link is for another event.",
                           status_code=400)
        identity = {"email_hash": str(payload["email_hash"])}
    voter = Voter.objects.filter(event=event, **identity).first()
    if voter is None:
        raise ApiError("ballot_not_found", "Create a ballot before submitting votes.",
                       status_code=404)
    return voter


def _ballot_changes_since(ballot: Ballot) -> int:
    from audit.models import AuditEvent
    return AuditEvent.objects.filter(
        event=ballot.voter.event, target_id=ballot.public_id,
        action="community.ballot.changed", created_at__gte=now() - timedelta(hours=1),
    ).count()


def submit_ballot(event: Event, public_id: str, user: Any, request: Any,
                  items: list[dict[str, Any]], *,
                  link_token: str = "", device_token: str = "",
                  email_ticket: str = "") -> Ballot:
    """Create or update one ballot under event and ballot locks."""
    if not isinstance(items, list):
        raise ApiError("invalid", "Ballot items must be a list.", fields={"items": ["Invalid list."]})
    with transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        config = _get_config(locked_event)
        voter = _voter_for_existing_ballot(
            locked_event, config, user, request, link_token=link_token,
            device_token=device_token, email_ticket=email_ticket,
        )
        ballot = Ballot.objects.filter(voter=voter, public_id=public_id).first()
        if ballot is None:
            raise ApiError("ballot_not_found", "No ballot was found for this voter.",
                           status_code=404)
        ballot = Ballot.objects.select_for_update().select_related("voter").get(pk=ballot.pk)
        if ballot.voided_at is not None:
            raise ApiError("ballot_voided", "This ballot has been voided.", status_code=409)
        if _ballot_changes_since(ballot) >= CHANGES_PER_HOUR:
            raise ApiError("throttled", "Too many ballot changes; try again later.",
                           status_code=429)

        seen: set[str] = set()
        normalized: list[tuple[Project, int]] = []
        project_ids = []
        for row in items:
            if not isinstance(row, dict) or not isinstance(row.get("project"), str):
                raise ApiError("invalid", "Each ballot item needs a project id and vote count.")
            project_id = row["project"]
            votes = row.get("votes")
            if type(votes) is not int or votes < 0 or votes > 2_147_483_647:
                raise ApiError("invalid", "Votes must be a supported non-negative integer.",
                               fields={"votes": ["Enter a non-negative integer."]})
            if project_id in seen:
                raise ApiError("invalid", "Each project may appear once on a ballot.")
            seen.add(project_id)
            project_ids.append(project_id)
        projects = {
            project.public_id: project
            for project in public_projects(locked_event).filter(public_id__in=project_ids)
        }
        if len(projects) != len(project_ids):
            raise ApiError("project_not_found", "A project is not in this event's public gallery.",
                           status_code=404)
        for row in items:
            project_id = row["project"]
            votes = row["votes"]
            project = projects[project_id]
            if (config.style == VotingStyle.SINGLE
                    and votes > config.max_votes_per_project):
                raise ApiError("vote_limit", "The per-project vote limit was exceeded.",
                               fields={"votes": [project_id]})
            normalized.append((project, votes))
        if config.style == VotingStyle.QUADRATIC and sum(votes * votes for _, votes in normalized) > config.credits:
            raise ApiError("budget_exceeded", "The quadratic ballot exceeds its credit budget.",
                           fields={"credits": [f"Budget is {config.credits}."]})

        old_items = {item.project_id: item for item in ballot.items.select_for_update()}
        for project, votes in normalized:
            if votes:
                BallotItem.objects.update_or_create(
                    ballot=ballot, project=project, defaults={"votes": votes}
                )
            else:
                BallotItem.objects.filter(ballot=ballot, project=project).delete()
            old_items.pop(project.pk, None)
        for item in old_items.values():
            item.delete()
        ballot.submitted_at = now()
        ballot.save(update_fields=["submitted_at"])
        audit.services.record(
            user if getattr(user, "is_authenticated", False) else None,
            "community.ballot.changed", event=locked_event, target=ballot,
            summary="A community ballot was submitted or changed.",
            data={"voter": ballot.voter.public_id, "item_count": len(normalized)},
            request=request,
        )
        return ballot


def _comment_rate_limit(user: Any) -> None:
    from audit.models import AuditEvent
    if AuditEvent.objects.filter(
        actor=user, action="community.comment.created",
        created_at__gte=now() - timedelta(minutes=10),
    ).count() >= 5:
        raise ApiError("throttled", "You may post at most five comments every ten minutes.",
                       status_code=429)


def create_comment(project: Project, user: Any, body: str, request: Any) -> Comment:
    if not getattr(user, "is_authenticated", False):
        raise ApiError("not_authenticated", "Sign in to comment.", status_code=401)
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        locked_project = Project.objects.select_for_update().select_related("event").get(pk=project.pk)
        if locked_project.status != "submitted" or not locked_project.event.gallery_public:
            raise ApiError("project_not_found", "Comments are not available for this project.",
                           status_code=404)
        _comment_rate_limit(user)
        text = body.strip()
        if not text or len(text) > 1000:
            raise ApiError("invalid", "Comments must contain 1 to 1000 characters.",
                           fields={"body": ["Enter between 1 and 1000 characters."]})
        comment = Comment.objects.create(project=locked_project, author=user, body=text)
        audit.services.record(
            user, "community.comment.created", event=locked_project.event, target=comment,
            summary="A project comment was posted.",
            data={"project": locked_project.public_id}, request=request,
        )
        return comment


def moderate_comment(comment: Comment, user: Any, reason: str, request: Any, *,
                     restore: bool) -> Comment:
    with transaction.atomic():
        locked = Comment.objects.select_for_update().select_related("project__event").get(
            pk=comment.pk
        )
        policy.require_manager(user, locked.project.event)
        text = reason.strip()
        if not text or len(text) > 300:
            raise ApiError("invalid", "A moderation reason of 1 to 300 characters is required.",
                           fields={"reason": ["Enter a moderation reason."]})
        locked.hidden_at = None if restore else now()
        locked.hidden_by = None if restore else user
        locked.hide_reason = "" if restore else text
        locked.save(update_fields=["hidden_at", "hidden_by", "hide_reason"])
        action = "community.comment.restored" if restore else "community.comment.hidden"
        audit.services.record(
            user, action, event=locked.project.event, target=locked,
            summary="A project comment was restored." if restore else "A project comment was hidden.",
            data={"project": locked.project.public_id, "reason": text}, request=request,
        )
        return locked


def moderate_ballot(ballot: Ballot, user: Any, reason: str, request: Any, *,
                    restore: bool) -> Ballot:
    with transaction.atomic():
        locked = Ballot.objects.select_for_update().select_related(
            "voter__event"
        ).get(pk=ballot.pk)
        policy.require_manager(user, locked.voter.event)
        text = reason.strip()
        if not text or len(text) > 300:
            raise ApiError("invalid", "A reason of 1 to 300 characters is required.",
                           fields={"reason": ["Enter a reason."]})
        if restore and locked.voided_at is None:
            raise ApiError("ballot_not_voided", "This ballot is not voided.", status_code=409)
        if not restore and locked.voided_at is not None:
            raise ApiError("ballot_already_voided", "This ballot is already voided.", status_code=409)
        locked.voided_at = None if restore else now()
        locked.void_reason = "" if restore else text
        locked.save(update_fields=["voided_at", "void_reason"])
        audit.services.record(
            user, "community.ballot.restored" if restore else "community.ballot.voided",
            event=locked.voter.event, target=locked,
            summary="A ballot was restored." if restore else "A ballot was voided.",
            data={"voter": locked.voter.public_id, "reason": text}, request=request,
        )
        return locked


def tallies_for_event(user: Any, event: Event) -> list[dict[str, Any]]:
    """Return aggregate votes only after the policy has authorized result visibility."""
    ballot_items = policy.visible_tallies(user, event)
    totals = {
        row["project_id"]: {
            "votes": row["total_votes"] or 0,
            "credits": row["credits"] or 0,
        }
        for row in ballot_items.values("project_id").annotate(
            total_votes=models.Sum("votes"),
            credits=models.Sum(models.F("votes") * models.F("votes")),
        )
    }
    rows = []
    for project in public_projects(event).values("id", "public_id", "title"):
        total = totals.get(project["id"], {})
        rows.append({
            "project__public_id": project["public_id"],
            "project__title": project["title"],
            "votes": total.get("votes", 0) or 0,
            "credits": total.get("credits", 0) or 0,
        })
    return sorted(rows, key=lambda row: (-row["votes"], row["project__title"],
                                         row["project__public_id"]))
