"""Rubric, judges, assignments, reviews and scores.

Read scoping and permission predicates. Every queryset in a view starts here.
"""
import hashlib

from django.db.models import F, Q, QuerySet

from core.errors import ApiError
from events.models import Event, EventRole, JudgingMode, Role
from events.policy import is_organizer
from judging.models import Assignment, Comparison, Conflict, Criterion, Review, ReviewStatus
from projects.models import Project, ProjectStatus
from results import engine


def rubric_criteria(event: Event) -> list[Criterion]:
    return list(Criterion.objects.filter(rubric__event=event).select_related("rubric")
                .order_by("position", "id"))


def visible_judges(user, event: Event) -> QuerySet[EventRole]:
    """Judge directory for organizers of the event."""
    require_manager(user, event)
    return (EventRole.objects.filter(event=event, role=Role.JUDGE).select_related("user")
            .prefetch_related("tracks").order_by("public_id"))


def visible_conflicts(user, event: Event) -> QuerySet[Conflict]:
    """Conflict declarations are restricted to the event organizers."""
    require_manager(user, event)
    return Conflict.objects.filter(event=event).select_related(
        "judge__user", "team"
    ).order_by("id")


def engine_criteria(event: Event) -> list[engine.Criterion]:
    """The event's rubric in the shape the pure engine expects."""
    return [
        engine.Criterion(key=c.key, weight=float(c.weight), min_score=c.min_score,
                         max_score=c.max_score)
        for c in rubric_criteria(event)
    ]


def review_values(review: Review) -> dict[str, int]:
    return {score.criterion.key: score.value for score in review.scores.all()}


def scored_reviews(reviews) -> list[tuple[Review, float]]:
    """Pair each review with its 0-100 weighted score (BUILD-SEC section 9).

    A review that has not filled in every criterion yet has no score at all and
    is left out rather than treated as a zero, which would drag a project down.
    """
    pairs: list[tuple[Review, float]] = []
    cache: dict[int, list[engine.Criterion]] = {}
    for review in reviews:
        criteria = cache.setdefault(review.event_id, engine_criteria(review.event))
        values = review_values(review)
        if any(c.key not in values for c in criteria):
            continue
        pairs.append((review, engine.review_score(values, criteria)))
    return pairs


def engine_reviews(pairs) -> list[engine.ReviewInput]:
    """Engine input for already-scored reviews."""
    return [
        engine.ReviewInput(
            review_id=review.public_id,
            judge_id=review.judge.public_id,
            project_id=review.project.public_id,
            values=review_values(review),
        )
        for review, _score in pairs
    ]


def included_reviews(event: Event) -> QuerySet[Review]:
    """Submitted, non-excluded reviews of this event's submitted projects."""
    return (
        Review.objects.filter(event=event, status=ReviewStatus.SUBMITTED, exclusion__isnull=True)
        .select_related("project", "judge", "judge__user", "event")
        .prefetch_related("scores__criterion")
    )


def judge_reviews(user) -> QuerySet[Review]:
    """The caller's own reviews, newest event first, in one scoped queryset."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Review.objects.none()
    return (
        Review.objects.filter(judge__user=user, judge__role=Role.JUDGE)
        .select_related("event", "judge", "project")
        .prefetch_related("scores__criterion")
        .order_by("event__slug", "project__title", "id")
    )


def require_manager(user, event: Event) -> None:
    """Require an authenticated organizer/admin before managing event judging."""
    if user is None or not getattr(user, "is_authenticated", False):
        raise ApiError("not_authenticated", "Authentication is required.", status_code=401)
    if not is_organizer(user, event):
        raise ApiError("forbidden", "Only organizers of this event can manage judging.",
                       status_code=403)


def require_judge(user, event: Event) -> EventRole:
    """Return the caller's judge role or deny access to judge-only data."""
    if user is None or not getattr(user, "is_authenticated", False):
        raise ApiError("not_authenticated", "Authentication is required.", status_code=401)
    role = EventRole.objects.filter(event=event, user=user, role=Role.JUDGE).first()
    if role is None:
        raise ApiError("forbidden", "Only a judge assigned to this event can do that.",
                       status_code=403)
    return role


def visible_pairwise_projects(user, event: Event, role=None) -> QuerySet[Project]:
    """Only currently submitted, assigned, in-track evidence without declared conflicts."""
    role = role or require_judge(user, event)
    if event.judging_mode not in (JudgingMode.PAIRWISE, JudgingMode.BOTH):
        raise ApiError("pairwise_disabled", "Pairwise judging is not enabled.", status_code=409)
    return (Project.objects.filter(
        event=event, team__event=event, track__event=event, status=ProjectStatus.SUBMITTED,
        assignments__event=event, assignments__judge=role, track__in=role.tracks.all(),
    ).exclude(team__conflicts__judge=role).select_related("team", "track")
            .prefetch_related("answers__question", "images").distinct().order_by("public_id"))


def visible_comparisons(user, event: Event) -> QuerySet[Comparison]:
    """Own judgments only; organizers may export the complete event history."""
    rows = Comparison.objects.filter(event=event).select_related("left", "right", "winner", "judge")
    if is_organizer(user, event):
        return rows
    return rows.filter(judge=require_judge(user, event))


def included_comparisons(event: Event) -> QuerySet[Comparison]:
    """Active historical judgments between still-eligible projects in this event."""
    return Comparison.objects.filter(
        event=event, judge__event=event, retracted_at__isnull=True,
        left__event=event, right__event=event,
        left__team__event=event, right__team__event=event,
        left__track__event=event, right__track__event=event,
        left__status=ProjectStatus.SUBMITTED, right__status=ProjectStatus.SUBMITTED,
    ).select_related("judge", "left", "right", "winner").order_by("public_id")


def require_judge_or_manager(user, event: Event) -> EventRole | None:
    """Allow event organizers/admins and judges of that event."""
    if is_organizer(user, event):
        return None
    return require_judge(user, event)


def visible_reviews(user, event: Event | None = None) -> QuerySet[Review]:
    """Reviews the caller may read: a judge sees their own, organizers see all."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Review.objects.none()
    if event is not None and is_organizer(user, event):
        return (
            Review.objects.filter(event=event)
            .select_related("event", "judge", "project")
            .prefetch_related("scores__criterion")
            .order_by("id")
        )
    rows = judge_reviews(user)
    return rows.filter(event=event) if event is not None else rows


def can_read_judge_scores(user, judge_role: EventRole) -> bool:
    """Only the judge themself and organizers of that event read these scores."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if judge_role.user_id == user.pk:
        return True
    return is_organizer(user, judge_role.event)


def reviews_for_judge_role(judge_role: EventRole) -> QuerySet[Review]:
    return (
        Review.objects.filter(judge=judge_role)
        .select_related("event", "judge", "project")
        .prefetch_related("scores__criterion")
        .order_by("project__title", "id")
    )


def review_order_key(event_slug: str, judge_public_id: str, project_public_id: str) -> str:
    """Deterministic per-judge sort key for one assignment.

    Events are addressed by slug (their public identifier; Event has no
    public_id), so the key is sha256 of
    ``"{event.slug}:{judge.public_id}:{project.public_id}"``. Two judges with
    the same projects get different orders; the same judge always gets the same
    order, which spreads serial-position effects across projects instead of
    piling them onto the same teams.
    """
    return hashlib.sha256(
        f"{event_slug}:{judge_public_id}:{project_public_id}".encode("utf-8")
    ).hexdigest()


def order_judge_queue(rows: list[Assignment]) -> list[Assignment]:
    """Sort a judge's already-fetched assignments into review order.

    To-do (no review, or a draft) comes before submitted reviews; within each
    group rows follow :func:`review_order_key`. Everything is computed in
    Python over select_related/prefetched relations, so sorting adds no
    queries. Organizer tables are lookup tables, not a judging sequence, and
    keep their database order: only judge-facing queues use this.
    """
    def key(row: Assignment) -> tuple:
        review = row.review if hasattr(row, "review") else None
        submitted = review is not None and review.status == ReviewStatus.SUBMITTED
        return (
            row.event.slug,
            submitted,
            review_order_key(row.event.slug, row.judge.public_id, row.project.public_id),
        )

    return sorted(rows, key=key)


def visible_assignments(user, event: Event | None = None,
                        judge_public_id: str | None = None) -> QuerySet[Assignment]:
    """Assignments visible to an event organizer or to their assigned judge."""
    if user is None or not getattr(user, "is_authenticated", False):
        raise ApiError("not_authenticated", "Authentication is required.", status_code=401)
    if event is None and getattr(user, "is_admin", False):
        rows = Assignment.objects.all().select_related(
            "event", "judge__user", "project", "project__team", "project__track"
        ).prefetch_related("judge__tracks").order_by("event__slug", "id")
        return rows.filter(judge__public_id=judge_public_id) if judge_public_id else rows
    if event is not None and is_organizer(user, event):
        rows = (Assignment.objects.filter(event=event).select_related(
            "event", "judge__user", "project", "project__team", "project__track"
        ).filter(
            judge__event=event, project__event=event, project__team__event=event,
        ).filter(
            Q(project__track__isnull=True) | Q(project__track__event_id=F("event_id"))
        ).prefetch_related("judge__tracks").order_by("id"))
        return rows.filter(judge__public_id=judge_public_id) if judge_public_id else rows
    roles = EventRole.objects.filter(user=user, role=Role.JUDGE)
    if event is not None:
        role = require_judge(user, event)
        roles = roles.filter(pk=role.pk)
    role_ids = list(roles.values_list("pk", flat=True))
    if not role_ids:
        raise ApiError("forbidden", "Only judges can read assignments.", status_code=403)
    selected_ids = role_ids
    if judge_public_id:
        selected_ids = list(roles.filter(public_id=judge_public_id).values_list("pk", flat=True))
        if not selected_ids:
            raise ApiError("forbidden", "You can only read your own assignments.", status_code=403)
    rows = Assignment.objects.filter(
        judge_id__in=selected_ids, judge__tracks__id=F("project__track_id"),
        judge__event=F("event"), project__event=F("event_id"),
        project__team__event=F("event_id"),
    )
    rows = rows.filter(project__track__event_id=F("event_id"))
    if event is not None:
        rows = rows.filter(event=event)
    return (rows.select_related(
        "event", "judge__user", "project", "project__team", "project__track"
    ).prefetch_related("judge__tracks").order_by("event__slug", "id"))


def judge_can_view_project(user, event: Event, project: Project) -> bool:
    """Private project evidence is visible to organizers and assigned judges only."""
    if (project.event_id != event.pk or project.team.event_id != event.pk
            or (project.track_id and project.track.event_id != event.pk)):
        return False
    if is_organizer(user, event):
        return True
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return Assignment.objects.filter(
        event=event, project=project, judge__user=user, judge__role=Role.JUDGE,
        judge__tracks__id=project.track_id,
    ).exists()


def judge_project(user, event: Event, project_public_id: str) -> Project:
    """Resolve a private project only for its organizer or assigned in-track judge."""
    role = require_judge_or_manager(user, event)
    project = Project.objects.filter(event=event, public_id=project_public_id).select_related(
        "team", "track"
    ).prefetch_related("answers__question").first()
    if project is None:
        raise ApiError("project_not_found", "No project was found in this event.", status_code=404)
    if role is not None and not judge_can_view_project(user, event, project):
        raise ApiError("forbidden", "You are not assigned to this project.", status_code=403)
    return project


def progress(event: Event) -> dict:
    """Coverage counts submitted reviews as evidence, not mere assignments."""
    projects = list(Project.objects.filter(event=event, status=ProjectStatus.SUBMITTED)
                    .select_related("track"))
    assignments = Assignment.objects.filter(event=event).select_related("judge", "project").prefetch_related(
        "review"
    )
    assignments_by_judge: dict[int, list[Assignment]] = {}
    assignments_by_project: dict[int, list[Assignment]] = {}
    for row in assignments:
        assignments_by_judge.setdefault(row.judge_id, []).append(row)
        assignments_by_project.setdefault(row.project_id, []).append(row)

    roles = list(EventRole.objects.filter(event=event, role=Role.JUDGE).select_related("user")
                 .prefetch_related("tracks"))
    judges = []
    for role in roles:
        rows = assignments_by_judge.get(role.pk, [])
        submitted = sum(1 for row in rows if hasattr(row, "review") and row.review.status == ReviewStatus.SUBMITTED)
        drafts = sum(1 for row in rows if hasattr(row, "review") and row.review.status == ReviewStatus.DRAFT)
        assigned = len(rows)
        judges.append({
            "judge": role.public_id,
            "name": role.user.display_name or role.user.email.split("@")[0],
            "tracks": sorted(track.name for track in role.tracks.all()),
            "assigned": assigned,
            "submitted": submitted,
            "drafts": drafts,
            "remaining": assigned - submitted,
            "last_activity": max(
                (row.review.updated_at for row in rows if hasattr(row, "review")), default=None
            ),
            "status": ("no assignments" if assigned == 0 else "not started" if submitted == 0 and drafts == 0
                       else "done" if submitted == assigned else "in progress"),
        })
    judges.sort(key=lambda row: (row["status"] != "not started", row["name"].casefold(), row["judge"]))
    project_rows = []
    for project in projects:
        rows = assignments_by_project.get(project.pk, [])
        submitted = sum(1 for row in rows if hasattr(row, "review") and row.review.status == ReviewStatus.SUBMITTED)
        target = event.reviews_per_project
        project_rows.append({
            "project": project.public_id,
            "title": project.title,
            "track": project.track.public_id if project.track_id else None,
            "submitted": submitted,
            "target": target,
            "under_covered": submitted < target,
        })
    tracks = []
    for track in event.tracks.all().order_by("position", "id"):
        in_track = [row for row in project_rows if row["track"] == track.public_id]
        denominator = len(in_track) * event.reviews_per_project
        actual = sum(row["submitted"] for row in in_track)
        tracks.append({
            "track": track.public_id, "name": track.name, "projects": len(in_track),
            "submitted": actual, "target": denominator,
            "coverage_percent": round(actual * 100 / denominator, 1) if denominator else 100.0,
        })
    return {"judges": judges, "projects": project_rows, "tracks": tracks}
