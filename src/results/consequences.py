"""Exact, read-only comparisons for actions that change official results."""
from __future__ import annotations

from core.errors import ApiError
from events.models import Event
from judging.models import Review, ReviewExclusion, ReviewStatus
from judging.policy import review_values
from projects.models import Project, ProjectStatus
from results import services
from results.models import ResultPublication


def what_if(
    event: Event,
    *,
    disqualify: tuple[str, ...] = (),
    exclude_reviews: tuple[str, ...] = (),
    include_reviews: tuple[str, ...] = (),
) -> dict:
    """Return the exact normal preview for a hypothetical in-memory result change."""
    inc, exc = services._build_input_lists(event)
    eligible = list(
        Project.objects.filter(event=event, status=ProjectStatus.SUBMITTED)
        .values_list("public_id", flat=True)
    )
    status_overrides: dict[str, str] = {}

    for project_id in disqualify:
        project = Project.objects.filter(event=event, public_id=project_id).first()
        if project is None:
            raise ApiError("project_not_found", "No such project.", status_code=404)
        if project.status in (ProjectStatus.DISQUALIFIED, ProjectStatus.SUPERSEDED):
            raise ApiError(
                "not_disqualifiable",
                f"This project is already {project.get_status_display().lower()}.",
                status_code=409,
            )
        status_overrides[project_id] = ProjectStatus.DISQUALIFIED
        if project_id in eligible:
            eligible.remove(project_id)
        inc = [row for row in inc if row["project_id"] != project_id]

    for review_id in exclude_reviews:
        review = _review_or_404(event, review_id)
        if review.status != ReviewStatus.SUBMITTED:
            raise ApiError(
                "review_not_submitted", "Only submitted reviews can be excluded.",
                status_code=409,
            )
        if ReviewExclusion.objects.filter(review=review).exists():
            raise ApiError("already_excluded", "This review is already excluded.", status_code=409)
        inc = [row for row in inc if row["review_id"] != review_id]
        exc.append({
            "review_id": review.public_id,
            "judge_id": review.judge.public_id,
            "project_id": review.project.public_id,
            "reason": "",
        })

    for review_id in include_reviews:
        review = _review_or_404(event, review_id)
        exclusion = ReviewExclusion.objects.filter(review=review).first()
        if exclusion is None:
            raise ApiError("review_not_excluded", "This review is not excluded.", status_code=409)
        if review.status != ReviewStatus.SUBMITTED:
            raise ApiError(
                "review_not_submitted", "Only submitted reviews can be included.",
                status_code=409,
            )
        exc = [row for row in exc if row["review_id"] != review_id]
        if review.project.public_id in eligible:
            inc.append({
                "review_id": review.public_id,
                "judge_id": review.judge.public_id,
                "project_id": review.project.public_id,
                "criteria": review_values(review),
                "rubric_version": review.rubric_version,
            })

    return services._preview_from(
        event, inc, exc, eligible, project_status_overrides=status_overrides
    )


def consequences(event: Event, action: str, target: str | None = None) -> dict:
    """Compare current official results with the exact result after one action."""
    if action == "publish":
        if target is not None:
            raise ApiError("invalid", "Publish does not accept a target.", status_code=400)
        before_publication = ResultPublication.objects.filter(event=event).order_by("-version").first()
        after = services.preview(event)
        if before_publication is None:
            ranked = sum(row.get("status") == "ranked" for row in after["rows"])
            return {
                "action": action,
                "target": None,
                "basis_digest": after["input_digest"],
                "rank_changes": [],
                "award_changes": [],
                "winner_before": None,
                "winner_after": _winner(after["rows"]),
                "n_rank_changes": 0,
                "n_award_changes": 0,
                "sentence": (
                    f"First publication: {ranked} ranked project"
                    f"{'' if ranked == 1 else 's'}, {len(after['awards'])} award"
                    f"{'' if len(after['awards']) == 1 else 's'}."
                ),
            }
        before_rows = before_publication.rows
        before_awards = before_publication.awards
        basis_digest = after["input_digest"]
    else:
        if action not in {"disqualify", "exclude_review", "include_review"}:
            raise ApiError("invalid", "Unknown consequence action.", status_code=400)
        if not target:
            raise ApiError("invalid", "A target is required for this action.", status_code=400)
        before = services.preview(event)
        before_rows, before_awards = before["rows"], before["awards"]
        basis_digest = before["input_digest"]
        if action == "disqualify":
            after = what_if(event, disqualify=(target,))
        elif action == "exclude_review":
            after = what_if(event, exclude_reviews=(target,))
        else:
            after = what_if(event, include_reviews=(target,))

    rank_changes = _rank_changes(before_rows, after["rows"])
    award_changes = _award_changes(before_awards, after["awards"])
    winner_before, winner_after = _winner(before_rows), _winner(after["rows"])
    sentence = _sentence(action, target, rank_changes, award_changes, winner_before, winner_after)
    return {
        "action": action,
        "target": target,
        "basis_digest": basis_digest,
        "rank_changes": rank_changes,
        "award_changes": award_changes,
        "winner_before": winner_before,
        "winner_after": winner_after,
        "n_rank_changes": len(rank_changes),
        "n_award_changes": len(award_changes),
        "sentence": sentence,
    }


def _review_or_404(event: Event, review_id: str) -> Review:
    review = (
        Review.objects.filter(event=event, public_id=review_id)
        .select_related("judge", "project")
        .first()
    )
    if review is None:
        raise ApiError("review_not_found", "No review was found.", status_code=404)
    return review


def _rank_label(row: dict) -> str:
    rank = row.get("rank")
    if rank:
        return str(rank)
    status = row.get("status", "")
    return "unranked" if status.startswith("unranked") else status or "unranked"


def _rank_changes(before: list[dict], after: list[dict]) -> list[dict]:
    old = {row["project_id"]: row for row in before}
    new = {row["project_id"]: row for row in after}
    changes = []
    for project_id in sorted(old.keys() | new.keys()):
        previous = old.get(project_id)
        current = new.get(project_id)
        old_rank = _rank_label(previous) if previous else "not ranked"
        new_rank = _rank_label(current) if current else "not ranked"
        if old_rank != new_rank:
            row = current or previous
            changes.append({
                "project": project_id,
                "title": row["title"],
                "before": old_rank,
                "after": new_rank,
            })
    return changes


def _award_rows(awards: list[dict]) -> dict[str, tuple[str, list[dict]]]:
    result: dict[str, tuple[str, list[dict]]] = {}
    for award in awards:
        prize_id = award["prize_id"]
        prize_name, projects = result.setdefault(prize_id, (award.get("prize", ""), []))
        if award.get("project_id"):
            projects.append({"project": award["project_id"], "title": award.get("project", "")})
    return result


def _award_changes(before: list[dict], after: list[dict]) -> list[dict]:
    old, new = _award_rows(before), _award_rows(after)
    changes = []
    for prize_id in sorted(old.keys() | new.keys()):
        old_name, old_projects = old.get(prize_id, ("", []))
        new_name, new_projects = new.get(prize_id, ("", []))
        if old_projects != new_projects or old_name != new_name:
            changes.append({
                "prize": prize_id,
                "prize_name": new_name or old_name,
                "before": old_projects,
                "after": new_projects,
            })
    return changes


def _winner(rows: list[dict]) -> dict[str, str] | None:
    firsts = [row for row in rows if row.get("status") == "ranked"
              and str(row.get("rank", "")).lstrip("=") == "1"]
    if not firsts:
        return None
    row = sorted(firsts, key=lambda item: item["title"].casefold())[0]
    return {"project": row["project_id"], "title": row["title"]}


def _sentence(
    action: str,
    target: str,
    ranks: list[dict],
    awards: list[dict],
    winner_before: dict[str, str] | None,
    winner_after: dict[str, str] | None,
) -> str:
    rank_count, award_count = len(ranks), len(awards)
    phrase = (
        f"{rank_count} rank{'s' if rank_count != 1 else ''} and "
        f"{award_count} award{'s' if award_count != 1 else ''}"
        if rank_count or award_count else "no rank and no award"
    )
    subject = {
        "disqualify": f"Disqualifying {_project_title(target, ranks)}",
        "exclude_review": "Excluding this review",
        "include_review": "Including this review",
        "publish": "Publishing results",
    }[action]
    sentence = f"{subject} changes {phrase}"
    if winner_before != winner_after and winner_before and winner_after:
        sentence += (
            f": Best overall moves from {winner_before['title']} "
            f"to {winner_after['title']}"
        )
    return sentence + "."


def _project_title(project_id: str, changes: list[dict]) -> str:
    for change in changes:
        if change["project"] == project_id:
            return change["title"]
    project = Project.objects.filter(public_id=project_id).only("title").first()
    return project.title if project else project_id
