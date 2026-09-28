"""The private, click-through demo tour.

Tour state is deliberately kept on the browser session and on the event
provenance marker.  That makes cleanup safe without adding a new migration.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import login
from django.db import transaction
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiResponse, extend_schema

from accounts.models import User
from accounts.services import start_session
from core.errors import ApiError
from core.showcase import build
from events.models import Event, EventRole, Role
from interop.importer import import_fixture
from judging.models import CriterionScore, Review, ReviewStatus
from teams.models import TeamMember

SESSION_KEY = "verdict_tour_event"
STEP_KEY = "verdict_tour_step"
MAX_SANDBOXES = 60
SANDBOX_TTL = timedelta(hours=6)
RATE_LIMIT_SECONDS = 60
_rate_hits: dict[str, list[float]] = {}

TOUR_STEPS = [
    {"id": "welcome", "title": "Welcome to the rehearsal", "text": "This is a private synthetic event. Every click below uses the same API and server-side rules as a real event.", "role": None, "url": "/tour/{slug}", "selector": "[data-tour-welcome]", "hint": "Nothing here changes the showcase or any other event."},
    {"id": "participant", "title": "Meet the submission deadline", "text": "Open your project and try an edit. The server refuses it because submissions are already closed.", "role": "participant", "url": "/events/{slug}/submission", "selector": "[data-tour-submission]", "hint": "The refusal is based on server time, not the browser clock."},
    {"id": "gallery", "title": "Browse the public gallery", "text": "Your synthetic project is visible with a display name, while private email addresses stay off the page.", "role": "participant", "url": "/events/{slug}/projects", "selector": "[data-tour-gallery]", "hint": "Open a project card to inspect the public view."},
    {"id": "judge", "title": "Score your queue", "text": "The judge sees only two unfinished reviews. Score them with the keyboard or the rubric controls.", "role": "judge", "url": "/judge", "selector": "[data-tour-judge]", "hint": "Press 1–5 on a focused criterion."},
    {"id": "privacy", "title": "Judge privacy is enforced", "text": "A judge can read and write only assigned work. Another judge's review is not available.", "role": "judge", "url": "/judge", "selector": "[data-tour-judge]", "hint": "Try a different project URL: the server still refuses it."},
    {"id": "progress", "title": "Watch progress", "text": "Switch to the organizer to see completion, then close judging when the two reviews are done.", "role": "organizer", "url": "/manage/{slug}/progress", "selector": "[data-tour-progress]", "hint": "Closing judging is a real, audited action."},
    {"id": "calibration", "title": "Compare raw and normalized scores", "text": "The calibration view makes the planted harsh and generous habits visible and compares both rankings.", "role": "organizer", "url": "/manage/{slug}/calibration", "selector": "[data-tour-calibration]", "hint": "The sandbox keeps the same synthetic truth."},
    {"id": "consequence", "title": "Preview a consequence", "text": "Before a disqualification, the organizer can inspect exactly which ranks and prizes would move.", "role": "organizer", "url": "/manage/{slug}/results", "selector": "[data-tour-consequence]", "hint": "A reason is required for every consequence."},
    {"id": "publish", "title": "Publish and verify", "text": "Preview, publish, open the public results, and verify the stored decision record.", "role": "organizer", "url": "/manage/{slug}/results", "selector": "[data-tour-publish]", "hint": "Publication is versioned and reproducible."},
    {"id": "certificate", "title": "Finish with a certificate", "text": "Certificates carry verification codes. Reset the sandbox to rehearse again, or explore the project with the commands below.", "role": "organizer", "url": "/manage/{slug}/certificates", "selector": "[data-tour-certificate]", "hint": "Next: python run.py .dogfood.toml, scripts/verify_tiers.py, docs/TIER-EVIDENCE.md."},
]


class TourRoleInputSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=[Role.ORGANIZER, Role.JUDGE, Role.PARTICIPANT])


class TourStepInputSerializer(serializers.Serializer):
    step = serializers.IntegerField(min_value=0, max_value=len(TOUR_STEPS) - 1)


class TourStartOutputSerializer(serializers.Serializer):
    slug = serializers.CharField()
    url = serializers.CharField()
    steps = serializers.ListField(child=serializers.DictField())


class TourResetOutputSerializer(serializers.Serializer):
    slug = serializers.CharField()
    url = serializers.CharField()


class TourRoleOutputSerializer(serializers.Serializer):
    role = serializers.CharField()
    redirect = serializers.CharField()


class TourStepOutputSerializer(serializers.Serializer):
    step = serializers.IntegerField()


def enabled() -> bool:
    return bool(settings.DEMO_MODE)


def _client_key(request) -> str:
    return request.META.get("REMOTE_ADDR") or "unknown"


def _throttle(request) -> None:
    now = timezone.now().timestamp()
    key = _client_key(request)
    hits = [value for value in _rate_hits.get(key, []) if now - value < RATE_LIMIT_SECONDS]
    if len(hits) >= 3:
        raise ApiError("throttled", "Please wait a moment before starting or resetting another tour.", 429)
    hits.append(now)
    _rate_hits[key] = hits


def _tour_events():
    return Event.objects.filter(source_id__startswith="evt_tour_")


def prune_expired(hours: float = 6) -> int:
    cutoff = timezone.now() - timedelta(hours=hours)
    events = list(_tour_events().filter(created_at__lt=cutoff))
    for event in events:
        _delete_event(event)
    return len(events)


def _delete_event(event: Event) -> None:
    """Delete only tour-owned rows; source data is never matched by slug."""
    user_ids = list(EventRole.objects.filter(event=event).values_list("user_id", flat=True))
    with transaction.atomic():
        event.projects.all().delete()
        event.teams.all().delete()
        event.tracks.all().delete()
        event.prizes.all().delete()
        event.questions.all().delete()
        event.roles.all().delete()
        Event.objects.filter(pk=event.pk).delete()
        User.objects.filter(pk__in=user_ids, email__endswith="@tour.verdict.local").delete()


def _event_for_session(request) -> Event | None:
    if not enabled():
        return None
    slug = request.session.get(SESSION_KEY)
    if not slug:
        return None
    return _tour_events().filter(slug=slug).first()


def _fresh_fixture() -> dict:
    data = build()
    token = secrets.token_hex(4)
    judge_id = "jdg_tour"
    data["event"]["id"] = f"evt_tour_{token}"
    data["judges"] = [{
        "id": judge_id,
        "name": "Tour Judge",
        "email": f"judge-{token}@tour.verdict.local",
        "tracks": [track["id"] for track in data["tracks"]],
    }]
    data["scores"] = [
        {**row, "judge": judge_id}
        for row in data["scores"] if row["judge"] == "jdg_sc01"
    ]
    for index, team in enumerate(data["teams"]):
        team["members"] = [f"tour.member{index + 1:02d}@example.org"]
    return data


@transaction.atomic
def create_sandbox(request, *, throttle: bool = True) -> Event:
    if throttle:
        _throttle(request)
    prune_expired()
    current = _event_for_session(request)
    if current is not None:
        return current
    if _tour_events().count() >= getattr(settings, "TOUR_MAX_SANDBOXES", MAX_SANDBOXES):
        raise ApiError("tour_capacity", "The tour is busy right now. Please try again in a few minutes.", 429)
    data = _fresh_fixture()
    token = secrets.token_hex(4)
    organizer = User.objects.create_user(
        email=f"organizer-{token}@tour.verdict.local",
        display_name="Tour Organizer",
    )
    report = import_fixture(data, slug=f"tour-{token}", actor=organizer)
    event = Event.objects.get(source_id=report.source_id)
    event.name = "VERDICT five-minute tour"
    event.tagline = "A private, synthetic rehearsal."
    event.gallery_public = False
    event.judging_close_at = None
    event.scoring_locked_at = None
    event.save(update_fields=["name", "tagline", "gallery_public", "judging_close_at", "scoring_locked_at"])
    judge_role = EventRole.objects.get(event=event, role=Role.JUDGE)
    old_judge = judge_role.user
    sandbox_judge = User.objects.create_user(
        email=f"judge-{event.slug[-8:]}@tour.verdict.local",
        display_name="Tour Judge",
    )
    judge_role.user = sandbox_judge
    judge_role.save(update_fields=["user"])
    old_judge.delete()
    imported_participant = EventRole.objects.filter(
        event=event, role=Role.PARTICIPANT
    ).select_related("user").first().user
    sandbox_participant = User.objects.create_user(
        email=f"participant-{event.slug[-8:]}@tour.verdict.local",
        display_name="Tour Participant",
    )
    participant_role = EventRole.objects.get(event=event, user=imported_participant, role=Role.PARTICIPANT)
    participant_role.user = sandbox_participant
    participant_role.save(update_fields=["user"])
    TeamMember.objects.filter(event=event, user=imported_participant).update(user=sandbox_participant)
    imported_participant.delete()
    EventRole.objects.create(event=event, user=organizer, role=Role.ORGANIZER, public_id=f"org_{event.slug[-8:]}")
    open_reviews = list(Review.objects.filter(event=event, judge=judge_role).order_by("id")[:2])
    for review in open_reviews:
        CriterionScore.objects.filter(review=review).delete()
        review.status = ReviewStatus.DRAFT
        review.submitted_at = None
        review.save(update_fields=["status", "submitted_at", "updated_at"])
    event.scoring_locked_at = None
    event.save(update_fields=["scoring_locked_at"])
    request.session[SESSION_KEY] = event.slug
    request.session[STEP_KEY] = 0
    request.session.modified = True
    request._tour_users = (organizer, sandbox_judge, sandbox_participant)
    return event


def role_user(request, role: str) -> User:
    event = _event_for_session(request)
    if event is None or role not in {Role.ORGANIZER, Role.JUDGE, Role.PARTICIPANT}:
        raise ApiError("tour_forbidden", "This role is not available in your tour sandbox.", 403)
    row = EventRole.objects.filter(event=event, role=role).select_related("user").first()
    if row is None:
        raise ApiError("tour_forbidden", "This role is not available in your tour sandbox.", 403)
    return row.user


class TourStartView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(operation_id="tour_start", request=None, responses={201: TourStartOutputSerializer})
    def post(self, request):
        if not enabled():
            raise ApiError("not_found", "Not found.", 404)
        event = create_sandbox(request)
        return Response({"slug": event.slug, "url": f"/tour/{event.slug}", "steps": TOUR_STEPS}, status=201)


class TourRoleView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(operation_id="tour_switch_role", request=TourRoleInputSerializer,
                   responses={200: TourRoleOutputSerializer})
    def post(self, request):
        if not enabled():
            raise ApiError("not_found", "Not found.", 404)
        serializer = TourRoleInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = role_user(request, serializer.validated_data["role"])
        start_session(request, user)
        return Response({"role": serializer.validated_data["role"], "redirect": f"/tour/{request.session[SESSION_KEY]}"})


class TourResetView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(operation_id="tour_reset", request=None, responses={201: TourResetOutputSerializer})
    def post(self, request):
        if not enabled():
            raise ApiError("not_found", "Not found.", 404)
        _throttle(request)
        old = _event_for_session(request)
        if old is not None:
            _delete_event(old)
        request.session.pop(SESSION_KEY, None)
        request.session.pop(STEP_KEY, None)
        event = create_sandbox(request, throttle=False)
        return Response({"slug": event.slug, "url": f"/tour/{event.slug}"}, status=201)


class TourStepView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(operation_id="tour_step", request=TourStepInputSerializer,
                   responses={200: TourStepOutputSerializer})
    def post(self, request):
        if not enabled():
            raise ApiError("not_found", "Not found.", 404)
        if _event_for_session(request) is None:
            raise ApiError("tour_forbidden", "Start a tour first.", 403)
        serializer = TourStepInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        step = serializer.validated_data["step"]
        request.session[STEP_KEY] = step
        request.session.modified = True
        return Response({"step": step})


def tour_page(request, slug: str | None = None):
    if not enabled():
        from django.http import Http404
        raise Http404()
    event = _event_for_session(request)
    if event is None or (slug and event.slug != slug):
        from django.http import Http404
        raise Http404()
    steps = []
    for step in TOUR_STEPS:
        item = dict(step)
        item["url"] = step["url"].format(slug=event.slug)
        steps.append(item)
    return render_tour(request, event, steps)


def tour_calibration(request, slug: str):
    """Sandbox counterpart to the showcase-only planted-truth page."""
    from django.http import Http404
    from django.shortcuts import render
    from events.policy import is_organizer

    event = _event_for_session(request)
    if event is None:
        from core.calibration_views import calibration
        return calibration(request, slug)
    if event is None or event.slug != slug or not is_organizer(request.user, event):
        raise Http404()
    return render(request, "core/tour_calibration.html", {"event": event})


def render_tour(request, event, steps):
    from django.shortcuts import render
    return render(request, "core/tour.html", {"tour_event": event, "tour_steps": steps})


def context(request):
    event = _event_for_session(request)
    if event is None:
        return {"tour_active": False, "tour_steps": []}
    steps = [dict(step, url=step["url"].format(slug=event.slug)) for step in TOUR_STEPS]
    return {"tour_active": True, "tour_event": event, "tour_steps": steps}
