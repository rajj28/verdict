"""Results services: preview, publish, feedback release and verification.

All write paths go through this module; views and API classes are thin
callers. Every publication is immutable once stored; a re-run supersedes
the previous one (BUILD-SPEC sections 5, 16).
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Callable
from typing import Any

from django.core.cache import cache
from django.db import transaction

import audit.services
from core.clock import now
from core.errors import ApiError
from events.models import Event
from events.policy import is_organizer
from judging.models import Review, ReviewExclusion, ReviewStatus
from judging.policy import engine_criteria, engine_reviews, included_reviews, review_values, scored_reviews
from judging.policy import included_comparisons
from projects.models import Project, ProjectStatus
from results import engine, prizes
from results.models import ResultPublication


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _lam_for_event(event: Event) -> float | str:
    """Return a numeric lambda or 'auto' when the event uses adaptive selection."""
    if event.shrinkage_lambda is None:
        return "auto"
    return float(event.shrinkage_lambda)


def _prize_specs(event: Event) -> list[prizes.PrizeSpec]:
    """Prizes as the pure allocator wants them, in position order."""
    return [
        prizes.PrizeSpec(
            prize_id=p.public_id,
            name=p.name,
            scope=p.scope,
            track_id=p.track.public_id if p.track_id else None,
            places=p.places,
            value=p.value,
            track_name=p.track.name if p.track_id else "",
            eligibility_note=p.eligibility_note,
            position=p.position,
        )
        for p in event.prizes.select_related("track").order_by("position", "id")
    ]


def _prize_config(event: Event) -> list[dict]:
    """Prize configuration in position order, as stored in the canonical inputs.

    Part of the digest: changing a prize after publication must show up
    when the live data is re-hashed.
    """
    return [
        {
            "prize_id": s.prize_id,
            "name": s.name,
            "scope": s.scope,
            "track_id": s.track_id,
            "track_name": s.track_name,
            "places": s.places,
            "position": s.position,
            "eligibility_note": s.eligibility_note,
        }
        for s in _prize_specs(event)
    ]


def _canonical_inputs(
    event: Event,
    included: list[dict],
    excluded: list[dict],
    params: dict,
    comparisons: list[dict] | None = None,
    *,
    eligible_project_ids: list[str] | None = None,
    project_status_overrides: dict[str, str] | None = None,
) -> dict:
    """The canonical input object stored and hashed on every publication."""
    inputs = {
        "event": event.slug,
        "method": params["method"],
        "lam": params["lam"],
        "lambda_source": params.get("lambda_source", "fixed"),
        "rubric_version": params["rubric_version"],
        "reviews_per_project": event.reviews_per_project,
        "included": sorted(included, key=lambda r: r["review_id"]),
        "excluded": sorted(excluded, key=lambda r: r["review_id"]),
        "projects": sorted(eligible_project_ids) if eligible_project_ids is not None else sorted(
            Project.objects.filter(event=event, status=ProjectStatus.SUBMITTED)
            .values_list("public_id", flat=True)
        ),
        "project_snapshot": _public_project_snapshot(event, project_status_overrides),
        "prizes": _prize_config(event),
        "one_prize_per_team": event.one_prize_per_team,
        "params": params,
    }
    if params.get("comparison_source") == "live":
        inputs["comparisons"] = comparisons if comparisons is not None else _comparison_inputs(event)
    return inputs


def _public_project_snapshot(
    event: Event, status_overrides: dict[str, str] | None = None,
) -> list[dict]:
    """Public display and status fields needed to replay and compare a publication."""
    overrides = status_overrides or {}
    return [
        {
            "project_id": project.public_id,
            "title": project.title,
            "team": project.team.name,
            "team_id": project.team.public_id,
            "track": project.track.name if project.track_id else None,
            "track_id": project.track.public_id if project.track_id else None,
            "status": (status_overrides or {}).get(project.public_id, project.status),
            "status_reason": project.status_reason or None,
        }
        for project in Project.objects.filter(event=event)
        .select_related("team", "track")
        .order_by("public_id")
        if overrides.get(project.public_id, project.status) != ProjectStatus.DRAFT
    ]


def _criteria_snapshot(event: Event) -> list[dict]:
    """Store scoring scales and weights so verification needs no live rubric."""
    return [
        {
            "key": criterion.key,
            "weight": float(criterion.weight),
            "min_score": criterion.min_score,
            "max_score": criterion.max_score,
        }
        for criterion in engine_criteria(event)
    ]


def _comparison_inputs(event: Event) -> list[dict]:
    return [{"comparison_id": item.public_id, "judge_id": item.judge.public_id,
             "left": item.left.public_id, "right": item.right.public_id,
             "winner": item.winner.public_id if item.winner_id else None,
             "created_at": item.created_at.isoformat()}
            for item in included_comparisons(event)]


def _engine_comparisons(comparisons: list[dict]) -> list[engine.Comparison]:
    return [engine.Comparison(item["winner"], item["right"] if item["winner"] == item["left"] else item["left"])
            for item in comparisons if item["winner"] is not None]


def _uses_live_pairwise(event: Event, comparisons: list[dict]) -> bool:
    return event.judging_mode in ("pairwise", "both") or event.ranking_method == "pairwise" or bool(comparisons)


def _live_pairwise_metadata(event: Event, result: engine.Result, comparisons: list[dict]) -> dict:
    return {"enabled": _uses_live_pairwise(event, comparisons), "comparisons": len(comparisons),
            "decisive": sum(item["winner"] is not None for item in comparisons),
            "abstentions": sum(item["winner"] is None for item in comparisons),
            "components": result.pairwise_components, "n_components": len(result.pairwise_components),
            "comparable": len(result.pairwise_components) <= 1, "official": result.method == "pairwise"}


def _comparison_params(event, criteria, enabled):
    if not enabled:
        return {}
    return {"comparison_source": "live", "pairwise_min_comparisons": event.pairwise_min_comparisons,
            "criteria": [{"key": criterion.key, "weight": criterion.weight,
                          "min_score": criterion.min_score, "max_score": criterion.max_score}
                         for criterion in criteria]}


def _digest(inputs: dict) -> str:
    canonical = json.dumps(inputs, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _live_digest(event: Event) -> str:
    """Recompute the digest from the live database to check for tampering."""
    inc, exc = _build_input_lists(event)
    rubric_version = _rubric_version(event)
    lam_val = _lam_for_event(event)
    params_stub = {
        "method": event.ranking_method,
        "lam": lam_val if lam_val != "auto" else None,
        "lambda_source": "auto" if lam_val == "auto" else "fixed",
        "rubric_version": rubric_version,
    }
    return _digest(_canonical_inputs(event, inc, exc, params_stub))


def check_expected_digest(event: Event, expected_digest: str | None) -> None:
    """Reject a confirmed write when its locked event no longer matches the preview."""
    if expected_digest is None:
        return
    if preview(event)["input_digest"] != expected_digest:
        raise ApiError(
            "stale_preview",
            "The results changed since you previewed this action. Review the new "
            "consequences and confirm again.",
            status_code=409,
        )


def _rubric_version(event: Event) -> int:
    try:
        return event.rubric.version
    except Exception:
        return 0


def _build_input_lists(event: Event) -> tuple[list[dict], list[dict]]:
    """Snapshot of included and excluded reviews in the canonical form."""
    # Included: submitted, non-excluded reviews of submitted projects
    inc_qs = (
        Review.objects.filter(
            event=event,
            status=ReviewStatus.SUBMITTED,
            exclusion__isnull=True,
            project__status=ProjectStatus.SUBMITTED,
        )
        .select_related("judge", "project")
        .prefetch_related("scores__criterion")
        .order_by("id")
    )
    included = []
    for review in inc_qs:
        included.append({
            "review_id": review.public_id,
            "judge_id": review.judge.public_id,
            "project_id": review.project.public_id,
            "criteria": review_values(review),
            "rubric_version": review.rubric_version,
        })

    # Excluded: submitted reviews that have an exclusion record
    exc_qs = (
        Review.objects.filter(event=event, status=ReviewStatus.SUBMITTED)
        .exclude(exclusion__isnull=True)
        .select_related("judge", "project", "exclusion")
        .order_by("id")
    )
    excluded = []
    for review in exc_qs:
        excluded.append({
            "review_id": review.public_id,
            "judge_id": review.judge.public_id,
            "project_id": review.project.public_id,
            "reason": review.exclusion.reason,
        })

    return included, excluded


def collect_inputs(event: Event) -> dict:
    """Gather all included and excluded reviews with metadata.

    Eligible: submitted reviews of submitted projects that are not excluded.
    Withdrawn/disqualified/superseded projects are listed separately with
    reasons so the decision record is complete.
    """
    inc, exc = _build_input_lists(event)
    non_submitted = list(
        Project.objects.filter(event=event)
        .exclude(status=ProjectStatus.SUBMITTED)
        .select_related("team", "track")
        .order_by("public_id")
    )
    ineligible = [
        {
            "project_id": p.public_id,
            "title": p.title,
            "status": p.status,
            "reason": p.status_reason or p.get_status_display(),
        }
        for p in non_submitted
    ]
    return {
        "included": inc,
        "excluded": exc,
        "ineligible_projects": ineligible,
    }


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def preview(event: Event) -> dict:
    """Run the engine over the current data and return a preview dict.

    Does not persist anything. The input_digest in the result lets the
    organizer see whether a subsequent publish would be based on the same
    data.
    """
    inc, exc = _build_input_lists(event)
    eligible_project_ids = list(
        Project.objects.filter(event=event, status=ProjectStatus.SUBMITTED)
        .values_list("public_id", flat=True)
    )
    return _preview_from(event, inc, exc, eligible_project_ids)


def _preview_from(
    event: Event,
    inc: list[dict],
    exc: list[dict],
    eligible_project_ids: list[str],
    *,
    project_status_overrides: dict[str, str] | None = None,
) -> dict:
    """Run the official computation from explicit inputs for live and hypothetical data."""
    comparisons = _comparison_inputs(event)
    live_enabled = _uses_live_pairwise(event, comparisons)
    criteria = engine_criteria(event)
    if not criteria and event.ranking_method != "pairwise":
        raise ApiError(
            "no_rubric",
            "This event has no rubric. Add criteria before previewing results.",
            status_code=409,
        )
    lam_val = _lam_for_event(event)
    rubric_version = _rubric_version(event)

    review_inputs = [
        engine.ReviewInput(
            review_id=r["review_id"],
            judge_id=r["judge_id"],
            project_id=r["project_id"],
            values=r["criteria"],
        )
        for r in inc
    ]
    all_submitted = list(eligible_project_ids)
    # 'auto' lambda selection requires at least one review; fall back to 2.0
    # when there are no reviews so preview doesn't crash on empty events.
    effective_lam: float | str = lam_val
    if lam_val == "auto" and not review_inputs:
        effective_lam = 2.0
    result = engine.evaluate(
        review_inputs,
        criteria,
        lam=effective_lam,
        target=event.reviews_per_project,
        method=event.ranking_method,
        projects=all_submitted,
        comparisons=_engine_comparisons(comparisons) if live_enabled else None,
    )
    if result.method == "pairwise" and len(result.pairwise_components) > 1:
        result.rank = {project: "unranked" for project in all_submitted}
    params = {
        "method": event.ranking_method,
        "lam": result.lam,
        "lambda_source": "auto" if lam_val == "auto" else "fixed",
        "rubric_version": rubric_version,
        "criteria": _criteria_snapshot(event),
        **_comparison_params(event, criteria, live_enabled),
    }
    if result.lambda_choice is not None:
        params["lambda_cv"] = {
            str(k): v for k, v in result.lambda_choice.cv_rmse.items()
        }
        params["lambda_cv_baseline_rmse"] = result.lambda_choice.baseline_rmse
        params["lambda_cv_n"] = result.lambda_choice.n
        params["lambda_cv_folds"] = result.lambda_choice.folds
    input_digest = _digest(_canonical_inputs(
        event, inc, exc, params, comparisons,
        eligible_project_ids=all_submitted,
        project_status_overrides=project_status_overrides,
    ))
    snapshot = _project_snapshot(result, event, project_status_overrides)
    prize_specs = _prize_specs(event)
    allocation = _allocate(result, event, snapshot, prize_specs)
    top_k = _uncertainty_top_k(prize_specs)
    if result.method == "normalized":
        uncertainty_result = engine.rank_uncertainty(
            result.scored, result.lam, top_k=top_k
        )
        uncertainty = _uncertainty_payload(uncertainty_result)
        uncertainty_rows = _uncertainty_row_values(uncertainty_result)
    else:
        uncertainty = _unavailable_uncertainty(top_k)
        uncertainty_rows = {}
    rows = _build_rows(result, snapshot, comparisons)
    decorate_uncertainty_rows(rows, uncertainty_rows)
    return {
        "method": result.method,
        "lam": result.lam,
        "lambda_choice": (
            {
                "value": result.lambda_choice.value,
                "cv_rmse": {str(k): v for k, v in result.lambda_choice.cv_rmse.items()},
                "baseline_rmse": result.lambda_choice.baseline_rmse,
                "folds": result.lambda_choice.folds,
                "n": result.lambda_choice.n,
            }
            if result.lambda_choice is not None
            else None
        ),
        "input_digest": input_digest,
        "rows": rows,
        "uncertainty": uncertainty,
        "live_pairwise": _live_pairwise_metadata(event, result, comparisons),
        "judge_rows": _build_judge_rows(result),
        "awards": [a.as_dict() for a in allocation.awards],
        "unawarded": [u.as_dict() for u in allocation.unawarded],
        "diagnostics": {
            "under_reviewed": result.diagnostics.under_reviewed,
            "single_review_judges": result.diagnostics.single_review_judges,
            "constant_scorers": result.diagnostics.constant_scorers,
            "n_components": result.diagnostics.n_components,
        },
        "outliers": [
            {
                "review_id": o.review_id,
                "judge_id": o.judge_id,
                "project_id": o.project_id,
                "score": o.score,
                "residual": o.residual,
                "sentence": o.sentence,
            }
            for o in result.outliers
        ],
        "spread": {"before": result.spread_before, "after": result.spread_after},
        "n_included": len(inc),
        "n_excluded": len(exc),
        "params": params,
    }


def _official_score(result: engine.Result, project_id: str) -> float | None:
    """The score the event's ranking method ranks on (prizes use this one)."""
    if result.method == "raw":
        entry = result.raw.get(project_id)
        return entry[0] if entry else None
    if result.method == "pairwise":
        if result.live_strengths is not None:
            return result.live_strengths.get(project_id) if len(result.pairwise_components) <= 1 else None
        return result.strengths.get(project_id)
    return result.normalized.get(project_id)


def _project_outcome(
    result: engine.Result, project: Project, status_override: str | None = None,
) -> tuple[str, float | None]:
    """Result status and official score for one project (score None unless ranked)."""
    raw_entry = result.raw.get(project.public_id)
    project_status = status_override or project.status
    if project_status == ProjectStatus.DRAFT:
        status = "draft"
    elif project_status == ProjectStatus.WITHDRAWN:
        status = "withdrawn"
    elif project_status == ProjectStatus.DISQUALIFIED:
        status = "disqualified"
    elif project_status == ProjectStatus.SUPERSEDED:
        status = "superseded"
    elif result.method == "pairwise" and result.live_strengths is not None:
        status = ("unranked_no_reviews" if result.live_strengths.get(project.public_id) is None else
                  "unranked_disconnected" if len(result.pairwise_components) > 1 else "ranked")
    elif raw_entry is None:
        status = "unranked_no_reviews"
    else:
        status = "ranked"
    score = _official_score(result, project.public_id) if status == "ranked" else None
    return status, score


def _project_snapshot(
    result: engine.Result, event: Event, status_overrides: dict[str, str] | None = None,
) -> list[tuple[Project, str, float | None]]:
    """Every project of the event with its result status and score (one query)."""
    projects = (
        Project.objects.filter(event=event)
        .select_related("team", "track")
        .order_by("public_id")
    )
    overrides = status_overrides or {}
    return [(p, *_project_outcome(result, p, overrides.get(p.public_id))) for p in projects]


def _rank_order(rank: str | None, title: str) -> tuple:
    """Display order: rank first, unranked last, ties and equal titles by name."""
    if rank is None or rank == "unranked":
        return (2, 0, title.casefold())
    return (0, int(rank.lstrip("=")), title.casefold())


def _build_rows(
    result: engine.Result, snapshot: list[tuple[Project, str, float | None]],
    comparisons: list[dict] = (),
) -> list[dict]:
    """Build public-facing rows with status classification."""
    rows = []
    comparison_counts = Counter(project for item in comparisons if item["winner"] is not None
                                for project in (item["left"], item["right"]))
    for project, status, _score in snapshot:
        if status == "draft":
            continue
        pid = project.public_id
        raw_tuple = result.raw.get(pid)
        rows.append({
            "project_id": pid,
            "title": project.title,
            "team": project.team.name,
            "track": project.track.name if project.track_id else None,
            "n_reviews": raw_tuple[1] if raw_tuple else 0,
            "raw_mean": raw_tuple[0] if raw_tuple else None,
            "normalized": result.normalized.get(pid),
            "rank": result.rank.get(pid),
            "rank_raw": result.rank_raw.get(pid),
            "rank_norm": result.rank_norm.get(pid),
            "rank_bt": result.rank_bt.get(pid),
            "live_strength": result.live_strengths.get(pid) if result.live_strengths is not None else None,
            "rank_live": result.rank_live.get(pid),
            "n_comparisons": comparison_counts[pid],
            "status": status,
            "status_reason": project.status_reason or None,
        })
    # Sort by primary rank, then title
    rows.sort(key=lambda row: _rank_order(row["rank"], row["title"]))
    return rows


def _allocation_inputs(
    result: engine.Result, snapshot: list[tuple[Project, str, float | None]]
) -> list[prizes.RankedProject]:
    """Snapshot rows as the pure allocator reads them, in official rank order."""
    rows = [
        prizes.RankedProject(
            project_id=project.public_id,
            title=project.title,
            team_id=project.team.public_id,
            team_name=project.team.name,
            track_id=project.track.public_id if project.track_id else None,
            track_name=project.track.name if project.track_id else None,
            score=score,
            rank=result.rank.get(project.public_id),
            status=status,
        )
        for project, status, score in snapshot
    ]
    rows.sort(key=lambda row: _rank_order(row.rank, row.title))
    return rows


def _allocate(
    result: engine.Result,
    event: Event,
    snapshot: list[tuple[Project, str, float | None]],
    specs: list[prizes.PrizeSpec] | None = None,
) -> prizes.Allocation:
    """Award the event's prizes from a computed result (pure allocation)."""
    return prizes.allocate(
        _allocation_inputs(result, snapshot),
        _prize_specs(event) if specs is None else specs,
        one_per_team=event.one_prize_per_team,
    )


def _uncertainty_top_k(specs: list[prizes.PrizeSpec]) -> int:
    """Count overall prize places for uncertainty, defaulting when none exist."""
    places = sum(spec.places for spec in specs if spec.scope == "overall")
    return places or 3


def _uncertainty_payload(uncertainty: engine.Uncertainty) -> dict:
    """Return serialized metadata, keeping project detail on result rows."""
    return {
        "available": uncertainty.available,
        "replicates": uncertainty.replicates,
        "seed": uncertainty.seed,
        "level": uncertainty.level,
        "top_k": uncertainty.top_k,
        "sigma": round(uncertainty.sigma, 2),
        "df": uncertainty.df,
        "summary": uncertainty.summary,
        "assumption": uncertainty.assumption,
        "reason": uncertainty.reason,
        "tied_pairs": [list(pair) for pair in uncertainty.tied_pairs],
        "groups": [list(group) for group in uncertainty.groups],
    }


def _uncertainty_row_values(uncertainty: engine.Uncertainty) -> dict[str, dict]:
    tied = set(uncertainty.tied_pairs)
    rows = {}
    for index, project_id in enumerate(uncertainty.order):
        project = uncertainty.projects[project_id]
        rows[project_id] = {
            "rank_low": project.rank_low,
            "rank_high": project.rank_high,
            "score_low": round(project.score_low, 2),
            "score_high": round(project.score_high, 2),
            "p_first": round(project.p_first, 3),
            "p_top": round(project.p_top, 3),
            "p_above_next": (
                round(project.p_above_next, 3)
                if project.p_above_next is not None else None
            ),
            "tied_with_next": (
                index + 1 < len(uncertainty.order)
                and (project_id, uncertainty.order[index + 1]) in tied
            ),
            "tied_with_previous": (
                index > 0
                and (uncertainty.order[index - 1], project_id) in tied
            ),
        }
    return rows


def _unavailable_uncertainty(top_k: int) -> dict:
    reason = "Rank intervals are computed for the normalized ranking only."
    return {
        "available": False,
        "replicates": 200,
        "seed": 20260929,
        "level": 0.90,
        "top_k": top_k,
        "sigma": 0.0,
        "df": 0,
        "summary": reason,
        "assumption": engine.UNCERTAINTY_ASSUMPTION,
        "reason": reason,
        "tied_pairs": [],
        "groups": [],
    }


def decorate_uncertainty_rows(rows: list[dict], row_values: dict[str, dict]) -> None:
    for row in rows:
        values = (
            row_values.get(row["project_id"])
            if row.get("status") == "ranked" else None
        )
        row.update(values or {
            "rank_low": None,
            "rank_high": None,
            "score_low": None,
            "score_high": None,
            "p_first": None,
            "p_top": None,
            "p_above_next": None,
            "tied_with_next": False,
            "tied_with_previous": False,
        })


def publication_uncertainty(pub: ResultPublication) -> tuple[dict, dict[str, dict]]:
    """Replay rank uncertainty from immutable publication inputs and cache it."""
    cache_key = f"results:uncertainty:{pub.public_id}:{pub.input_digest}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached["payload"], cached["rows"]

    stored_prizes = pub.inputs.get("prizes", [])
    top_k = sum(
        int(prize.get("places", 1))
        for prize in stored_prizes
        if prize.get("scope") == "overall"
    ) or 3
    if pub.method != "normalized":
        payload = _unavailable_uncertainty(top_k)
        row_values = {}
    else:
        params = pub.inputs.get("params", {})
        criteria = [
            engine.Criterion(
                key=item["key"],
                weight=float(item["weight"]),
                min_score=int(item["min_score"]),
                max_score=int(item["max_score"]),
            )
            for item in params["criteria"]
        ]
        reviews = [
            engine.ReviewInput(
                review_id=item["review_id"],
                judge_id=item["judge_id"],
                project_id=item["project_id"],
                values=item["criteria"],
            )
            for item in pub.inputs["included"]
        ]
        scored = engine.score_reviews(reviews, criteria)
        uncertainty = engine.rank_uncertainty(
            scored, float(params["lam"]), top_k=top_k
        )
        payload = _uncertainty_payload(uncertainty)
        row_values = _uncertainty_row_values(uncertainty)
    cache.set(cache_key, {"payload": payload, "rows": row_values}, timeout=None)
    return payload, row_values


def _build_judge_rows(result: engine.Result) -> list[dict]:
    """Judge table (organizer-only)."""
    return [
        {
            "judge_id": jr.judge_id,
            "n": jr.n,
            "mean": jr.mean,
            "offset": jr.offset,
            "label": jr.label,
            "spread": jr.spread,
        }
        for jr in sorted(result.judges.values(), key=lambda j: j.judge_id)
    ]


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

@transaction.atomic
def publish(
    actor,
    event: Event,
    note: str = "",
    acknowledge_unranked: bool = False,
    expected_digest: str | None = None,
) -> ResultPublication:
    """Snapshot the current results and create a new publication.

    Requires judging closed; voting closed if configured. Raises 409 for:
    - judging_open (judging not yet closed)
    - unranked_projects (eligible projects with no reviews, unless acknowledged)
    - no_rubric (no criteria defined)
    """
    # Re-check state under lock so two concurrent publishes cannot race.
    event_locked = Event.objects.select_for_update().get(pk=event.pk)
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise ApiError("not_authenticated", "Authentication is required.", status_code=401)
    if not is_organizer(actor, event_locked):
        raise ApiError("forbidden", "Only organizers of this event can publish results.", status_code=403)
    check_expected_digest(event_locked, expected_digest)
    stamp = now()
    if event_locked.judging_close_at is None or stamp < event_locked.judging_close_at:
        raise ApiError(
            "judging_open",
            "Judging must be closed before results can be published. "
            "Use 'Close judging' first.",
            status_code=409,
        )
    voting_configured = (
        event_locked.voting_open_at is not None
        or event_locked.voting_close_at is not None
        or hasattr(event_locked, "voting_config")
    )
    if voting_configured and (
        event_locked.voting_close_at is None or stamp < event_locked.voting_close_at
    ):
        raise ApiError(
            "voting_open",
            "Voting must close before results can be published.",
            status_code=409,
        )

    # Build inputs and engine result.
    inc, exc = _build_input_lists(event_locked)
    comparisons = _comparison_inputs(event_locked)
    live_enabled = _uses_live_pairwise(event_locked, comparisons)
    criteria = engine_criteria(event_locked)
    if not criteria and event_locked.ranking_method != "pairwise":
        raise ApiError(
            "no_rubric",
            "This event has no rubric. Add criteria before publishing results.",
            status_code=409,
        )
    lam_val = _lam_for_event(event_locked)
    rubric_version = _rubric_version(event_locked)

    review_inputs = [
        engine.ReviewInput(
            review_id=r["review_id"],
            judge_id=r["judge_id"],
            project_id=r["project_id"],
            values=r["criteria"],
        )
        for r in inc
    ]
    all_submitted = list(
        Project.objects.filter(event=event_locked, status=ProjectStatus.SUBMITTED)
        .values_list("public_id", flat=True)
    )
    # 'auto' lambda selection requires at least one review; fall back to 2.0
    # when there are no reviews so publish doesn't crash on empty events.
    effective_lam_publish: float | str = lam_val
    if lam_val == "auto" and not review_inputs:
        effective_lam_publish = 2.0
    result = engine.evaluate(
        review_inputs,
        criteria,
        lam=effective_lam_publish,
        target=event_locked.reviews_per_project,
        method=event_locked.ranking_method,
        projects=all_submitted,
        comparisons=_engine_comparisons(comparisons) if live_enabled else None,
    )
    if result.method == "pairwise" and len(result.pairwise_components) > 1:
        raise ApiError("pairwise_disconnected", "Live comparison groups are disconnected. "
                       "Connect them through additional comparisons before publishing an overall ranking.",
                       status_code=409)

    # Check for unranked eligible projects.
    unranked = [
        pid for pid in all_submitted
        if (_official_score(result, pid) is None)
    ]
    if unranked and not acknowledge_unranked:
        raise ApiError(
            "unranked_projects",
            f"{len(unranked)} eligible project(s) have no reviews and will be "
            "unranked. Pass acknowledge_unranked=true to confirm.",
            status_code=409,
            fields={"unranked_projects": unranked},
        )

    params: dict[str, Any] = {
        "method": event_locked.ranking_method,
        "lam": result.lam,
        "lambda_source": "auto" if lam_val == "auto" else "fixed",
        "rubric_version": rubric_version,
        "criteria": _criteria_snapshot(event_locked),
        **_comparison_params(event_locked, criteria, live_enabled),
    }
    if result.lambda_choice is not None:
        params["lambda_cv"] = {
            str(k): v for k, v in result.lambda_choice.cv_rmse.items()
        }
        params["lambda_cv_baseline_rmse"] = result.lambda_choice.baseline_rmse
        params["lambda_cv_n"] = result.lambda_choice.n
        params["lambda_cv_folds"] = result.lambda_choice.folds

    inputs = _canonical_inputs(event_locked, inc, exc, params, comparisons)
    digest = _digest(inputs)
    snapshot = _project_snapshot(result, event_locked)
    rows = _build_rows(result, snapshot, comparisons)
    judge_rows = _build_judge_rows(result)
    # Awards are proposed, never final: an exact tie at a cut stays
    # unawarded until an organizer decides (results.prizes).
    allocation = _allocate(result, event_locked, snapshot)
    awards = [a.as_dict() for a in allocation.awards]
    unawarded = [u.as_dict() for u in allocation.unawarded]

    previous = (
        ResultPublication.objects.filter(event=event_locked)
        .order_by("-version")
        .first()
    )
    if previous and not note:
        raise ApiError(
            "note_required",
            "A note is required when superseding an existing publication.",
            status_code=400,
        )

    version = (
        ResultPublication.objects.filter(event=event_locked)
        .order_by("-version")
        .values_list("version", flat=True)
        .first()
        or 0
    ) + 1
    pub = ResultPublication.objects.create(
        event=event_locked,
        version=version,
        method=event_locked.ranking_method,
        params=params,
        inputs=inputs,
        input_digest=digest,
        rows=rows,
        judge_rows=judge_rows,
        awards=awards,
        unawarded=unawarded,
        note=note,
        published_by=actor,
        supersedes=previous,
    )

    audit.services.record(
        actor,
        "results.published",
        event=event_locked,
        target=pub,
        summary=(
            f"Results published (method={event_locked.ranking_method}, "
            f"lam={result.lam:.4g}, digest={digest[:12]}…)"
        ),
        data={
            "pub_id": pub.public_id,
            "method": event_locked.ranking_method,
            "lam": result.lam,
            "digest": digest,
            "n_included": len(inc),
            "n_excluded": len(exc),
            "n_awards": len(awards),
            "n_unawarded": len(unawarded),
            "note": note,
        },
    )
    return pub


# ---------------------------------------------------------------------------
# Public results
# ---------------------------------------------------------------------------

def public_results(event: Event) -> dict:
    """The public projection of the latest publication.

    Judge identities, offsets, individual review details and comments are
    never included (BUILD-SPEC section 6).
    """
    pub = (
        ResultPublication.objects.filter(event=event)
        .order_by("-version")
        .first()
    )
    if pub is None:
        raise ApiError(
            "not_published",
            "Results have not been published yet.",
            status_code=404,
        )
    uncertainty, uncertainty_rows = publication_uncertainty(pub)
    history = [
        {
            "version": item.version,
            "published_at": item.published_at.isoformat(),
            "note": item.note,
            "input_digest": item.input_digest,
        }
        for item in ResultPublication.objects.filter(event=event).order_by("-version")
    ]
    # Strip judge data from rows – public rows only carry ranked fields.
    public_rows = []
    for row in pub.rows:
        if row.get("status") == "draft":
            continue
        public_rows.append({
            "rank": row.get("rank"),
            "project_id": row.get("project_id"),
            "title": row.get("title"),
            "team": row.get("team"),
            "track": row.get("track"),
            "n_reviews": row.get("n_reviews"),
            "raw_mean": row.get("raw_mean"),
            "normalized": row.get("normalized"),
            "live_strength": row.get("live_strength"),
            "rank_live": row.get("rank_live"),
            "n_comparisons": row.get("n_comparisons", 0),
            "status": row.get("status"),
            "status_reason": row.get("status_reason"),
        })
    decorate_uncertainty_rows(public_rows, uncertainty_rows)
    public_uncertainty = {
        key: value for key, value in uncertainty.items() if key not in {"sigma", "seed"}
    }
    return {
        "pub_id": pub.public_id,
        "version": pub.version,
        "published_at": pub.published_at.isoformat(),
        "supersedes_version": pub.supersedes.version if pub.supersedes_id else None,
        "note": pub.note,
        "method": pub.method,
        "lam": pub.params.get("lam"),
        "lambda_source": pub.params.get("lambda_source"),
        "rows": public_rows,
        "awards": pub.awards,
        "unawarded": pub.unawarded,
        "history": history,
        "uncertainty": public_uncertainty,
    }


# ---------------------------------------------------------------------------
# Decision record
# ---------------------------------------------------------------------------

def decision_record(pub: ResultPublication) -> dict:
    """Full organizer-only decision record for a publication (BUILD-SPEC 16)."""
    from audit.models import AuditEvent
    event = pub.event
    # Interventions from the audit log: window changes, exclusions, assignment batches.
    interventions = list(
        AuditEvent.objects.filter(
            event=event,
            action__in=[
                "event.window_changed",
                "judging.closed",
                "review.excluded",
                "review.exclusion_removed",
                "assignments.batch_created",
                "results.published",
            ],
        )
        .order_by("created_at")
        .values("action", "actor_label", "summary", "created_at", "data")
    )
    lam_source = pub.params.get("lambda_source", "fixed")
    lam_sentence = (
        f"λ chosen by 5-fold cross-validation = {pub.params.get('lam')}"
        if lam_source == "auto"
        else f"λ fixed at {pub.params.get('lam')} (pre-declared)"
    )
    rule_plain = (
        f"Ranking method: {pub.method}. "
        f"Scoring: additive judge-offset model with shrinkage penalty. "
        f"{lam_sentence}. "
        f"Rubric version: {pub.params.get('rubric_version')}. "
        "Reviews excluded from results are listed below with their reasons. "
        "Only submitted reviews of submitted projects are included. "
        "Prizes are awarded from the official ranking in prize order; a "
        "team that already won a higher prize is skipped for the next one, "
        "and an exact tie at a cut is left for an organizer to decide."
    )
    return {
        "pub_id": pub.public_id,
        "version": pub.version,
        "published_at": pub.published_at.isoformat(),
        "published_by": pub.published_by.display_name if pub.published_by else None,
        "method": pub.method,
        "params": pub.params,
        "input_digest": pub.input_digest,
        "rule_plain": rule_plain,
        "included_reviews": pub.inputs.get("included", []),
        "excluded_reviews": pub.inputs.get("excluded", []),
        "project_snapshot": pub.inputs.get("project_snapshot", []),
        "project_snapshot_backfilled": pub.project_snapshot_backfilled,
        "prizes": pub.inputs.get("prizes", []),
        "one_prize_per_team": pub.inputs.get("one_prize_per_team"),
        "awards": pub.awards,
        "unawarded": pub.unawarded,
        "interventions": [
            {
                "action": iv["action"],
                "actor": iv["actor_label"],
                "summary": iv["summary"],
                "at": iv["created_at"].isoformat() if iv["created_at"] else None,
                "data": iv["data"],
            }
            for iv in interventions
        ],
        "limitations": [
            "The public gallery is visible to judges before judging opens.",
            "The additive offset model assumes one connected judge–project graph; "
            f"this publication has {pub.params.get('n_components', '?')} component(s).",
            "Outlier reviews are flagged but never auto-excluded.",
            "Constant scorers are kept; their level is absorbed by their offset.",
        ],
        "supersedes": pub.supersedes.public_id if pub.supersedes_id else None,
        "note": pub.note,
    }


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

# Far above any weight, score, lambda or position the models can store and far
# below the float range, so converting a stored number can never overflow.
_NUMBER_LIMIT = 10**12


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and abs(value) <= _NUMBER_LIMIT


def _is_number(value: Any) -> bool:
    return _is_int(value) or (isinstance(value, float) and abs(value) <= _NUMBER_LIMIT)


def _is_optional_int(value: Any) -> bool:
    return value is None or _is_int(value)


def _is_text(value: Any) -> bool:
    return isinstance(value, str)


def _is_optional_text(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _is_values(value: Any) -> bool:
    return isinstance(value, dict) and all(_is_number(v) for v in value.values())


def _records(value: Any, **checks: Callable[[Any], bool]) -> bool:
    """A list of objects whose named fields each pass their check."""
    return isinstance(value, list) and all(
        isinstance(item, dict) and all(check(item.get(name)) for name, check in checks.items())
        for item in value
    )


def _malformed_inputs(pub: ResultPublication) -> str | None:
    """Name the stored field recomputation cannot use, or None when all are usable.

    Publications are persisted JSON, so verification treats them as
    untrusted: a malformed field fails verification instead of crashing it.
    Only shapes and number magnitudes are checked here; value ranges are left
    to the engine, which raises ValueError.
    """
    inputs = pub.inputs
    params = inputs.get("params") if isinstance(inputs, dict) else None
    if not isinstance(params, dict) or not _is_text(params.get("method")) or "rubric_version" not in params:
        return "params are not an object with a method and rubric version"
    if "lam" not in params or not (params["lam"] is None or _is_number(params["lam"])):
        return "params.lam is not a number"
    checks = [
        ("params.criteria", "criteria" in params and _records(
            params["criteria"], key=_is_text, weight=_is_number, min_score=_is_int, max_score=_is_int)),
        ("reviews_per_project", _is_int(inputs.get("reviews_per_project"))),
        ("included reviews", _records(inputs.get("included", []), review_id=_is_text, judge_id=_is_text,
                                      project_id=_is_text, criteria=_is_values)),
        ("projects", "projects" not in inputs or (
            isinstance(inputs["projects"], list) and all(map(_is_text, inputs["projects"])))),
        ("project snapshot", _records(
            inputs.get("project_snapshot", []), project_id=_is_text, title=_is_text,
            team=_is_text, status=_is_text, status_reason=_is_optional_text,
        )),
        ("comparisons", inputs.get("comparisons") is None or _records(
            inputs["comparisons"], left=_is_text, right=_is_text, winner=_is_optional_text)),
        ("prizes", inputs.get("prizes") is None or _records(
            inputs["prizes"], prize_id=_is_text, name=_is_text, scope=_is_text, places=_is_int,
            track_id=_is_optional_text, position=_is_optional_int)),
        ("rows", _records(pub.rows, project_id=_is_text)),
    ]
    return next((f"{name} are malformed" for name, ok in checks if not ok), None)


def _unverifiable(pub: ResultPublication, reason: str) -> dict:
    """An explicit failed verification for stored inputs that cannot be recomputed."""
    return {
        "pub_id": pub.public_id,
        "verdict": "differs",
        "detail": f"Stored publication cannot be recomputed: {reason}.",
        "reproducible": {
            "verdict": "not reproducible",
            "matches": False,
            "detail": f"Stored publication cannot be recomputed: {reason}.",
        },
        "unchanged_since_publication": {
            "verdict": "not checked",
            "matches": False,
            "differences": [],
        },
        "rows_match": False,
        "awards_match": False,
        "digest_match": False,
        "stored_inputs_match": False,
        "stored_digest": pub.input_digest,
        "live_digest": None,
    }


def _unscored_review(inc: list[dict], criteria: list[engine.Criterion]) -> str | None:
    """The first included review lacking a value for some criterion, if any."""
    return next((r["review_id"] for r in inc if any(c.key not in r["criteria"] for c in criteria)), None)


def _finite_result(result: engine.Result) -> bool:
    """False when stored numbers overflowed to inf or NaN in a value the rows read."""
    values = [result.lam, *result.normalized.values(), *(mean for mean, _n in result.raw.values()),
              *(result.live_strengths or {}).values()]
    return all(value is None or math.isfinite(value) for value in values)


def _row_projection(rows: list[dict], live_pairwise: bool) -> list[dict]:
    """The recomputable fields of each row, in project id order.

    Duplicates are kept, so a repeated row cannot pass for one; the display
    order of the stored rows is not compared.
    """
    fields = (
        "project_id", "title", "team", "track", "n_reviews", "raw_mean", "normalized",
        "rank", "rank_raw", "rank_norm", "rank_bt", "live_strength", "rank_live",
        "n_comparisons", "status", "status_reason",
    )
    return sorted(({name: row.get(name) for name in fields} for row in rows), key=lambda row: row["project_id"])


def _stored_project_status(result: engine.Result, project: dict) -> str:
    """Result status from stored public status plus recomputed scores."""
    status = project["status"]
    if status != ProjectStatus.SUBMITTED:
        return status
    project_id = project["project_id"]
    if result.method == "pairwise" and result.live_strengths is not None:
        if result.live_strengths.get(project_id) is None:
            return "unranked_no_reviews"
        if len(result.pairwise_components) > 1:
            return "unranked_disconnected"
        return "ranked"
    return "unranked_no_reviews" if project_id not in result.raw else "ranked"


def _rows_from_stored_snapshot(
    result: engine.Result, project_snapshot: list[dict], comparisons: list[dict]
) -> list[dict]:
    """Build result rows without consulting the current Project or Team roster."""
    comparison_counts = Counter(
        project_id
        for item in comparisons if item.get("winner") is not None
        for project_id in (item["left"], item["right"])
    )
    rows = []
    for project in project_snapshot:
        if project["status"] == ProjectStatus.DRAFT:
            continue
        project_id = project["project_id"]
        raw_tuple = result.raw.get(project_id)
        status = _stored_project_status(result, project)
        rows.append({
            "project_id": project_id,
            "title": project["title"],
            "team": project["team"],
            "track": project.get("track"),
            "n_reviews": raw_tuple[1] if raw_tuple else 0,
            "raw_mean": raw_tuple[0] if raw_tuple else None,
            "normalized": result.normalized.get(project_id),
            "rank": result.rank.get(project_id),
            "rank_raw": result.rank_raw.get(project_id),
            "rank_norm": result.rank_norm.get(project_id),
            "rank_bt": result.rank_bt.get(project_id),
            "live_strength": result.live_strengths.get(project_id)
            if result.live_strengths is not None else None,
            "rank_live": result.rank_live.get(project_id),
            "n_comparisons": comparison_counts[project_id],
            "status": status,
            "status_reason": project.get("status_reason"),
        })
    rows.sort(key=lambda row: _rank_order(row["rank"], row["title"]))
    return rows


def _allocation_inputs_from_snapshot(
    result: engine.Result, project_snapshot: list[dict]
) -> list[prizes.RankedProject]:
    rows = []
    for project in project_snapshot:
        project_id = project["project_id"]
        status = _stored_project_status(result, project)
        rows.append(prizes.RankedProject(
            project_id=project_id,
            title=project["title"],
            team_id=project.get("team_id") or project_id,
            team_name=project["team"],
            track_id=project.get("track_id"),
            track_name=project.get("track"),
            score=_official_score(result, project_id) if status == "ranked" else None,
            rank=result.rank.get(project_id),
            status=status,
        ))
    rows.sort(key=lambda row: _rank_order(row.rank, row.title))
    return rows


def _projected_differences(stored: dict, live: dict) -> list[str]:
    """Describe canonical field changes without treating corrections as tampering."""
    differences: list[str] = []
    stored_reviews = {
        row.get("review_id"): (section, row)
        for section in ("included", "excluded")
        for row in stored.get(section, [])
    }
    live_reviews = {
        row.get("review_id"): (section, row)
        for section in ("included", "excluded")
        for row in live.get(section, [])
    }
    for review_id in sorted(set(stored_reviews) | set(live_reviews)):
        old = stored_reviews.get(review_id)
        new = live_reviews.get(review_id)
        if old is None:
            differences.append(f"review {review_id} added after publication")
        elif new is None:
            project_id = old[1].get("project_id")
            live_project = next(
                (project for project in live.get("project_snapshot", [])
                 if project.get("project_id") == project_id),
                None,
            )
            if live_project and live_project.get("status") in (
                ProjectStatus.DISQUALIFIED, ProjectStatus.WITHDRAWN, ProjectStatus.SUPERSEDED
            ):
                differences.append(f"review {review_id} excluded after publication")
            else:
                differences.append(f"review {review_id} removed after publication")
        elif old[0] != new[0]:
            differences.append(
                f"review {review_id} {'excluded' if new[0] == 'excluded' else 'included'} "
                "after publication"
            )
        else:
            for field in sorted(set(old[1]) | set(new[1])):
                if field == "criteria" and old[1].get(field) != new[1].get(field):
                    old_criteria = old[1].get(field) or {}
                    new_criteria = new[1].get(field) or {}
                    for key in sorted(set(old_criteria) | set(new_criteria)):
                        if old_criteria.get(key) != new_criteria.get(key):
                            differences.append(
                                f"review {review_id} criterion {key} changed after publication"
                            )
                elif old[1].get(field) != new[1].get(field):
                    differences.append(f"review {review_id} {field} changed after publication")

    stored_projects = {row["project_id"]: row for row in stored.get("project_snapshot", [])}
    live_projects = {row["project_id"]: row for row in live.get("project_snapshot", [])}
    for project_id in sorted(set(stored_projects) | set(live_projects)):
        old = stored_projects.get(project_id)
        new = live_projects.get(project_id)
        if old is None:
            differences.append(f"project {project_id} added after publication")
        elif new is None:
            differences.append(f"project {project_id} removed after publication")
        else:
            if old.get("status") != new.get("status") and new.get("status") in (
                ProjectStatus.DISQUALIFIED, ProjectStatus.WITHDRAWN, ProjectStatus.SUPERSEDED
            ):
                differences.append(
                    f"project {project_id} {new['status']} after publication"
                )
            for field in ("title", "team", "track", "status", "status_reason"):
                if old.get(field) != new.get(field):
                    differences.append(f"project {project_id} {field} changed after publication")

    ignored = {"included", "excluded", "project_snapshot"}
    for key in sorted((set(stored) | set(live)) - ignored):
        differences.extend(_canonical_field_differences(
            stored.get(key), live.get(key), key
        ))
    return differences


def _canonical_field_differences(old: Any, new: Any, path: str) -> list[str]:
    """Flatten remaining canonical changes to their leaf fields."""
    if old == new:
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        return [
            difference
            for key in sorted(set(old) | set(new))
            for difference in _canonical_field_differences(
                old.get(key), new.get(key), f"{path}.{key}"
            )
        ]
    if isinstance(old, list) and isinstance(new, list):
        identity = next(
            (field for field in ("review_id", "project_id", "prize_id", "comparison_id", "key")
             if old + new and all(isinstance(item, dict) and field in item for item in old + new)),
            None,
        )
        if identity:
            old_items = {item[identity]: item for item in old}
            new_items = {item[identity]: item for item in new}
            return [
                difference
                for key in sorted(set(old_items) | set(new_items))
                for difference in _canonical_field_differences(
                    old_items.get(key), new_items.get(key), f"{path}.{key}"
                )
            ]
        return [
            difference
            for index in range(max(len(old), len(new)))
            for difference in _canonical_field_differences(
                old[index] if index < len(old) else None,
                new[index] if index < len(new) else None,
                f"{path}[{index}]",
            )
        ]
    return [f"{path} changed after publication"]


def _live_canonical_inputs(pub: ResultPublication) -> dict:
    """Project current database inputs in the same shape as the stored snapshot."""
    event = pub.event
    included, excluded = _build_input_lists(event)
    comparisons = _comparison_inputs(event)
    params = dict(pub.inputs.get("params", {}))
    params.update({
        "method": event.ranking_method,
        "rubric_version": _rubric_version(event),
        "criteria": _criteria_snapshot(event),
        "lambda_source": "auto" if _lam_for_event(event) == "auto" else "fixed",
    })
    live_lam = _lam_for_event(event)
    if live_lam != "auto":
        params["lam"] = live_lam
    return _canonical_inputs(event, included, excluded, params, comparisons)


def verify_publication(pub: ResultPublication) -> dict:
    """Return independent historical-replay and live-projection verdicts."""
    problem = _malformed_inputs(pub)
    if problem is not None:
        return _unverifiable(pub, problem)
    stored_inputs = pub.inputs
    inc = stored_inputs.get("included", [])
    exc = stored_inputs.get("excluded", [])
    params = stored_inputs.get("params", {})
    criteria_dicts = params.get("criteria", [])

    criteria = [
        engine.Criterion(
            key=c["key"],
            weight=float(c["weight"]),
            min_score=int(c["min_score"]),
            max_score=int(c["max_score"]),
        )
        for c in criteria_dicts
    ]
    unscored = _unscored_review(inc, criteria)
    if unscored is not None:
        return _unverifiable(pub, f"included review {unscored} lacks a score for a criterion")

    lam_src = params.get("lambda_source", "fixed")
    lam_stored = params.get("lam")
    if lam_src == "auto" and lam_stored is not None:
        lam_val: float | str = float(lam_stored)
    elif lam_stored is not None:
        lam_val = float(lam_stored)
    else:
        lam_val = "auto"

    review_inputs = [
        engine.ReviewInput(
            review_id=r["review_id"],
            judge_id=r["judge_id"],
            project_id=r["project_id"],
            values=r["criteria"],
        )
        for r in inc
    ]
    project_snapshot = stored_inputs.get("project_snapshot", [])
    all_project_ids = stored_inputs.get(
        "projects",
        [project["project_id"] for project in project_snapshot
         if project.get("status") == ProjectStatus.SUBMITTED],
    )
    comparisons = stored_inputs.get("comparisons")
    try:
        result = engine.evaluate(
            review_inputs,
            criteria,
            lam=lam_val,
            target=stored_inputs["reviews_per_project"],
            method=params.get("method", pub.method),
            projects=all_project_ids,
            comparisons=_engine_comparisons(comparisons) if comparisons is not None else None,
        )
        if not _finite_result(result):
            return _unverifiable(pub, "recomputed scores are not finite")
        awards_match = _verify_awards(pub, result, project_snapshot)
    except ValueError as error:
        # Engine and allocator reject out-of-domain stored values (negative
        # lambda, unknown method, scores off the scale): fail, never crash.
        return _unverifiable(pub, str(error))

    live_pairwise = comparisons is not None
    rows_match = (
        _row_projection(
            _rows_from_stored_snapshot(result, project_snapshot, comparisons or ()), live_pairwise
        )
        == _row_projection(pub.rows, live_pairwise)
    )
    stored_inputs_match = _digest(stored_inputs) == pub.input_digest

    live_inputs = _live_canonical_inputs(pub)
    live_digest = _digest(live_inputs)
    digest_match = live_digest == pub.input_digest
    differences = _projected_differences(stored_inputs, live_inputs)

    reproducible = rows_match and stored_inputs_match and awards_match is not False
    if reproducible:
        reproducible_detail = "Stored inputs reproduce the publication rows and awards."
    else:
        reasons = [
            text for text, failed in (
                ("recomputed rows differ from stored rows", not rows_match),
                ("recomputed awards differ from stored awards", awards_match is False),
                ("stored inputs do not hash to the stored digest", not stored_inputs_match),
            ) if failed
        ]
        reproducible_detail = "; ".join(reasons).capitalize() + "."
    if stored_inputs_match:
        unchanged = digest_match and not differences
        live_detail = "Unchanged since publication." if unchanged else "Live inputs changed since publication."
    else:
        # The stored copy failed its own digest, so stored-vs-live differences cannot be blamed
        # on the live data; the live database is judged only against the published digest.
        unchanged = digest_match
        live_detail = (
            "Live data still matches the published digest; the stored copy was altered."
            if digest_match
            else "Live data also differs from the published digest."
        )

    return {
        "pub_id": pub.public_id,
        "verdict": "identical" if reproducible and unchanged else "differs",
        "detail": f"Reproducible: {reproducible_detail} {live_detail}",
        "reproducible": {
            "verdict": "reproducible" if reproducible else "not reproducible",
            "matches": reproducible,
            "detail": reproducible_detail,
        },
        "unchanged_since_publication": {
            "verdict": "unchanged since publication" if unchanged else "changed since publication",
            "matches": unchanged,
            "differences": differences,
        },
        "rows_match": rows_match,
        "awards_match": awards_match,
        "digest_match": digest_match,
        "stored_inputs_match": stored_inputs_match,
        "stored_digest": pub.input_digest,
        "live_digest": live_digest,
    }


def _verify_awards(
    pub: ResultPublication, result: engine.Result, project_snapshot: list[dict]
) -> bool | None:
    """True when the re-run allocation equals the stored one, None when absent."""
    stored_prizes = pub.inputs.get("prizes")
    if stored_prizes is None:
        return None
    specs = [
        prizes.PrizeSpec(
            prize_id=p["prize_id"],
            name=p["name"],
            scope=p["scope"],
            track_id=p.get("track_id"),
            places=p["places"],
            track_name=p.get("track_name", ""),
            position=p.get("position", 0),
            eligibility_note=p.get("eligibility_note", ""),
        )
        for p in stored_prizes
    ]
    recomputed = prizes.allocate(
        _allocation_inputs_from_snapshot(result, project_snapshot),
        specs,
        one_per_team=bool(pub.inputs.get("one_prize_per_team", True)),
    )  # the stored policy, not today's
    return (
        [a.as_dict() for a in recomputed.awards] == pub.awards
        and [u.as_dict() for u in recomputed.unawarded] == pub.unawarded
    )


# ---------------------------------------------------------------------------
# Feedback release
# ---------------------------------------------------------------------------

@transaction.atomic
def release_feedback(actor, event: Event) -> ResultPublication:
    """Mark the latest publication as feedback-released (organizer, audited)."""
    pub = (
        ResultPublication.objects.select_for_update()
        .filter(event=event)
        .order_by("-published_at")
        .first()
    )
    if pub is None:
        raise ApiError(
            "not_published",
            "No results have been published for this event.",
            status_code=409,
        )
    if pub.feedback_released_at is not None:
        raise ApiError(
            "already_released",
            "Feedback is already released for this publication.",
            status_code=409,
        )
    pub.feedback_released_at = now()
    pub.feedback_released_by = actor
    pub.save(update_fields=["feedback_released_at", "feedback_released_by"])
    audit.services.record(
        actor,
        "results.feedback_released",
        event=event,
        target=pub,
        summary=f"Feedback released for publication {pub.public_id}.",
    )
    return pub


@transaction.atomic
def retract_feedback(actor, event: Event) -> ResultPublication:
    """Remove the feedback-released flag (organizer, audited)."""
    pub = (
        ResultPublication.objects.select_for_update()
        .filter(event=event)
        .order_by("-published_at")
        .first()
    )
    if pub is None:
        raise ApiError(
            "not_published",
            "No results have been published for this event.",
            status_code=409,
        )
    if pub.feedback_released_at is None:
        raise ApiError(
            "not_released",
            "Feedback is not currently released for this publication.",
            status_code=409,
        )
    pub.feedback_released_at = None
    pub.feedback_released_by = None
    pub.save(update_fields=["feedback_released_at", "feedback_released_by"])
    audit.services.record(
        actor,
        "results.feedback_retracted",
        event=event,
        target=pub,
        summary=f"Feedback retracted for publication {pub.public_id}.",
    )
    return pub


def project_feedback(event: Event, project: Project) -> dict:
    """De-attributed feedback for a project: score, rank and per-criterion averages.

    Review.comment is collected as private to the judge and the organizers
    (judge/review.html), so it never reaches team feedback, even after
    release. ``comments`` stays in the payload, always empty, to keep the API
    shape; team-facing notes would need their own separately consented field.
    """
    pub = (
        ResultPublication.objects.filter(event=event)
        .order_by("-published_at")
        .first()
    )
    if pub is None:
        raise ApiError(
            "not_published",
            "Results have not been published yet.",
            status_code=404,
        )
    # Find the project's row in the publication.
    pub_row = next(
        (r for r in pub.rows if r.get("project_id") == project.public_id), None
    )
    if pub_row is None:
        return {
            "pub_id": pub.public_id,
            "feedback_released": pub.feedback_released_at is not None,
            "official_score": None,
            "rank": None,
            "n_reviews": 0,
            "per_criterion": {},
            "comments": [],
        }
    # Gather reviews for this project from the included inputs.
    review_ids = {
        r["review_id"]
        for r in pub.inputs.get("included", [])
        if r.get("project_id") == project.public_id
    }
    per_criterion: dict[str, list[int]] = {}
    for review in pub.inputs.get("included", []):
        if review.get("review_id") not in review_ids:
            continue
        for key, value in review.get("criteria", {}).items():
            per_criterion.setdefault(key, []).append(value)
    criterion_averages = {
        key: sum(vals) / len(vals) for key, vals in per_criterion.items()
    }
    return {
        "pub_id": pub.public_id,
        "feedback_released": pub.feedback_released_at is not None,
        "official_score": pub_row.get("normalized"),
        "rank": pub_row.get("rank"),
        "n_reviews": pub_row.get("n_reviews", 0),
        "per_criterion": criterion_averages,
        "comments": [],
    }
