"""Fixture import: the organiser's fixtures.json becomes a real event with provenance.

Everything happens in one transaction (BUILD-SEC section 8 step 1); the caller is
responsible for skipping when an event with the same source_id already exists.
"""
import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import audit.services
from accounts.models import User
from core.errors import ApiError
from core.ids import new_public_id
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils.dateparse import parse_datetime
from events.models import Event, EventRole, Role, Track
from judging.models import (Assignment, AssignmentBatch, AssignmentMethod, Criterion, CriterionScore,
                            Review, ReviewSource, ReviewStatus, Rubric)
from projects.models import Project, ProjectStatus
from projects.services import create_revision
from teams.models import Team, TeamMember

DEFAULT_SLUG = "sample-hack-2026"
DEMO_PASSWORD = "verdict-demo"
RUBRIC_CRITERIA = (
    ("functionality", "Functionality", Decimal("1.000")),
    ("quality", "Quality", Decimal("1.000")),
    ("innovation", "Innovation", Decimal("1.000")),
)
RUBRIC_MIN = 1
RUBRIC_MAX = 5
RUBRIC_VERSION = 1
REVIEWS_PER_PROJECT = 3


@dataclass
class ImportReport:
    """What the import did, in the shape stored on the import.completed audit event."""

    source_id: str
    slug: str
    counts: dict = field(default_factory=dict)
    superseded: list = field(default_factory=list)
    constant_scorers: list = field(default_factory=list)
    under_reviewed: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    fixture_sha256: str = ""
    fixture_bytes: int = 0

    def as_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "slug": self.slug,
            "counts": self.counts,
            "superseded": self.superseded,
            "constant_scorers": self.constant_scorers,
            "under_reviewed": self.under_reviewed,
            "notes": self.notes,
            "fixture_sha256": self.fixture_sha256,
            "fixture_bytes": self.fixture_bytes,
        }


def shared_password_hash() -> str:
    """One hash for every seeded user: PBKDF2 per user would take a minute.

    With DEMO_MODE off the shared hash is Django's unusable-password marker, so
    seeded accounts cannot be logged into at all.
    """
    return make_password(DEMO_PASSWORD if settings.DEMO_MODE else None)


def file_digest(path) -> tuple[str, int]:
    """(sha256 hex, byte size) of the fixture file, for the provenance record."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return "", 0
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _parse_dt(value: str | None):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ApiError("invalid_fixture", f"Unparseable timestamp {value!r} in fixture.")
    return parsed


def _ensure_user(email: str, display_name: str, password_hash: str) -> User:
    user = User.objects.filter(email=email).first()
    if user is None:
        user = User(email=email, display_name=display_name, password=password_hash)
        user.save()
    return user


@transaction.atomic
def import_fixture(data: dict, *, slug: str | None = None, actor=None,
                   fixture_path=None) -> ImportReport:
    """Import a fixtures-shaped dict as a new event."""
    fixture = data.get("event") or {}
    source_id = fixture.get("id") or "evt_01"
    slug = slug or DEFAULT_SLUG
    if Event.objects.filter(source_id=source_id).exists():
        raise ApiError("already_imported", f"Fixture {source_id} was already imported.", status_code=409)
    if Event.objects.filter(slug=slug).exists():
        raise ApiError("slug_taken", f"Event slug {slug!r} is already in use.", status_code=409)

    submissions_close = _parse_dt(fixture.get("submissions_close"))
    if submissions_close is None:
        raise ApiError("invalid_fixture", "Fixture event has no submissions_close.")

    report = ImportReport(source_id=source_id, slug=slug)
    password_hash = shared_password_hash()

    event = Event.objects.create(
        slug=slug,
        source_id=source_id,
        name=fixture.get("name") or slug,
        submissions_open_at=None,
        submissions_close_at=submissions_close,
        # Judging opens when submissions close: projects never change under judges.
        judging_open_at=submissions_close,
        judging_close_at=None,
        reviews_per_project=REVIEWS_PER_PROJECT,
        created_by=actor,
    )

    tracks = _import_tracks(event, data.get("tracks") or [])
    rubric, criteria_by_key = _import_rubric(event, _rubric_spec(data))
    judges = _import_judges(event, data.get("judges") or [], tracks, password_hash)
    teams, participant_count = _import_teams(event, data.get("teams") or [], password_hash)
    projects = _import_projects(event, data.get("projects") or [], teams, tracks, report, actor)
    reviews, scores = _import_scores(event, data.get("scores") or [], judges, projects,
                                     criteria_by_key, rubric, report, actor)

    # Historical reviews already exist, so the scoring policy is locked at import.
    Event.objects.filter(pk=event.pk).update(scoring_locked_at=submissions_close)
    event.scoring_locked_at = submissions_close

    report.counts = {
        "tracks": len(tracks),
        "judges": len(judges),
        "participants": participant_count,
        "teams": len(teams),
        "projects": len(projects),
        "assignments": Assignment.objects.filter(event=event).count(),
        "reviews": reviews,
        "criterion_scores": scores,
        "superseded": len(report.superseded),
    }
    if fixture_path is not None:
        report.fixture_sha256, report.fixture_bytes = file_digest(fixture_path)

    audit.services.record(
        actor,
        "import.completed",
        event=event,
        target=event,
        summary=(f"Imported fixture {source_id} as {slug}: {len(projects)} projects, "
                 f"{reviews} reviews, {len(report.superseded)} superseded."),
        data=report.as_dict(),
    )
    return report


def _import_tracks(event: Event, rows: list) -> dict:
    tracks: dict[str, Track] = {}
    for position, row in enumerate(rows, start=1):
        track = Track.objects.create(
            event=event,
            public_id=row["id"],
            source_id=row["id"],
            name=row["name"],
            description=row.get("description", ""),
            position=position,
        )
        tracks[row["id"]] = track
    return tracks


def _rubric_spec(data: dict) -> list[dict]:
    """The criteria the imported event is scored on.

    An event.json export declares its rubric, so a round trip keeps the event's own
    criteria, weights and ranges. A fixtures-shaped document without one is scored on
    the organizers' three criteria, unless its scores use other keys: then on exactly
    the keys they use. The organizers' fixtures.json, the showcase and the tour all
    score the default three, so they import exactly as before.
    """
    declared = data.get("rubric")
    if isinstance(declared, dict) and declared.get("criteria"):
        return _declared_criteria(declared["criteria"])
    defaults = [
        {"key": key, "name": name, "weight": weight, "min_score": RUBRIC_MIN, "max_score": RUBRIC_MAX}
        for key, name, weight in RUBRIC_CRITERIA
    ]
    used: list[str] = []
    for row in data.get("scores") or []:
        for key in (row.get("criteria") or {}) if isinstance(row, dict) else ():
            if key not in used:
                used.append(key)
    if not used or set(used) == {item["key"] for item in defaults}:
        return defaults
    return _declared_criteria([
        {"key": key, "name": key.replace("_", " ").replace("-", " ").capitalize()} for key in used
    ])


def _declared_criteria(items) -> list[dict]:
    """Validate a rubric from the document with the same limits the rubric editor uses."""
    invalid = ApiError("invalid_fixture", "The rubric must list 1 to 10 criteria, each with a "
                       "unique key, a positive weight and a score range.")
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        raise invalid
    criteria, keys = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise invalid
        key = item.get("key")
        name = item.get("name") or key
        description = item.get("description") or ""
        minimum = item.get("min_score", RUBRIC_MIN)
        maximum = item.get("max_score", RUBRIC_MAX)
        try:
            weight = Decimal(str(item.get("weight", "1.000")))
        except InvalidOperation:
            raise invalid from None
        if (
            not isinstance(key, str) or not re.fullmatch(r"[-a-zA-Z0-9_]{1,60}", key) or key in keys
            or not isinstance(name, str) or len(name) > 120 or not isinstance(description, str)
            or not weight.is_finite() or not Decimal("0") < weight <= Decimal("999.999")
            or any(isinstance(v, bool) or not isinstance(v, int) for v in (minimum, maximum))
            or not 0 <= minimum < maximum <= 32767
        ):
            raise invalid
        keys.add(key)
        criteria.append({"key": key, "name": name, "description": description,
                         "weight": weight.quantize(Decimal("0.001")),
                         "min_score": minimum, "max_score": maximum})
    return criteria


def _import_rubric(event: Event, spec: list[dict]) -> tuple[Rubric, dict]:
    rubric = Rubric.objects.create(event=event, version=RUBRIC_VERSION)
    criteria = {}
    for position, item in enumerate(spec, start=1):
        criteria[item["key"]] = Criterion.objects.create(rubric=rubric, position=position, **item)
    return rubric, criteria


def _import_judges(event: Event, rows: list, tracks: dict, password_hash: str) -> dict:
    """Judge roles keep the fixture id as public_id (jdg_24), which the checker uses."""
    judges: dict[str, EventRole] = {}
    for row in rows:
        user = _ensure_user(row["email"], row.get("name", ""), password_hash)
        role = EventRole.objects.create(
            event=event,
            user=user,
            role=Role.JUDGE,
            public_id=row["id"],
            source_id=row["id"],
        )
        role.tracks.set([tracks[track_id] for track_id in row.get("tracks", []) if track_id in tracks])
        judges[row["id"]] = role
    return judges


def _import_teams(event: Event, rows: list, password_hash: str) -> tuple[dict, int]:
    """Teams are keyed by fixture id: names repeat inside one event and stay unconstrained."""
    teams: dict[str, Team] = {}
    seen_users: set[int] = set()
    for row in rows:
        team = Team.objects.create(
            event=event,
            public_id=row["id"],
            source_id=row["id"],
            name=row["name"],
        )
        teams[row["id"]] = team
        for position, email in enumerate(row.get("members", [])):
            user = _ensure_user(email, email.split("@")[0], password_hash)
            TeamMember.objects.create(team=team, user=user, event=event, is_owner=(position == 0))
            if user.pk in seen_users:
                continue
            seen_users.add(user.pk)
            EventRole.objects.create(
                event=event,
                user=user,
                role=Role.PARTICIPANT,
                public_id=new_public_id("par"),
                source_id=email,
            )
    return teams, len(seen_users)


def _import_projects(event: Event, rows: list, teams: dict, tracks: dict, report: ImportReport,
                     actor) -> dict:
    """Import in submission order so a duplicate is superseded as soon as it appears.

    Superseding during the walk (rather than in a second pass) keeps the
    "one active project per team" partial unique constraint true at every moment.
    """
    projects: dict[str, Project] = {}
    by_key: dict[tuple, list] = {}
    for row in sorted(rows, key=lambda r: (str(r.get("submitted_at") or ""), str(r["id"]))):
        team = teams[row["team"]]
        key = (team.pk, row["title"].casefold())
        previous = by_key.get(key, [])
        if previous:
            # Stand the earlier ones down first: the partial unique constraint allows
            # only one active project per team.
            Project.objects.filter(pk__in=[p.pk for p in previous]).update(
                status=ProjectStatus.SUPERSEDED, superseded_by=None,
                status_reason="Replaced by a later submission of the same title.",
            )
        project = _create_project(event, row, teams, tracks, actor)
        if previous:
            _point_at_replacement(previous, project, report)
            previous.clear()
        by_key.setdefault(key, []).append(project)
        projects[row["id"]] = project
    return projects


def _create_project(event: Event, row: dict, teams: dict, tracks: dict, actor) -> Project:
    submitted_at = _parse_dt(row.get("submitted_at"))
    project = Project.objects.create(
        event=event,
        team=teams[row["team"]],
        track=tracks.get(row.get("track")),
        public_id=row["id"],
        source_id=row["id"],
        title=row["title"],
        summary=row.get("summary", ""),
        description=row.get("description", ""),
        repo_url=row.get("repo_url", ""),
        demo_video_url=row.get("demo_video_url", ""),
        live_url=row.get("live_url", ""),
        tech_tags=row.get("tech_tags", []),
        status=ProjectStatus.SUBMITTED,
        first_submitted_at=submitted_at,
        last_submitted_at=submitted_at,
        revision=0,
    )
    create_revision(project, actor=actor)
    return project


def _point_at_replacement(previous: list, latest: Project, report: ImportReport) -> None:
    """Same team + same case-folded title: every earlier one points at the newest."""
    for project in previous:
        reason = (f"Superseded by {latest.public_id}: the same team submitted the same title "
                  f"again at {latest.last_submitted_at:%Y-%m-%d %H:%M} UTC.")
        Project.objects.filter(pk=project.pk).update(
            superseded_by=latest, status_reason=reason
        )
        project.status = ProjectStatus.SUPERSEDED
        project.superseded_by = latest
        project.status_reason = reason
        report.superseded.append({
            "project": project.public_id,
            "superseded_by": latest.public_id,
            "team": project.team.public_id,
            "title": project.title,
        })
    report.notes.append(
        f"{len(previous)} duplicate project title(s) for team {latest.team.public_id} "
        f"({latest.title!r}) collapsed into {latest.public_id}."
    )


def _import_scores(event: Event, rows: list, judges: dict, projects: dict, criteria_by_key: dict,
                   rubric: Rubric, report: ImportReport, actor) -> tuple[int, int]:
    """Each fixture score becomes an assignment, a submitted review and criterion scores."""
    batch = AssignmentBatch.objects.create(
        event=event,
        method=AssignmentMethod.IMPORT,
        params={"source": "fixtures.json", "target": event.reviews_per_project},
        note="Imported together with the fixture event.",
        created_by=actor,
    )
    review_count = 0
    score_count = 0
    per_judge_values: dict[str, list] = {}
    per_project_reviews: dict[str, int] = {public_id: 0 for public_id in projects}
    seen_pairs: set[tuple] = set()

    for row in rows:
        judge = judges.get(row.get("judge"))
        project = projects.get(row.get("project"))
        if judge is None or project is None:
            report.notes.append(f"Skipped score for unknown judge/project {row!r}.")
            continue
        if (judge.pk, project.pk) in seen_pairs:
            # One review per assignment; a repeated fixture row is recorded, not doubled.
            report.notes.append(
                f"Skipped repeated score from {judge.public_id} for {project.public_id}."
            )
            continue
        missing = [key for key in criteria_by_key if key not in (row.get("criteria") or {})]
        if missing:
            # A submitted review has a value for every criterion, as submit_review
            # requires; a partial row is recorded, not imported half-scored.
            report.notes.append(
                f"Skipped score from {judge.public_id} for {project.public_id}: "
                f"no value for {', '.join(missing)}."
            )
            continue
        seen_pairs.add((judge.pk, project.pk))
        assignment, _ = Assignment.objects.get_or_create(
            event=event, judge=judge, project=project, defaults={"batch": batch}
        )
        review = Review.objects.create(
            event=event,
            assignment=assignment,
            judge=judge,
            project=project,
            status=ReviewStatus.SUBMITTED,
            comment=row.get("comment") or "",
            submitted_at=event.submissions_close_at,
            rubric_version=rubric.version,
            project_revision=project.revision,
            source=ReviewSource.IMPORT,
        )
        review_count += 1
        per_project_reviews[project.public_id] += 1
        values = {key: int(value) for key, value in (row.get("criteria") or {}).items()}
        per_judge_values.setdefault(judge.public_id, []).append(
            tuple(sorted(values.items()))
        )
        for key, value in values.items():
            criterion = criteria_by_key.get(key)
            if criterion is None:
                continue
            CriterionScore.objects.create(
                review=review, criterion=criterion, value=criterion.clamp(value)
            )
            score_count += 1

    report.constant_scorers = _constant_scorers(per_judge_values)
    report.under_reviewed = _under_reviewed(per_project_reviews, event.reviews_per_project)
    return review_count, score_count


def _constant_scorers(per_judge_values: dict) -> list:
    """Judges whose criterion values never move add no ordering information (section 9)."""
    return sorted(
        public_id
        for public_id, value_sets in per_judge_values.items()
        if len(value_sets) >= 2 and len(set(value_sets)) == 1
    )


def _under_reviewed(per_project_reviews: dict, target: int) -> list:
    return sorted(pid for pid, count in per_project_reviews.items() if count < target)
