"""CSV exports for the organizer.

One stage of the judging pipeline per file, built with core.csvutil so a cell
that starts with =, +, -, @ or a tab is neutralised before a spreadsheet can
execute it. Scores come from results.engine, the one implementation of the
judging maths, so a CSV can never disagree with the published results.
"""
from core.csvutil import write_csv
from core.errors import ApiError
from events.models import Event
from judging.policy import (engine_criteria, engine_reviews, included_reviews, review_values,
                            rubric_criteria, scored_reviews)
from projects.models import Project, ProjectStatus
from results import engine

RESULTS_STATUSES = (ProjectStatus.SUBMITTED,)
EXPORT_KINDS = ("reviews", "results")


def _track_name(project: Project) -> str:
    return project.track.name if project.track_id else ""


def _score_text(score: float | None) -> str:
    return "" if score is None else f"{score:.2f}"


def reviews_csv(event: Event) -> str:
    """Every review of the event with its criterion values, drafts included.

    Drafts are here on purpose: the organizer needs to see outstanding work, and
    the status column says which rows count.
    """
    criteria = rubric_criteria(event)
    reviews = (
        event.reviews.select_related("judge", "judge__user", "project", "project__track")
        .prefetch_related("scores__criterion")
        .order_by("judge__public_id", "project__title", "id")
    )
    header = [
        "review_id", "judge_id", "judge_name", "project_id", "project_title", "track", "status",
    ] + [c.key for c in criteria] + ["weighted_score", "comment"]
    rows = []
    for review, score in scored_reviews(reviews):
        values = review_values(review)
        rows.append(
            [
                review.public_id,
                review.judge.public_id,
                review.judge.user.display_name or review.judge.user.email.split("@")[0],
                review.project.public_id,
                review.project.title,
                _track_name(review.project),
                review.status,
            ]
            + [values.get(c.key, "") for c in criteria]
            + [_score_text(score), review.comment]
        )
    return write_csv(header, rows)


def _ranked_projects(event: Event) -> list[Project]:
    """Submitted projects only: withdrawn, disqualified, superseded and drafts are out."""
    return list(
        Project.objects.filter(event=event, status__in=RESULTS_STATUSES)
        .select_related("team", "track")
        .order_by("title", "id")
    )


def results_csv(event: Event) -> str:
    """Ranked projects with their review count and raw weighted mean (0-100).

    ``rank`` is the event's official ranking method; ``raw_mean`` is deliberately
    the un-normalized mean so an organizer can see what the offsets did.
    """
    projects = _ranked_projects(event)
    by_public_id = {project.public_id: project for project in projects}
    reviews = [review for review in included_reviews(event)
               if review.project.public_id in by_public_id]
    pairs = scored_reviews(reviews)
    result = engine.evaluate(
        engine_reviews(pairs),
        engine_criteria(event),
        lam=float(event.shrinkage_lambda),
        target=event.reviews_per_project,
        method=event.ranking_method,
        projects=list(by_public_id),
    )
    rows = []
    for project in projects:
        public_id = project.public_id
        mean, count = result.raw.get(public_id, (None, 0))
        rank = result.rank.get(public_id)
        rows.append([
            rank or "unranked",
            public_id,
            project.title,
            project.team.name,
            _track_name(project),
            count,
            _score_text(mean),
            "ranked" if rank else "unranked_no_reviews",
        ])
    rows.sort(key=lambda row: (_rank_order(row[0]), row[2].casefold(), row[1]))
    return write_csv(
        ["rank", "project_id", "title", "team", "track", "reviews", "raw_mean", "status"], rows
    )


def _rank_order(rank: str) -> tuple[int, int]:
    """"3" sorts before "=3" for the same position, unranked rows go last."""
    if rank == "unranked":
        return (2, 0)
    return (1 if rank.startswith("=") else 0, int(rank.lstrip("=")))


def export_csv(event: Event, kind: str) -> str:
    """Dispatch by kind; an unknown kind is a 404, not an empty file."""
    if kind not in EXPORT_KINDS:
        raise ApiError("unknown_export", f"No export called {kind!r}.", status_code=404,
                       fields={"kind": [f"Available: {', '.join(EXPORT_KINDS)}."]})
    return {"reviews": reviews_csv, "results": results_csv}[kind](event)
