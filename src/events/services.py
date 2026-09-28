"""Events, tracks, prizes, custom questions and per-event roles.

Writes and business rules live here. Views and API classes only call in here.
"""
from decimal import Decimal

import audit.services
from accounts.models import User
from core.clock import now
from core.errors import ApiError
from core.ids import new_public_id
from django.db import IntegrityError, transaction
from django.utils.text import slugify
from events.models import CustomQuestion, Event, EventRole, Prize, RankingMethod, Role, Track

EVENT_FIELDS = {
    "name", "tagline", "description", "submissions_open_at", "submissions_close_at",
    "judging_open_at", "judging_close_at", "max_team_size", "reviews_per_project",
    "judging_mode", "ranking_method", "shrinkage_lambda", "gallery_public",
    "one_prize_per_team", "pairwise_min_comparisons",
}
WINDOW_FIELDS = {"submissions_open_at", "submissions_close_at", "judging_open_at", "judging_close_at"}
LOCKED_FIELDS = {"ranking_method", "shrinkage_lambda", "pairwise_min_comparisons", "judging_mode"}


def _require_authenticated(actor):
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise ApiError("not_authenticated", "Authentication is required.", status_code=401)


def _can_manage(actor, event: Event) -> bool:
    if actor is None or not getattr(actor, "is_authenticated", False):
        return False
    if actor.is_admin:
        return True
    return EventRole.objects.filter(event=event, user=actor, role=Role.ORGANIZER).exists()


def _require_manager(actor, event: Event):
    _require_authenticated(actor)
    if not _can_manage(actor, event):
        raise ApiError("forbidden", "Only organizers of this event can manage it.", status_code=403)


def _validate_event_fields(data: dict) -> None:
    close = data.get("submissions_close_at")
    open_at = data.get("submissions_open_at")
    judging_open = data.get("judging_open_at")
    judging_close = data.get("judging_close_at")
    fields = {}
    if close is None:
        fields["submissions_close_at"] = ["This field is required."]
    if open_at is not None and close is not None and open_at >= close:
        fields["submissions_open_at"] = ["Submissions must open before they close."]
    if judging_open is not None and close is not None and judging_open < close:
        fields["judging_open_at"] = ["Judging cannot start before submissions close."]
    if judging_close is not None and judging_open is not None and judging_close <= judging_open:
        fields["judging_close_at"] = ["Judging must close after it opens."]
    max_team_size = data.get("max_team_size")
    if max_team_size is not None and not 1 <= int(max_team_size) <= 10:
        fields["max_team_size"] = ["Must be between 1 and 10."]
    reviews_per_project = data.get("reviews_per_project")
    if reviews_per_project is not None and int(reviews_per_project) < 1:
        fields["reviews_per_project"] = ["Must be at least 1."]
    minimum = data.get("pairwise_min_comparisons")
    if minimum is not None and not 1 <= int(minimum) <= 32767:
        fields["pairwise_min_comparisons"] = ["Must be between 1 and 32767."]
    if fields:
        raise ApiError("invalid", "Please correct the highlighted fields.", status_code=400, fields=fields)


def _unique_slug(name: str) -> str:
    base = slugify(name)[:70].strip("-") or "event"
    slug = base
    suffix = 2
    while Event.objects.filter(slug=slug).exists():
        tail = f"-{suffix}"
        slug = f"{base[:80 - len(tail)]}{tail}"
        suffix += 1
    return slug


def _serial_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _window_change_data(old: dict, event: Event, changed: set[str]) -> dict:
    return {
        field: {"old": _serial_value(old[field]), "new": _serial_value(getattr(event, field))}
        for field in sorted(changed & WINDOW_FIELDS)
    }


def _audit_event_update(actor, event: Event, old: dict, changed: set[str]) -> None:
    if not changed:
        return
    window_changes = _window_change_data(old, event, changed)
    if window_changes:
        pieces = [f"{field}: {values['old']} -> {values['new']}" for field, values in window_changes.items()]
        summary = f"Updated {event.name} windows ({'; '.join(pieces)})."
    else:
        summary = f"Updated {event.name}."
    audit.services.record(
        actor, "event.updated", event=event, target=event, summary=summary,
        data={field: {"old": _serial_value(old[field]), "new": _serial_value(getattr(event, field))}
              for field in sorted(changed)},
    )


@transaction.atomic
def create_event(actor, data: dict) -> Event:
    _require_authenticated(actor)
    if not actor.is_admin and not actor.is_host:
        raise ApiError("forbidden", "Only hosts and admins can create events.", status_code=403)
    values = {field: data[field] for field in EVENT_FIELDS if field in data}
    _validate_event_fields(values)
    event = Event.objects.create(slug=_unique_slug(values["name"]), created_by=actor, **values)
    role = EventRole.objects.create(
        event=event, user=actor, role=Role.ORGANIZER, public_id=new_public_id("org"), added_by=actor
    )
    audit.services.record(
        actor, "event.created", event=event, target=event,
        summary=f"Created event {event.name} and made {actor.display_name or actor.email} an organizer.",
        data={"organizer": role.public_id},
    )
    return event


@transaction.atomic
def update_event(actor, event: Event, data: dict) -> Event:
    locked = Event.objects.select_for_update().get(pk=event.pk)
    _require_manager(actor, locked)
    values = {field: data[field] for field in EVENT_FIELDS if field in data}
    if not values:
        return locked
    merged = {field: getattr(locked, field) for field in EVENT_FIELDS}
    merged.update(values)
    if locked.scoring_locked_at is not None:
        submission_changes = {"submissions_open_at", "submissions_close_at"} & set(values)
        if any(getattr(locked, field) != values[field] for field in submission_changes):
            raise ApiError("scoring_locked", "Submission windows are locked after scoring starts.", status_code=409)
        if any(getattr(locked, field) != values[field] for field in LOCKED_FIELDS & set(values)):
            raise ApiError("scoring_locked", "Scoring settings are locked after scoring starts.", status_code=409)
    _validate_event_fields(merged)
    old = {field: getattr(locked, field) for field in EVENT_FIELDS}
    changed = {field for field, value in values.items() if getattr(locked, field) != value}
    for field in changed:
        setattr(locked, field, values[field])
    if changed:
        locked.save(update_fields=sorted(changed) + ["updated_at"])
        _audit_event_update(actor, locked, old, changed)
    return locked


@transaction.atomic
def close_judging(actor, event: Event) -> Event:
    locked = Event.objects.select_for_update().get(pk=event.pk)
    _require_manager(actor, locked)
    old = locked.judging_close_at
    locked.judging_close_at = now()
    locked.save(update_fields=["judging_close_at", "updated_at"])
    audit.services.record(
        actor, "event.judging_closed", event=locked, target=locked,
        summary=f"Closed judging for {locked.name}: {old} -> {locked.judging_close_at}.",
        data={"judging_close_at": {"old": _serial_value(old), "new": _serial_value(locked.judging_close_at)}},
    )
    return locked


@transaction.atomic
def close_submissions(actor, event: Event) -> Event:
    locked = Event.objects.select_for_update().get(pk=event.pk)
    _require_manager(actor, locked)
    if locked.scoring_locked_at is not None:
        raise ApiError("scoring_locked", "Submission windows are locked after scoring starts.", status_code=409)
    if not _submission_window_open(locked):
        raise ApiError("window_closed", f"Submissions for {locked.name} are not open.", status_code=403)
    old = {"submissions_close_at": locked.submissions_close_at, "judging_open_at": locked.judging_open_at}
    closed_at = now()
    locked.submissions_close_at = closed_at
    locked.judging_open_at = closed_at
    locked.save(update_fields=["submissions_close_at", "judging_open_at", "updated_at"])
    audit.services.record(
        actor, "event.submissions_closed", event=locked, target=locked,
        summary=f"Closed submissions for {locked.name}: {old['submissions_close_at']} -> {closed_at}.",
        data={"submissions_close_at": {"old": _serial_value(old["submissions_close_at"]),
                                        "new": _serial_value(closed_at)},
              "judging_open_at": {"old": _serial_value(old["judging_open_at"]),
                                  "new": _serial_value(closed_at)}},
    )
    return locked


def _submission_window_open(event: Event) -> bool:
    at = now()
    if event.submissions_open_at is not None and at < event.submissions_open_at:
        return False
    return at < event.submissions_close_at


def _window_closed(event: Event) -> ApiError:
    return ApiError(
        "window_closed",
        f"Submissions for {event.name} closed at {event.submissions_close_at.isoformat()}.",
        status_code=403,
        fields={"submissions_close_at": event.submissions_close_at.isoformat()},
    )


@transaction.atomic
def create_track(actor, event: Event, data: dict) -> Track:
    _require_manager(actor, event)
    track = Track.objects.create(
        event=event,
        name=data.get("name", "").strip(),
        description=data.get("description", ""),
        position=data.get("position", 0),
    )
    audit.services.record(actor, "event.track_created", event=event, target=track,
                          summary=f"Created track {track.name} in {event.name}.")
    return track


@transaction.atomic
def update_track(actor, event: Event, track: Track, data: dict) -> Track:
    _require_manager(actor, event)
    locked = Track.objects.select_for_update().get(pk=track.pk, event=event)
    changed = []
    for field in ("name", "description", "position"):
        if field in data and getattr(locked, field) != data[field]:
            setattr(locked, field, data[field])
            changed.append(field)
    if changed:
        locked.save(update_fields=changed)
        audit.services.record(actor, "event.track_updated", event=event, target=locked,
                              summary=f"Updated track {locked.name} in {event.name}.",
                              data={"fields": changed})
    return locked


@transaction.atomic
def delete_track(actor, event: Event, track: Track) -> None:
    _require_manager(actor, event)
    locked = Track.objects.select_for_update().get(pk=track.pk, event=event)
    if locked.projects.exists():
        raise ApiError("track_in_use", "A track with projects cannot be deleted.", status_code=409)
    name = locked.name
    public_id = locked.public_id
    locked.delete()
    audit.services.record(actor, "event.track_deleted", event=event, target=public_id,
                          summary=f"Deleted track {name} from {event.name}.")


def _track_for_event(event: Event, public_id: str | None) -> Track | None:
    if not public_id:
        return None
    track = Track.objects.filter(public_id=public_id).first()
    if track is None:
        raise ApiError("track_not_found", f"No track {public_id!r}.", status_code=404)
    if track.event_id != event.id:
        raise ApiError("cross_event", "Prize tracks must belong to the same event.", status_code=400)
    return track


@transaction.atomic
def create_prize(actor, event: Event, data: dict) -> Prize:
    _require_manager(actor, event)
    prize = Prize.objects.create(
        event=event,
        name=data.get("name", "").strip(),
        description=data.get("description", ""),
        value=data.get("value", ""),
        track=_track_for_event(event, data.get("track")),
        position=data.get("position", 0),
        places=data.get("places", 1),
        eligibility_note=data.get("eligibility_note", ""),
    )
    audit.services.record(actor, "event.prize_created", event=event, target=prize,
                          summary=f"Created prize {prize.name} in {event.name}.")
    return prize


@transaction.atomic
def update_prize(actor, event: Event, prize: Prize, data: dict) -> Prize:
    _require_manager(actor, event)
    locked = Prize.objects.select_for_update().get(pk=prize.pk, event=event)
    changed = []
    for field in ("name", "description", "value", "position", "places", "eligibility_note"):
        if field in data and getattr(locked, field) != data[field]:
            setattr(locked, field, data[field])
            changed.append(field)
    if "track" in data:
        track = _track_for_event(event, data.get("track"))
        if locked.track_id != (track.id if track else None):
            locked.track = track
            changed.append("track")
    if changed:
        locked.save(update_fields=changed)
        audit.services.record(actor, "event.prize_updated", event=event, target=locked,
                              summary=f"Updated prize {locked.name} in {event.name}.",
                              data={"fields": changed})
    return locked


@transaction.atomic
def delete_prize(actor, event: Event, prize: Prize) -> None:
    _require_manager(actor, event)
    locked = Prize.objects.select_for_update().get(pk=prize.pk, event=event)
    name = locked.name
    public_id = locked.public_id
    locked.delete()
    audit.services.record(actor, "event.prize_deleted", event=event, target=public_id,
                          summary=f"Deleted prize {name} from {event.name}.")


@transaction.atomic
def create_question(actor, event: Event, data: dict) -> CustomQuestion:
    _require_manager(actor, event)
    question = CustomQuestion.objects.create(
        event=event,
        prompt=data.get("prompt", "").strip(),
        help_text=data.get("help_text", ""),
        kind=data.get("kind", "short_text"),
        choices=data.get("choices", []),
        required=data.get("required", False),
        is_public=data.get("is_public", True),
        is_active=data.get("is_active", True),
        position=data.get("position", 0),
    )
    audit.services.record(actor, "event.question_created", event=event, target=question,
                          summary=f"Created question in {event.name}.")
    return question


@transaction.atomic
def update_question(actor, event: Event, question: CustomQuestion, data: dict) -> CustomQuestion:
    _require_manager(actor, event)
    locked = CustomQuestion.objects.select_for_update().get(pk=question.pk, event=event)
    changed = []
    for field in ("prompt", "help_text", "kind", "choices", "required", "is_public", "is_active", "position"):
        if field in data and getattr(locked, field) != data[field]:
            setattr(locked, field, data[field])
            changed.append(field)
    if changed:
        locked.save(update_fields=changed)
        audit.services.record(actor, "event.question_updated", event=event, target=locked,
                              summary=f"Updated question {locked.public_id} in {event.name}.",
                              data={"fields": changed})
    return locked


@transaction.atomic
def delete_question(actor, event: Event, question: CustomQuestion) -> None:
    _require_manager(actor, event)
    locked = CustomQuestion.objects.select_for_update().get(pk=question.pk, event=event)
    if locked.answers.exists():
        raise ApiError("question_in_use", "A question with answers cannot be deleted.", status_code=409)
    public_id = locked.public_id
    locked.delete()
    audit.services.record(actor, "event.question_deleted", event=event, target=public_id,
                          summary=f"Deleted question {public_id} from {event.name}.")


@transaction.atomic
def register_participant(actor, event: Event) -> tuple[EventRole, bool]:
    _require_authenticated(actor)
    locked = Event.objects.select_for_update().get(pk=event.pk)
    if not _submission_window_open(locked):
        raise _window_closed(locked)
    existing = EventRole.objects.filter(event=locked, user=actor).first()
    if existing is not None:
        if existing.role == Role.PARTICIPANT:
            return existing, False
        raise ApiError("role_conflict", "Your existing event role cannot register as a participant.",
                       status_code=409)
    role = EventRole.objects.create(
        event=locked, user=actor, role=Role.PARTICIPANT, public_id=new_public_id("par"), added_by=actor
    )
    audit.services.record(actor, "event.participant_registered", event=locked, target=role,
                          summary=f"{actor.display_name or actor.email} registered for {locked.name}.")
    return role, True


@transaction.atomic
def add_organizer(actor, event: Event, email: str) -> tuple[EventRole, bool]:
    _require_manager(actor, event)
    user = User.objects.filter(email=email.strip().lower()).first()
    if user is None:
        raise ApiError("user_not_found", "No user with that email exists.", status_code=404,
                       fields={"email": ["No user with that email exists."]})
    existing = EventRole.objects.filter(event=event, user=user).first()
    if existing is not None:
        if existing.role == Role.ORGANIZER:
            return existing, False
        raise ApiError("role_conflict", "That user already has another role in this event.",
                       status_code=409)
    role = EventRole.objects.create(
        event=event, user=user, role=Role.ORGANIZER, public_id=new_public_id("org"), added_by=actor
    )
    audit.services.record(actor, "event.organizer_added", event=event, target=role,
                          summary=f"Added {user.display_name or user.email} as an organizer of {event.name}.")
    return role, True


@transaction.atomic
def remove_organizer(actor, event: Event, organizer: EventRole) -> None:
    _require_manager(actor, event)
    locked = EventRole.objects.select_for_update().get(pk=organizer.pk, event=event, role=Role.ORGANIZER)
    if EventRole.objects.filter(event=event, role=Role.ORGANIZER).count() <= 1:
        raise ApiError("last_organizer", "The last organizer cannot be removed.", status_code=409)
    public_id = locked.public_id
    name = locked.user.display_name or locked.user.email
    locked.delete()
    audit.services.record(actor, "event.organizer_removed", event=event, target=public_id,
                          summary=f"Removed {name} as an organizer of {event.name}.")
