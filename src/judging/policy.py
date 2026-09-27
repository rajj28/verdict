"""Rubric, judges, assignments, reviews and scores.

Read scoping and permission predicates. Every queryset in a view starts here.
"""
from django.db.models import QuerySet

from events.models import Event, EventRole
from events.policy import is_organizer
from judging.models import Criterion, Review, ReviewStatus
from results import engine


def rubric_criteria(event: Event) -> list[Criterion]:
    return list(Criterion.objects.filter(rubric__event=event).order_by("position", "id"))


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
        Review.objects.filter(judge__user=user)
        .select_related("event", "judge", "project")
        .prefetch_related("scores__criterion")
        .order_by("event__slug", "project__title", "id")
    )


def visible_reviews(user, event: Event | None = None) -> QuerySet[Review]:
    """Reviews the caller may read: a judge sees their own, organizers see all."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Review.objects.none()
    if event is None or is_organizer(user, event):
        return (
            Review.objects.all()
            .select_related("event", "judge", "project")
            .prefetch_related("scores__criterion")
            .order_by("event__slug", "id")
        )
    return judge_reviews(user)


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
