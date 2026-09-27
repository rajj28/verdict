"""Results services: preview, publish, feedback release and verification.

All write paths go through this module; views and API classes are thin
callers. Every publication is immutable once stored; a re-run supersedes
the previous one (BUILD-SPEC sections 5, 16).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from django.db import transaction

import audit.services
from core.clock import now
from core.errors import ApiError
from events.models import Event
from events.policy import is_organizer
from judging.models import Review, ReviewExclusion, ReviewStatus
from judging.policy import engine_criteria, engine_reviews, included_reviews, review_values, scored_reviews
from projects.models import Project, ProjectStatus
from results import engine
from results.models import ResultPublication


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _lam_for_event(event: Event) -> float | str:
    """Return a numeric lambda or 'auto' when the event uses adaptive selection."""
    if event.shrinkage_lambda is None:
        return "auto"
    return float(event.shrinkage_lambda)


def _canonical_inputs(
    event: Event,
    included: list[dict],
    excluded: list[dict],
    params: dict,
) -> dict:
    """The canonical input object stored and hashed on every publication."""
    return {
        "event": event.slug,
        "method": params["method"],
        "lam": params["lam"],
        "lambda_source": params.get("lambda_source", "fixed"),
        "rubric_version": params["rubric_version"],
        "reviews_per_project": event.reviews_per_project,
        "included": sorted(included, key=lambda r: r["review_id"]),
        "excluded": sorted(excluded, key=lambda r: r["review_id"]),
        "params": params,
    }


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
    criteria = engine_criteria(event)
    if not criteria:
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
    all_submitted = list(
        Project.objects.filter(event=event, status=ProjectStatus.SUBMITTED)
        .values_list("public_id", flat=True)
    )
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
    )
    params = {
        "method": event.ranking_method,
        "lam": result.lam,
        "lambda_source": "auto" if lam_val == "auto" else "fixed",
        "rubric_version": rubric_version,
    }
    if result.lambda_choice is not None:
        params["lambda_cv"] = {
            str(k): v for k, v in result.lambda_choice.cv_rmse.items()
        }
        params["lambda_cv_baseline_rmse"] = result.lambda_choice.baseline_rmse
        params["lambda_cv_n"] = result.lambda_choice.n
        params["lambda_cv_folds"] = result.lambda_choice.folds
    input_digest = _digest(_canonical_inputs(event, inc, exc, params))
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
        "rows": _build_rows(result, event),
        "judge_rows": _build_judge_rows(result),
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


def _build_rows(result: engine.Result, event: Event) -> list[dict]:
    """Build public-facing rows with status classification."""
    projects = list(
        Project.objects.filter(event=event)
        .select_related("team", "track")
        .order_by("public_id")
    )
    rows = []
    for project in projects:
        pid = project.public_id
        rank_str = result.rank.get(pid)
        rank_raw = result.rank_raw.get(pid)
        rank_norm = result.rank_norm.get(pid)
        rank_bt = result.rank_bt.get(pid)
        raw_tuple = result.raw.get(pid)
        raw_mean = raw_tuple[0] if raw_tuple else None
        n_reviews = raw_tuple[1] if raw_tuple else 0
        normalized = result.normalized.get(pid)
        if project.status == ProjectStatus.WITHDRAWN:
            status = "withdrawn"
        elif project.status == ProjectStatus.DISQUALIFIED:
            status = "disqualified"
        elif project.status == ProjectStatus.SUPERSEDED:
            status = "superseded"
        elif n_reviews == 0:
            status = "unranked_no_reviews"
        else:
            status = "ranked"
        rows.append({
            "project_id": pid,
            "title": project.title,
            "team": project.team.name,
            "track": project.track.name if project.track_id else None,
            "n_reviews": n_reviews,
            "raw_mean": raw_mean,
            "normalized": normalized,
            "rank": rank_str,
            "rank_raw": rank_raw,
            "rank_norm": rank_norm,
            "rank_bt": rank_bt,
            "status": status,
            "status_reason": project.status_reason or None,
        })
    # Sort by primary rank, then title
    def _rank_key(row):
        r = row.get("rank")
        if r is None or r == "unranked":
            return (2, 0, row["title"].casefold())
        return (1 if r.startswith("=") else 0, int(r.lstrip("=")), row["title"].casefold())
    rows.sort(key=_rank_key)
    return rows


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
def publish(actor, event: Event, note: str = "", acknowledge_unranked: bool = False) -> ResultPublication:
    """Snapshot the current results and create a new publication.

    Requires judging closed; voting closed if configured. Raises 409 for:
    - judging_open (judging not yet closed)
    - unranked_projects (eligible projects with no reviews, unless acknowledged)
    - no_rubric (no criteria defined)
    """
    # Re-check state under lock so two concurrent publishes cannot race.
    event_locked = Event.objects.select_for_update().get(pk=event.pk)

    if event_locked.judging_close_at is None:
        raise ApiError(
            "judging_open",
            "Judging must be closed before results can be published. "
            "Use 'Close judging' first.",
            status_code=409,
        )
    if (
        event_locked.voting_close_at is not None
        and event_locked.voting_close_at > now()
    ):
        raise ApiError(
            "voting_open",
            "Voting must close before results can be published.",
            status_code=409,
        )

    # Build inputs and engine result.
    inc, exc = _build_input_lists(event_locked)
    criteria = engine_criteria(event_locked)
    if not criteria:
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
    )

    # Check for unranked eligible projects.
    unranked = [
        pid for pid in all_submitted
        if result.raw.get(pid) is None
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
    }
    if result.lambda_choice is not None:
        params["lambda_cv"] = {
            str(k): v for k, v in result.lambda_choice.cv_rmse.items()
        }
        params["lambda_cv_baseline_rmse"] = result.lambda_choice.baseline_rmse
        params["lambda_cv_n"] = result.lambda_choice.n
        params["lambda_cv_folds"] = result.lambda_choice.folds

    inputs = _canonical_inputs(event_locked, inc, exc, params)
    digest = _digest(inputs)
    rows = _build_rows(result, event_locked)
    judge_rows = _build_judge_rows(result)

    previous = (
        ResultPublication.objects.filter(event=event_locked)
        .order_by("-published_at")
        .first()
    )
    if previous and not note:
        raise ApiError(
            "note_required",
            "A note is required when superseding an existing publication.",
            status_code=400,
        )

    pub = ResultPublication.objects.create(
        event=event_locked,
        method=event_locked.ranking_method,
        params=params,
        inputs=inputs,
        input_digest=digest,
        rows=rows,
        judge_rows=judge_rows,
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
        .order_by("-published_at")
        .first()
    )
    if pub is None:
        raise ApiError(
            "not_published",
            "Results have not been published yet.",
            status_code=404,
        )
    # Strip judge data from rows – public rows only carry ranked fields.
    public_rows = []
    for row in pub.rows:
        public_rows.append({
            "rank": row.get("rank"),
            "project_id": row.get("project_id"),
            "title": row.get("title"),
            "team": row.get("team"),
            "track": row.get("track"),
            "n_reviews": row.get("n_reviews"),
            "raw_mean": row.get("raw_mean"),
            "normalized": row.get("normalized"),
            "status": row.get("status"),
        })
    return {
        "pub_id": pub.public_id,
        "published_at": pub.published_at.isoformat(),
        "method": pub.method,
        "lam": pub.params.get("lam"),
        "lambda_source": pub.params.get("lambda_source"),
        "rows": public_rows,
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
        "Only submitted reviews of submitted projects are included."
    )
    return {
        "pub_id": pub.public_id,
        "published_at": pub.published_at.isoformat(),
        "published_by": pub.published_by.display_name if pub.published_by else None,
        "method": pub.method,
        "params": pub.params,
        "input_digest": pub.input_digest,
        "rule_plain": rule_plain,
        "included_reviews": pub.inputs.get("included", []),
        "excluded_reviews": pub.inputs.get("excluded", []),
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

def verify_publication(pub: ResultPublication) -> dict:
    """Recompute results from stored inputs and compare.

    Two checks (BUILD-SPEC 16):
    1. Rerun the engine on the stored inputs → rows must be identical.
    2. Hash the live database → digest must match the stored digest.
    """
    stored_inputs = pub.inputs
    inc = stored_inputs.get("included", [])
    exc = stored_inputs.get("excluded", [])
    params = stored_inputs.get("params", {})
    criteria_dicts = params.get("criteria", [])

    # Rebuild engine criteria from the rubric at publication time.
    # Fall back to current rubric if not stored (older publications).
    if criteria_dicts:
        criteria = [
            engine.Criterion(
                key=c["key"],
                weight=float(c["weight"]),
                min_score=int(c["min_score"]),
                max_score=int(c["max_score"]),
            )
            for c in criteria_dicts
        ]
    else:
        criteria = engine_criteria(pub.event)

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
    all_project_ids = list({r["project_id"] for r in inc})
    result = engine.evaluate(
        review_inputs,
        criteria,
        lam=lam_val,
        target=pub.event.reviews_per_project,
        method=params.get("method", pub.method),
        projects=all_project_ids,
    )

    recomputed_rows = []
    for row in pub.rows:
        pid = row.get("project_id")
        recomputed_rows.append({
            "project_id": pid,
            "rank": result.rank.get(pid),
            "normalized": result.normalized.get(pid),
        })

    stored_rows = [
        {"project_id": r.get("project_id"), "rank": r.get("rank"), "normalized": r.get("normalized")}
        for r in pub.rows
    ]

    rows_match = recomputed_rows == stored_rows

    # Live digest check: same params, but freshly built included/excluded lists.
    # This checks whether the live data (reviews/exclusions) has changed since
    # publication. The params are fixed at publication time and don't vary.
    live_inc, live_exc = _build_input_lists(pub.event)
    # Use the stored params to reconstruct the live digest – only the review
    # data changes if there is tampering.
    live_digest = _digest(_canonical_inputs(pub.event, live_inc, live_exc, params))
    digest_match = live_digest == pub.input_digest

    if rows_match and digest_match:
        verdict = "identical"
        detail = "Recomputed rows match and live data digest matches the stored digest."
    else:
        parts = []
        if not rows_match:
            parts.append("recomputed rows differ from stored rows")
        if not digest_match:
            parts.append(
                f"live data digest {live_digest[:12]}… differs from stored {pub.input_digest[:12]}…"
            )
        verdict = "differs"
        detail = "; ".join(parts).capitalize() + "."

    return {
        "pub_id": pub.public_id,
        "verdict": verdict,
        "detail": detail,
        "rows_match": rows_match,
        "digest_match": digest_match,
        "stored_digest": pub.input_digest,
        "live_digest": live_digest,
    }


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
    """De-attributed feedback for a project: score, rank, per-criterion averages, comments.

    Judge names and IDs are stripped; comment order is shuffled with a
    seeded RNG so it is deterministic but unlinked from any judge identifier.
    """
    import random
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
    criteria_keys = []
    per_criterion: dict[str, list[int]] = {}
    from judging.models import CriterionScore, Review as JudgingReview
    reviews = list(
        JudgingReview.objects.filter(
            event=event, public_id__in=review_ids
        ).prefetch_related("scores__criterion")
    )
    comments = []
    for review in reviews:
        if review.comment:
            comments.append(review.comment)
        for score in review.scores.all():
            per_criterion.setdefault(score.criterion.key, []).append(score.value)

    # Shuffle comments deterministically by project id so they are not linkable.
    rng = random.Random(project.public_id)
    rng.shuffle(comments)
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
        "comments": comments,
    }
