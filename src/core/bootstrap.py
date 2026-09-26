"""Idempotent first-boot seed: fixture event, demo accounts, demo event, demo tokens.

Safe to run on every container start (BUILD-SEC section 8). Everything is written
in one transaction, so a crash mid-way leaves the database untouched.
"""
import json
import os
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

import audit.services
from accounts.models import ApiToken, User
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import transaction
from accounts.tokens import hash_token
from core.clock import now
from core.ids import new_public_id
from events.models import (CustomQuestion, Event, EventRole, JudgingMode, Prize, QuestionKind, Role,
                           Track)
from interop.importer import ImportReport, import_fixture, shared_password_hash
from judging.models import Criterion, Rubric
from projects.models import Project, ProjectStatus
from projects.services import create_revision
from teams.models import Team, TeamMember

ADMIN_EMAIL = "admin@verdict.local"
ORGANIZER_EMAIL = "organizer@verdict.local"
DEMO_EVENT_SLUG = "demo-hack"
DEMO_EVENT_NAME = "Demo Hack (live)"
DEMO_SUBMISSIONS_DAYS = 14
DEMO_TRACKS = (
    ("Climate & energy", "Anything that reduces or shifts emissions."),
    ("Health & care", "Software that helps people look after each other."),
    ("Open tools", "Developer tools and infrastructure."),
)
DEMO_PRIZES = (
    ("Best overall", "The strongest entry across all tracks.", "$1,500"),
    ("Most useful", "The entry teams actually keep using.", "Hardware bundle"),
)
DEMO_CRITERIA = (
    ("impact", "Impact", Decimal("3.000")),
    ("craft", "Craft", Decimal("2.500")),
    ("originality", "Originality", Decimal("2.000")),
    ("documentation", "Documentation", Decimal("1.000")),
)
DEMO_PROJECTS = (
    ("Signal Garden", "Live sensor dashboards for community plots.", 0),
    ("Paper Trails", "Turns a shoebox of receipts into a searchable ledger.", 1),
    ("Cold Brew Compass", "Ritual timer and water hardness notes.", 2),
    ("Night Shift Radio", "Shift handover notes read aloud to the next team.", 0),
    ("Tide Table", "Twelve hours of tide, wind and swell in one view.", 1),
)
DEMO_JUDGES = (
    ("judge1@verdict.local", "Ada Okonkwo", (0, 1)),
    ("judge2@verdict.local", "Bruno Sattler", (1, 2)),
    ("judge3@verdict.local", "Chen Wei", (0, 2)),
)
DEMO_TOKENS = (
    ("admin", ADMIN_EMAIL, "vd_demo_admin_c0ffee5eed01"),
    ("organizer", ORGANIZER_EMAIL, "vd_demo_organizer_7f2a91c4e0b3"),
    ("judge_a", "diego.herrera@example.org", "vd_demo_judge_a_91bc5d2e8f10"),
    ("judge_b", "jonas.vogel@example.org", "vd_demo_judge_b_44de0a7c3b92"),
    ("participant", "priya1@example.org", "vd_demo_participant_2e88f1d4a6c5"),
)
DEMO_PASSWORD = "verdict-demo"


@dataclass
class BootstrapReport:
    imported: bool = False
    import_report: ImportReport | None = None
    demo_event_created: bool = False
    tokens: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "imported": self.imported,
            "demo_event_created": self.demo_event_created,
            "tokens": [name for name, _email, _plaintext in self.tokens],
        }


def _ensure_user(email: str, display_name: str, password_hash: str | None, *,
                 is_admin: bool = False, is_host: bool = False) -> User:
    user = User.objects.filter(email=email).first()
    if user is None:
        user = User(email=email, display_name=display_name, is_admin=is_admin, is_host=is_host)
        if password_hash:
            user.password = password_hash
        else:
            user.set_unusable_password()
        user.save()
        return user
    changed = []
    if password_hash and not user.has_usable_password():
        user.password = password_hash
        changed.append("password")
    if is_admin and not user.is_admin:
        user.is_admin = True
        changed.append("is_admin")
    if is_host and not user.is_host:
        user.is_host = True
        changed.append("is_host")
    if not user.is_active:
        user.is_active = True
        changed.append("is_active")
    if changed:
        user.save(update_fields=changed)
    return user


@transaction.atomic
def bootstrap() -> BootstrapReport:
    """Run every seed step. Repeated calls change nothing."""
    report = BootstrapReport()
    shared_hash = shared_password_hash()
    admin = _ensure_user(
        admin_email(), "Portal admin", _admin_password_hash(shared_hash),
        is_admin=True, is_host=True,
    )
    organizer = _ensure_user(ORGANIZER_EMAIL, "Event organizer", shared_hash, is_host=True)

    if not Event.objects.filter(source_id="evt_01").exists():
        report.imported = True
        report.import_report = _import_fixture_event(admin)

    fixture_event = Event.objects.filter(source_id="evt_01").first()
    if fixture_event is not None:
        _ensure_organizer_role(fixture_event, organizer)

    demo_event = Event.objects.filter(slug=DEMO_EVENT_SLUG).first()
    if demo_event is None:
        report.demo_event_created = True
        demo_event = _create_demo_event(organizer, shared_hash)
    _ensure_organizer_role(demo_event, organizer)

    if settings.DEMO_MODE:
        report.tokens = _ensure_demo_tokens()
    return report


def admin_email() -> str:
    """Outside DEMO_MODE the admin comes from ADMIN_EMAIL, not the demo address."""
    if settings.DEMO_MODE:
        return ADMIN_EMAIL
    return (os.environ.get("ADMIN_EMAIL") or "").strip() or ADMIN_EMAIL


def _admin_password_hash(shared_hash: str) -> str:
    """Outside DEMO_MODE the admin password comes from ADMIN_PASSWORD, not the demo one."""
    if not settings.DEMO_MODE:
        password = os.environ.get("ADMIN_PASSWORD") or ""
        if password:
            return make_password(password)
    return shared_hash


def _import_fixture_event(admin: User) -> ImportReport:
    with open(settings.FIXTURES_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    return import_fixture(data, actor=admin, fixture_path=settings.FIXTURES_PATH)


def _ensure_organizer_role(event: Event, organizer: User) -> EventRole:
    role, created = EventRole.objects.get_or_create(
        event=event,
        user=organizer,
        defaults={"role": Role.ORGANIZER, "public_id": new_public_id("org"), "added_by": organizer},
    )
    if created:
        audit.services.record(
            organizer, "event.organizer_added", event=event, target=role,
            summary=f"{organizer.display_name} is an organizer of {event.name}.",
        )
    return role


def _create_demo_event(organizer: User, shared_hash: str) -> Event:
    """A live event with a 14 day window, fixed at first boot so restarts do not slide it."""
    start = now()
    event = Event.objects.create(
        slug=DEMO_EVENT_SLUG,
        name=DEMO_EVENT_NAME,
        tagline="Everything is open. Register, form a team and submit before the timer runs out.",
        description="A seeded event for trying VERDICT end to end: register, team, submit, "
                    "then close submissions to start judging.",
        submissions_open_at=start,
        submissions_close_at=start + timedelta(days=DEMO_SUBMISSIONS_DAYS),
        judging_open_at=None,
        judging_close_at=None,
        max_team_size=4,
        reviews_per_project=3,
        judging_mode=JudgingMode.RUBRIC,
        created_by=organizer,
    )

    tracks = []
    for position, (name, description) in enumerate(DEMO_TRACKS, start=1):
        tracks.append(Track.objects.create(
            event=event, name=name, description=description, position=position))

    for position, (name, description, value) in enumerate(DEMO_PRIZES, start=1):
        Prize.objects.create(event=event, name=name, description=description, value=value,
                             position=position)

    CustomQuestion.objects.create(
        event=event,
        prompt="Link to a live demo or video walkthrough",
        kind=QuestionKind.URL,
        is_public=True,
        position=1,
    )
    CustomQuestion.objects.create(
        event=event,
        prompt="Anything the judges should know that is not in the write-up?",
        help_text="Only judges and organizers can read this.",
        kind=QuestionKind.LONG_TEXT,
        is_public=False,
        position=2,
    )

    rubric = Rubric.objects.create(event=event, version=1)
    for position, (key, name, weight) in enumerate(DEMO_CRITERIA, start=1):
        Criterion.objects.create(rubric=rubric, key=key, name=name, weight=weight,
                                 min_score=1, max_score=5, position=position)

    for index, (title, summary, track_index) in enumerate(DEMO_PROJECTS, start=1):
        email = f"demo-team-{index}@verdict.local"
        member = _ensure_user(email, f"Demo team {index}", shared_hash)
        EventRole.objects.get_or_create(
            event=event, user=member,
            defaults={"role": Role.PARTICIPANT, "public_id": new_public_id("par")},
        )
        team = Team.objects.create(event=event, name=f"Demo Team {index}", created_by=member)
        TeamMember.objects.create(team=team, user=member, event=event, is_owner=True)
        project = Project.objects.create(
            event=event,
            team=team,
            track=tracks[track_index],
            title=title,
            summary=summary,
            description=f"{summary} Built during the guided demo walkthrough.",
            repo_url=f"https://example.org/demo/{index:02d}",
            tech_tags=["demo"],
            status=ProjectStatus.SUBMITTED,
            first_submitted_at=start,
            last_submitted_at=start,
        )
        create_revision(project, actor=member)

    for email, display_name, track_indexes in DEMO_JUDGES:
        judge = _ensure_user(email, display_name, shared_hash)
        role, _ = EventRole.objects.get_or_create(
            event=event, user=judge,
            defaults={"role": Role.JUDGE, "public_id": new_public_id("jdg"), "added_by": organizer},
        )
        role.tracks.set([tracks[i] for i in track_indexes])

    audit.services.record(
        organizer, "event.created", event=event, target=event,
        summary=f"Created the demo event {DEMO_EVENT_NAME}.",
        data={"slug": DEMO_EVENT_SLUG, "tracks": len(tracks), "projects": len(DEMO_PROJECTS)},
    )
    return event


def _ensure_demo_tokens() -> list:
    """Deterministic demo tokens, re-activated on every boot so run.py always works."""
    ensured = []
    for name, email, plaintext in DEMO_TOKENS:
        user = User.objects.filter(email=email).first()
        if user is None:
            # The fixture judge/participant may be missing if the fixture was not imported.
            user = _ensure_user(email, email.split("@")[0], None)
        user.is_active = True
        user.save(update_fields=["is_active"])
        key_hash = hash_token(plaintext)
        token = ApiToken.objects.filter(key_hash=key_hash).first()
        if token is None:
            ApiToken.objects.create(
                user=user,
                name=f"demo {name}",
                prefix=plaintext[:12],
                key_hash=key_hash,
                is_demo=True,
            )
        else:
            # A judge revoking a demo token while exploring must not break the next boot.
            ApiToken.objects.filter(pk=token.pk).update(revoked_at=None, is_demo=True, user=user)
        ensured.append((name, email, plaintext))
    return ensured


def banner(report: BootstrapReport) -> str:
    """The block printed on every boot so the demo credentials are never a secret."""
    lines = [
        "VERDICT is running at http://localhost:8080   "
        "(DEMO MODE: demo credentials below, never use in production)",
        f"password for every demo account: {DEMO_PASSWORD}",
    ]
    labels = {"admin": "admin       ", "organizer": "organizer   ", "judge_a": "judge_a     ",
              "judge_b": "judge_b     ", "participant": "participant "}
    for name, email, plaintext in DEMO_TOKENS:
        lines.append(f"  {labels[name]} {email:<26} Authorization: Bearer {plaintext}")
    counts = report.import_report.counts if report.import_report else {}
    if counts:
        lines.append(
            f"seeded: 1 fixture event ({counts.get('projects', 0)} projects incl. "
            f"{counts.get('superseded', 0)} duplicate, {counts.get('judges', 0)} judges, "
            f"{counts.get('reviews', 0)} reviews), 1 open demo event"
        )
    return "\n".join(lines)
