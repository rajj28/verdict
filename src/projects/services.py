"""Project writes: revision snapshots, digests and canonical JSON.

Kept separate from the importer so submissions made through the API and
revisions created at import time produce byte-identical receipts.
"""
import hashlib
import io
import json
import uuid
from urllib.parse import urlsplit

import audit.services
from PIL import Image, UnidentifiedImageError
from core.clock import now
from core.errors import ApiError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.dateparse import parse_datetime
from events.models import CustomQuestion, Event, Track
from events.policy import is_organizer, submission_window_open
from projects.models import (ACTIVE_STATUSES, Answer, Project, ProjectImage, ProjectRevision,
                             ProjectStatus)
from teams.models import Team, TeamMember
from teams.policy import is_team_member, is_team_owner

TITLE_MAX = 120
SUMMARY_MAX = 200
DESCRIPTION_MAX = 10000
ANSWER_MAX = 2000
TAGS_MAX = 10
TAG_MAX = 24
IMAGES_MAX = 6
IMAGE_BYTES_MAX = 5 * 1024 * 1024
# Rule 9: raster formats only, verified with Pillow. No SVG: it is a document
# format that can carry script.
IMAGE_FORMATS = {
    "JPEG": "jpg",
    "PNG": "png",
    "WEBP": "webp",
    "GIF": "gif",
}
URL_FIELDS = ("demo_video_url", "repo_url", "live_url")
TEXT_FIELDS = ("title", "summary", "description")
# What a submission must carry before the backend will call it submitted
# (BUILD-SEC section 5), with the message the form shows next to each field.
REQUIRED_LABELS = {
    "title": "A title is required.",
    "summary": "A one-line summary is required.",
    "description": "The write-up is required.",
    "repo_url": "A repository link is required.",
}


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


def _validate_url(value: str) -> str | None:
    """A URL is only accepted as http(s) with a host (rule 9: no javascript:)."""
    parts = urlsplit(value)
    if parts.scheme in ("http", "https") and parts.netloc:
        return value
    return None


def _clean_tags(value) -> tuple[list[str], list[str]]:
    """Lowercased, de-duplicated, order-preserving; at most 10 short strings."""
    if not isinstance(value, (list, tuple)):
        return [], ["Send the tags as a list of strings."]
    problems: list[str] = []
    if len(value) > TAGS_MAX:
        problems.append(f"Use at most {TAGS_MAX} tags.")
    tags: list[str] = []
    for item in value:
        if not isinstance(item, str):
            problems.append("Every tag must be text.")
            continue
        tag = item.strip().lower()
        if not tag:
            continue
        if len(tag) > TAG_MAX:
            problems.append(f"Keep each tag under {TAG_MAX} characters.")
        if tag not in tags:
            tags.append(tag)
    return tags, problems


def _validated_values(data: dict, *, require_title: bool = True) -> dict:
    """Clean every writable field in one pass and report all problems at once.

    Returning one ApiError with every field message means the form can paint them
    all instead of making the writer fix one field per round trip.
    """
    fields: dict[str, list[str]] = {}
    values: dict = {}
    for name in TEXT_FIELDS:
        if name not in data:
            continue
        text = (data.get(name) or "").strip()
        if name == "title":
            if require_title and not text:
                fields.setdefault(name, []).append("A title is required.")
            elif len(text) > TITLE_MAX:
                fields.setdefault(name, []).append(f"Keep the title under {TITLE_MAX} characters.")
        elif name == "summary" and len(text) > SUMMARY_MAX:
            fields.setdefault(name, []).append(f"Keep the summary under {SUMMARY_MAX} characters.")
        elif name == "description" and len(text) > DESCRIPTION_MAX:
            fields.setdefault(name, []).append(
                f"Keep the description under {DESCRIPTION_MAX} characters.")
        values[name] = text
    for name in URL_FIELDS:
        if name not in data:
            continue
        text = (data.get(name) or "").strip()
        if text and _validate_url(text) is None:
            fields.setdefault(name, []).append("Use a full http:// or https:// address.")
        values[name] = text
    if "tech_tags" in data:
        tags, problems = _clean_tags(data.get("tech_tags"))
        if problems:
            fields.setdefault("tech_tags", []).extend(problems)
        values["tech_tags"] = tags
    if fields:
        raise ApiError("invalid", "Please correct the highlighted fields.", fields=fields)
    return values


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
    event = Event.objects.select_for_update().get(pk=event.pk)
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

    values = _validated_values(data)
    project = Project.objects.create(
        event=event,
        team=team,
        track=resolve_track(event, data.get("track")),
        title=values["title"],
        summary=values.get("summary", ""),
        description=values.get("description", ""),
        repo_url=values.get("repo_url", ""),
        live_url=values.get("live_url", ""),
        demo_video_url=values.get("demo_video_url", ""),
        tech_tags=values.get("tech_tags", []),
        status=ProjectStatus.DRAFT,
    )
    audit.services.record(
        actor, "project.created", event=event, target=project,
        summary=f"{actor.display_name or actor.email.split('@')[0]} started the draft "
                f"{project.title} for {team.name}.",
        data={"team": team.public_id, "status": ProjectStatus.DRAFT},
    )
    return project


def _require_authenticated(actor):
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise ApiError("not_authenticated", "Sign in to edit this project.", status_code=401)


def _lock_project(project: Project) -> Project:
    # Event first, matching deadline changes/publication, then the project and
    # its team. PostgreSQL cannot lock the nullable side of the track outer join.
    event = Event.objects.select_for_update().get(pk=project.event_id)
    locked = (Project.objects.select_for_update(of=("self", "team"))
              .select_related("team", "track").get(pk=project.pk))
    locked.event = event
    return locked


def _require_editor(actor, project: Project) -> None:
    """A team member may write; somebody else's team may not."""
    if not is_team_member(actor, project.team):
        raise ApiError("not_a_member", f"Only members of {project.team.name} can change this project.",
                       status_code=403)


def _require_active(project: Project) -> None:
    if project.status not in ACTIVE_STATUSES:
        raise ApiError(
            "project_not_active",
            f"This project is {project.get_status_display().lower()}, so it can no longer be edited.",
            status_code=409,
        )


def _check_base_updated_at(project: Project, value) -> None:
    """Optimistic concurrency: the editor says which version it was looking at.

    Two tabs on the same project is the normal case, not an attack, so this is a
    409 that tells the writer to reload rather than a silent overwrite.
    """
    if value in (None, ""):
        return
    sent = value if hasattr(value, "tzinfo") else parse_datetime(str(value))
    if sent is None:
        raise ApiError("invalid", "base_updated_at must be an ISO 8601 timestamp.",
                       fields={"base_updated_at": ["Send the ISO timestamp of the version you edited."]})
    if abs((project.updated_at - sent).total_seconds()) > 0.001:
        raise ApiError(
            "stale_edit",
            "This project changed in another tab; reload to see the latest version.",
            status_code=409,
        )


@transaction.atomic
def update_project(actor, event: Event, project: Project, data: dict) -> Project:
    """Edit a draft or a submitted project.

    Window first, then ownership, then the optimistic-concurrency check, then
    field validation. A save on a submitted project is a new revision: there is no
    "unsubmit" state, so what a judge already read keeps matching a revision.
    """
    _require_authenticated(actor)
    if event is None:
        raise ApiError("event_not_found", "That event does not exist.", status_code=404)
    if project.event_id != event.id:
        # Two event-scoped rows can never be mixed, whatever the caller sent.
        raise ApiError("project_not_found", "That project belongs to a different event.",
                       status_code=404)
    locked = _lock_project(project)
    event = locked.event
    check_submission_window(event)
    _require_editor(actor, locked)
    _require_active(locked)
    _check_base_updated_at(locked, data.get("base_updated_at"))

    changed = []
    values = _validated_values(data, require_title=False)
    if "track" in data:
        values["track"] = resolve_track(event, data.get("track"))
    for field, value in values.items():
        current = getattr(locked, field)
        if field == "track":
            if locked.track_id != (value.id if value is not None else None):
                locked.track = value
                changed.append("track")
            continue
        if current != value:
            setattr(locked, field, value)
            changed.append(field)
    if not changed:
        return locked
    if locked.status == ProjectStatus.SUBMITTED:
        locked.last_submitted_at = now()
    locked.save(update_fields=sorted(changed) + ["updated_at", "last_submitted_at"])
    revision = None
    if locked.status == ProjectStatus.SUBMITTED:
        revision = create_revision(locked, actor=actor)
    audit.services.record(
        actor, "project.updated", event=event, target=locked,
        summary=f"Edited {locked.title} for {locked.team.name}.",
        data={"fields": sorted(changed),
              "revision": revision.number if revision is not None else None},
    )
    return locked


def _required_field_errors(project: Project) -> dict[str, list[str]]:
    """What is still missing before this project can be submitted."""
    fields: dict[str, list[str]] = {}
    for name, message in REQUIRED_LABELS.items():
        if not (getattr(project, name) or "").strip():
            fields[name] = [message]
    if project.track_id is None:
        fields["track"] = ["Pick the track you are entering."]
    for question in project.event.questions.filter(required=True, is_active=True):
        answer = project.answers.filter(question=question).only("value").first()
        if answer is None or not (answer.value or "").strip():
            fields[f"question_{question.public_id}"] = [f"Answer: {question.prompt}"]
    return fields


@transaction.atomic
def submit_project(actor, project: Project) -> Project:
    """Turn the team's draft into a submitted project and record the receipt."""
    _require_authenticated(actor)
    locked = _lock_project(project)
    check_submission_window(locked.event)
    _require_editor(actor, locked)
    if locked.status == ProjectStatus.SUBMITTED:
        raise ApiError("already_submitted", "This project is already submitted.", status_code=409)
    _require_active(locked)
    fields = _required_field_errors(locked)
    if fields:
        raise ApiError(
            "incomplete",
            "Fill in the highlighted fields before submitting.",
            status_code=400,
            fields=fields,
        )
    stamp = now()
    locked.status = ProjectStatus.SUBMITTED
    locked.first_submitted_at = locked.first_submitted_at or stamp
    locked.last_submitted_at = stamp
    locked.save(update_fields=["status", "first_submitted_at", "last_submitted_at", "updated_at"])
    revision = create_revision(locked, actor=actor)
    audit.services.record(
        actor, "project.submitted", event=locked.event, target=locked,
        summary=f"Submitted {locked.title} for {locked.team.name} (revision {revision.number}).",
        data={"revision": revision.number, "digest": revision.digest},
    )
    return locked


@transaction.atomic
def withdraw_project(actor, project: Project) -> Project:
    """The team owner stands the submission down while the window is still open."""
    _require_authenticated(actor)
    locked = _lock_project(project)
    check_submission_window(locked.event)
    if not is_team_owner(actor, locked.team):
        raise ApiError("not_team_owner",
                       f"Only the owner of {locked.team.name} can withdraw its project.",
                       status_code=403)
    _require_active(locked)
    reason = (f"Withdrawn by the team owner at {now():%Y-%m-%d %H:%M} UTC."
              if locked.status == ProjectStatus.SUBMITTED else "Withdrawn while still a draft.")
    locked.status = ProjectStatus.WITHDRAWN
    locked.status_reason = reason
    locked.save(update_fields=["status", "status_reason", "updated_at"])
    audit.services.record(
        actor, "project.withdrawn", event=locked.event, target=locked,
        summary=f"Withdrew {locked.title} for {locked.team.name}.",
        data={"reason": reason},
    )
    return locked


@transaction.atomic
def disqualify_project(actor, project: Project, reason: str) -> Project:
    """An organizer stands a project down, any time, with a recorded reason."""
    _require_authenticated(actor)
    locked = _lock_project(project)
    if not is_organizer(actor, locked.event):
        raise ApiError("forbidden", "Only organizers of this event can disqualify a project.",
                       status_code=403)
    text = (reason or "").strip()
    if not text:
        raise ApiError("invalid", "Say why this project is disqualified.",
                       fields={"reason": ["A reason is required."]})
    if locked.status in (ProjectStatus.DISQUALIFIED, ProjectStatus.SUPERSEDED):
        raise ApiError("not_disqualifiable",
                       f"This project is already {locked.get_status_display().lower()}.",
                       status_code=409)
    locked.status = ProjectStatus.DISQUALIFIED
    locked.status_reason = text
    locked.save(update_fields=["status", "status_reason", "updated_at"])
    audit.services.record(
        actor, "project.disqualified", event=locked.event, target=locked,
        summary=f"Disqualified {locked.title} for {locked.team.name}: {text}",
        data={"reason": text},
    )
    return locked


def _verified_image(upload) -> tuple[bytes, str]:
    """Read an upload and prove it is one of the four allowed raster formats.

    Pillow does the parsing rather than the file extension, so a .png that is
    really an SVG never reaches storage.
    """
    raw = upload.read()
    if len(raw) > IMAGE_BYTES_MAX:
        raise ApiError("image_too_large", "Images are limited to 5 MB.", status_code=400,
                       fields={"image": ["This file is larger than 5 MB."]})
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(raw)) as probe:
            image_format = (probe.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError):
        raise ApiError("invalid_image", "That file is not a readable image.", status_code=400,
                       fields={"image": ["Upload a jpeg, png, webp or gif."]})
    extension = IMAGE_FORMATS.get(image_format)
    if extension is None:
        raise ApiError(
            "unsupported_image_type",
            f"{image_format or 'That file'} images are not accepted; use jpeg, png, webp or gif.",
            status_code=400,
            fields={"image": ["Upload a jpeg, png, webp or gif."]},
        )
    return raw, extension


@transaction.atomic
def add_image(actor, project: Project, upload, caption: str = "") -> ProjectImage:
    """Attach one gallery image (at most six per project)."""
    _require_authenticated(actor)
    locked = _lock_project(project)
    check_submission_window(locked.event)
    _require_editor(actor, locked)
    _require_active(locked)
    if upload is None:
        raise ApiError("invalid", "Choose a file to upload.", fields={"image": ["No file was sent."]})
    count = locked.images.count()
    if count >= IMAGES_MAX:
        raise ApiError("too_many_images", f"A project can have {IMAGES_MAX} images.", status_code=409)
    raw, extension = _verified_image(upload)
    # A random name: the client never chooses where the file lands, and two
    # uploads of "screenshot.png" cannot overwrite each other.
    name = f"projects/{uuid.uuid4().hex}.{extension}"
    # Positions are 1-based, because the editor shows them and the delete
    # endpoint is addressed by the number the writer can see.
    image = ProjectImage(project=locked, caption=(caption or "").strip()[:200], position=count + 1)
    image.image.save(name, ContentFile(raw), save=False)
    image.save()
    audit.services.record(
        actor, "project.image_added", event=locked.event, target=locked,
        summary=f"Added an image to {locked.title}.",
        data={"position": image.position},
    )
    return image


@transaction.atomic
def delete_image(actor, project: Project, position: int) -> None:
    """Remove one gallery image by its 1-based position in the editor."""
    _require_authenticated(actor)
    locked = _lock_project(project)
    check_submission_window(locked.event)
    _require_editor(actor, locked)
    _require_active(locked)
    image = locked.images.filter(position=position).order_by("position", "id").first()
    if image is None:
        raise ApiError("image_not_found", f"Image {position} is not on this project.",
                       status_code=404)
    image.image.delete(save=False)
    image.delete()
    # Positions are display order, not identity: close the gap so the editor
    # shows 1..n and the next delete addresses what the writer can see.
    for index, remaining in enumerate(locked.images.order_by("position", "id"), start=1):
        if remaining.position != index:
            remaining.position = index
            remaining.save(update_fields=["position"])
    audit.services.record(
        actor, "project.image_deleted", event=locked.event, target=locked,
        summary=f"Removed image {position} from {locked.title}.",
        data={"position": position},
    )


@transaction.atomic
def save_answers(actor, project: Project, answers: dict) -> Project:
    """Replace the answers to custom questions (rule: every question is one event's)."""
    _require_authenticated(actor)
    locked = _lock_project(project)
    check_submission_window(locked.event)
    _require_editor(actor, locked)
    _require_active(locked)
    if not isinstance(answers, dict):
        raise ApiError("invalid", "Send the answers as an object keyed by question id.",
                       fields={"answers": ["Expected {question_public_id: value}."]})
    questions = {question.public_id: question for question in
                 CustomQuestion.objects.filter(event=locked.event, is_active=True)}
    fields: dict[str, list[str]] = {}
    resolved: dict = {}
    for key, value in answers.items():
        question = questions.get(str(key))
        if question is None:
            if CustomQuestion.objects.filter(public_id=str(key)).exists():
                raise ApiError("cross_event", "That question belongs to a different event.",
                               status_code=400, fields={str(key): ["Unknown question in this event."]})
            raise ApiError("question_not_found", f"No question {key!r} in this event.",
                           status_code=404, fields={str(key): ["Unknown question in this event."]})
        text = ("" if value is None else str(value)).strip()
        if len(text) > ANSWER_MAX:
            fields[str(key)] = [f"Keep the answer under {ANSWER_MAX} characters."]
        resolved[question] = text
    if fields:
        raise ApiError("invalid", "Please correct the highlighted fields.", fields=fields)
    changed = False
    for question, text in resolved.items():
        answer = Answer.objects.filter(project=locked, question=question).first()
        if answer is None:
            # An untouched optional question stays absent rather than becoming a
            # row of empty text, so "has an answer" keeps meaning something.
            if not text:
                continue
            Answer.objects.create(project=locked, question=question, value=text)
            changed = True
            continue
        if answer.value != text:
            answer.value = text
            answer.save(update_fields=["value", "updated_at"])
            changed = True
    if not changed:
        return locked
    revision = None
    if locked.status == ProjectStatus.SUBMITTED:
        locked.last_submitted_at = now()
        locked.save(update_fields=["last_submitted_at", "updated_at"])
        revision = create_revision(locked, actor=actor)
    audit.services.record(
        actor, "project.answers_updated", event=locked.event, target=locked,
        summary=(f"Updated the custom answers on {locked.title} (revision {revision.number})."
                 if revision is not None
                 else f"Updated the custom answers on {locked.title}."),
        data={"questions": sorted(question.public_id for question in resolved),
              "revision": revision.number if revision is not None else None},
    )
    return locked
