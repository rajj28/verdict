"""VERDICT Closure: bounded review-completion scenario search.

Pure standard-library module (no Django), mirroring ``results.engine``'s
layering. Given a portable snapshot of an event's observed reviews plus
a declared roster of still-pending review slots, :func:`analyze`
searches a small, deterministic set of fully-completed hypothetical
scenarios for one that changes the first-place set, using the real
scoring engine (``engine.evaluate``) as the sole oracle -- including a
full adaptive cross-validation rerun for every candidate when
``lam == "auto"``. It reports only ``counterexample_found``, ``unknown``
or ``unsupported``: never ``stable``, a confidence probability, or a
minimum-flip claim. :func:`replay` independently re-verifies a claimed
witness against the exact declared pending-slot identities and the real
engine, without trusting the witness's own claimed output.

Shipping boundary: a bounded witness search, never a stability
certificate (JUDGING.md describes what it may and may not claim).
"""

from __future__ import annotations

import hashlib
import json
import math

from . import engine as E

#: Snapshot input caps (bound the work; reject oversized input instead).
MAX_PROJECTS = 100
MAX_OBSERVED_REVIEWS = 500
MAX_PENDING = 24
MAX_CRITERIA = 16
#: Hard ceiling on the search budget regardless of the caller's request.
HARD_MAX_SCENARIOS = 24
#: Magnitude/length caps (bound conversions before they can overflow or blow
#: up parsing, instead of after).
MAX_ID_LEN = 256
MAX_WEIGHT = 1e9
MAX_LAM = 1e12
MAX_CRITERION_BOUND = 1e9
MAX_TARGET = 10_000
MAX_CONTEXT_BYTES = 1024 * 1024

_METHODS = ("raw", "normalized", "pairwise")
_ROSTER_KINDS = ("hypothetical", "declared_assignments")

ASSUMPTIONS = (
    "Held fixed: current submitted review revisions, eligibility, duplicate "
    "policy, rubric/criteria domains, and the declared judge/project identities.",
    "Pending slot identities (review_id/judge_id/project_id) are analytical "
    "placeholders; an actual future review may get a different public id, "
    "which can change the seeded cross-validation fold assignment.",
    "This module does not independently verify upstream permission, track "
    "eligibility, conflict-of-interest or revision-lock guarantees; those "
    "are the calling adapter's responsibility.",
)


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_str(x) -> bool:
    return isinstance(x, str) and 0 < len(x) <= MAX_ID_LEN


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def _is_bounded_number(x, cap: float) -> bool:
    """True for a real, non-bool int/float within +/-cap.

    Ints are compared directly against ``cap`` rather than funnelled through
    ``math.isfinite``, which raises ``OverflowError`` on integers too large
    to convert to a float.
    """
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return False
    if isinstance(x, float) and not math.isfinite(x):
        return False
    return -cap <= x <= cap


def _validate_context(context) -> None:
    _check(isinstance(context, dict), "snapshot.context must be an object")
    try:
        text = json.dumps(context, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"snapshot.context is not canonical JSON-safe: {exc}") from None
    except RecursionError:
        raise ValueError("snapshot.context is too deeply nested") from None
    _check(len(text.encode("ascii")) <= MAX_CONTEXT_BYTES,
           f"snapshot.context exceeds the {MAX_CONTEXT_BYTES} byte cap")


def _digest(snapshot: dict) -> str:
    """Sha256 of sorted compact ASCII JSON over the whole snapshot object."""
    try:
        text = json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"snapshot is not canonical JSON-safe: {exc}") from None
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _validate_values(values, criteria_by_key: dict) -> None:
    _check(isinstance(values, dict), "review values must be an object")
    _check(set(values) == set(criteria_by_key), "values must have exactly the criterion keys")
    for key, crit in criteria_by_key.items():
        v = values[key]
        _check(
            _is_int(v) and crit.min_score <= v <= crit.max_score,
            f"value for {key!r} must be an integer in [{crit.min_score}, {crit.max_score}]",
        )


def _validate_max_scenarios(value) -> int:
    _check(_is_int(value) and value >= 1, "max_scenarios must be a positive integer")
    return min(value, HARD_MAX_SCENARIOS)


def _validate_snapshot(snapshot: dict) -> dict:
    """Validate the portable snapshot contract; return fresh parsed copies.

    Every collection is rebuilt into new dataclasses/dicts, so the
    caller's ``snapshot`` is never mutated or aliased by the result.
    """
    _check(isinstance(snapshot, dict), "snapshot must be an object")
    _check(_is_int(snapshot.get("version")) and snapshot["version"] == 1,
           "snapshot.version must be exactly 1")
    event = snapshot.get("event")
    _check(_is_str(event), "snapshot.event must be a non-empty string")
    method = snapshot.get("method")
    _check(method in _METHODS, "snapshot.method must be one of raw|normalized|pairwise")

    lam_raw = snapshot.get("lam")
    if lam_raw == "auto":
        lam: float | str = "auto"
    else:
        _check(_is_bounded_number(lam_raw, MAX_LAM) and lam_raw >= 0,
               f'snapshot.lam must be "auto" or a finite non-negative number <= {MAX_LAM:g}')
        lam = float(lam_raw)

    target = snapshot.get("target")
    _check(_is_int(target) and 0 < target <= MAX_TARGET,
           f"snapshot.target must be a positive integer <= {MAX_TARGET}")
    roster_declared = snapshot.get("roster_declared")
    _check(isinstance(roster_declared, bool), "snapshot.roster_declared must be a boolean")
    roster_kind = snapshot.get("roster_kind")
    _check(roster_kind in _ROSTER_KINDS,
           "snapshot.roster_kind must be hypothetical|declared_assignments")

    criteria_raw = snapshot.get("criteria")
    _check(isinstance(criteria_raw, list) and 1 <= len(criteria_raw) <= MAX_CRITERIA,
           f"snapshot.criteria must be a list of 1-{MAX_CRITERIA} entries")
    criteria: list[E.Criterion] = []
    seen_keys: set = set()
    for c in criteria_raw:
        _check(isinstance(c, dict), "each criterion must be an object")
        key = c.get("key")
        _check(_is_str(key) and key not in seen_keys,
               f"criterion key must be a distinct non-empty string, got {key!r}")
        seen_keys.add(key)
        weight = c.get("weight")
        _check(_is_bounded_number(weight, MAX_WEIGHT) and weight > 0,
               f"criterion {key!r} weight must be a positive finite number <= {MAX_WEIGHT:g}")
        lo, hi = c.get("min_score"), c.get("max_score")
        _check(
            _is_int(lo) and _is_int(hi) and abs(lo) <= MAX_CRITERION_BOUND
            and abs(hi) <= MAX_CRITERION_BOUND and hi > lo,
            f"criterion {key!r} must have integer max_score > min_score, "
            f"each within +/-{MAX_CRITERION_BOUND:g}",
        )
        criteria.append(E.Criterion(key, float(weight), lo, hi))
    criteria_by_key = {c.key: c for c in criteria}

    projects_raw = snapshot.get("projects")
    _check(isinstance(projects_raw, list) and len(projects_raw) <= MAX_PROJECTS,
           f"snapshot.projects must be a list of at most {MAX_PROJECTS} entries")
    projects: list[str] = []
    seen_p: set = set()
    for p in projects_raw:
        _check(_is_str(p) and p not in seen_p,
               f"project ids must be distinct non-empty strings, got {p!r}")
        seen_p.add(p)
        projects.append(p)
    project_set = set(projects)

    reviews_raw = snapshot.get("reviews")
    _check(isinstance(reviews_raw, list) and len(reviews_raw) <= MAX_OBSERVED_REVIEWS,
           f"snapshot.reviews must be a list of at most {MAX_OBSERVED_REVIEWS} entries")
    reviews: list[E.ReviewInput] = []
    seen_rid: set = set()
    seen_pair: set = set()
    for r in reviews_raw:
        _check(isinstance(r, dict), "each review must be an object")
        rid, jid, pid = r.get("review_id"), r.get("judge_id"), r.get("project_id")
        _check(_is_str(rid) and rid not in seen_rid,
               f"review_id must be a distinct non-empty string, got {rid!r}")
        _check(_is_str(jid), f"review {rid!r} judge_id must be a non-empty string")
        _check(_is_str(pid) and pid in project_set,
               f"review {rid!r} references unknown project {pid!r}")
        pair = (jid, pid)
        _check(pair not in seen_pair, f"duplicate judge/project pair {pair!r} in reviews")
        _validate_values(r.get("values"), criteria_by_key)
        seen_rid.add(rid)
        seen_pair.add(pair)
        reviews.append(E.ReviewInput(rid, jid, pid, dict(r["values"])))

    pending_raw = snapshot.get("pending")
    _check(isinstance(pending_raw, list) and len(pending_raw) <= MAX_PENDING,
           f"snapshot.pending must be a list of at most {MAX_PENDING} entries")
    pending: list[dict] = []
    seen_slot: set = set()
    seen_pending_rid: set = set()
    seen_pending_pair: set = set()
    for s in pending_raw:
        _check(isinstance(s, dict), "each pending slot must be an object")
        slot_id = s.get("slot_id")
        rid, jid, pid = s.get("review_id"), s.get("judge_id"), s.get("project_id")
        _check(_is_str(slot_id) and slot_id not in seen_slot,
               f"pending slot_id must be distinct, got {slot_id!r}")
        _check(_is_str(rid) and rid not in seen_rid and rid not in seen_pending_rid,
               f"pending review_id must be distinct and disjoint from observed, got {rid!r}")
        _check(_is_str(jid), f"pending slot {slot_id!r} judge_id must be a non-empty string")
        _check(_is_str(pid) and pid in project_set,
               f"pending slot {slot_id!r} references unknown project {pid!r}")
        pair = (jid, pid)
        _check(pair not in seen_pair and pair not in seen_pending_pair,
               f"pending judge/project pair {pair!r} duplicates an existing pair")
        seen_slot.add(slot_id)
        seen_pending_rid.add(rid)
        seen_pending_pair.add(pair)
        pending.append({"slot_id": slot_id, "review_id": rid, "judge_id": jid, "project_id": pid})
    pending.sort(key=lambda s: s["slot_id"])

    _check("context" in snapshot, "snapshot.context is required")
    _validate_context(snapshot.get("context"))
    notes = snapshot.get("notes")
    _check(notes is None or isinstance(notes, str), "snapshot.notes must be a string if present")

    return {
        "event": event, "method": method, "lam": lam, "target": target,
        "roster_declared": roster_declared, "roster_kind": roster_kind,
        "criteria": criteria, "projects": projects, "reviews": reviews, "pending": pending,
    }


def _winners(rank_map: dict) -> frozenset:
    return frozenset(p for p, r in rank_map.items() if r.lstrip("=") == "1")


def _payload(result: E.Result, method: str) -> dict:
    scores = (
        {p: mean for p, (mean, _n) in result.raw.items()}
        if method == "raw" else dict(result.normalized)
    )
    return {
        "winners": sorted(_winners(result.rank)),
        "scores": scores,
        "lam": result.lam,
        "rank": dict(result.rank),
    }


def _build_candidates(pending: list, criteria: list) -> list:
    """Deterministic bounded plan: all-min, all-max, then per-project focus."""
    lo = {c.key: c.min_score for c in criteria}
    hi = {c.key: c.max_score for c in criteria}

    def build(chooser):
        return [
            E.ReviewInput(s["review_id"], s["judge_id"], s["project_id"], dict(chooser(s)))
            for s in pending
        ]

    ordered = [build(lambda s: lo), build(lambda s: hi)]
    project_ids = sorted({s["project_id"] for s in pending})
    for focus in project_ids:
        ordered.append(build(lambda s, focus=focus: hi if s["project_id"] == focus else lo))
    for focus in project_ids:
        ordered.append(build(lambda s, focus=focus: lo if s["project_id"] == focus else hi))

    seen: set = set()
    unique = []
    for cand in ordered:
        key = tuple((r.review_id, tuple(sorted(r.values.items()))) for r in cand)
        if key in seen:
            continue
        seen.add(key)
        unique.append(cand)
    return unique


def analyze(snapshot: dict, *, max_scenarios: int = 12) -> dict:
    """Bounded counterexample search over admissible pending-slot completions.

    Returns ``status`` in {"counterexample_found", "unknown",
    "unsupported"} plus the canonical ``digest``, the baseline
    first-place set and, for a found counterexample, a complete
    replayable ``witness`` (feedable straight into :func:`replay`).
    """
    max_scenarios = _validate_max_scenarios(max_scenarios)
    parsed = _validate_snapshot(snapshot)
    digest = _digest(snapshot)
    assumptions = list(ASSUMPTIONS) + [
        "Roster is explicitly hypothetical, not a declared assignment roster."
        if parsed["roster_kind"] == "hypothetical" else
        "Roster reflects declared assignments as of this snapshot; still "
        "conditional on those exact identities."
    ]
    out = {
        "status": "unknown", "digest": digest, "event": parsed["event"],
        "method": parsed["method"], "roster_kind": parsed["roster_kind"],
        "roster_declared": parsed["roster_declared"],
        "pending_count": len(parsed["pending"]), "scenarios_limit": max_scenarios,
        "scenarios_considered": 0, "assumptions": assumptions,
        "reason": None, "baseline": None, "witness": None,
    }

    if parsed["method"] == "pairwise":
        out["status"] = "unsupported"
        out["reason"] = "pairwise_unsupported"
        return out
    if not parsed["reviews"]:
        out["reason"] = "no_observed_reviews"
        return out

    baseline_result = E.evaluate(
        parsed["reviews"], parsed["criteria"], lam=parsed["lam"],
        target=parsed["target"], method=parsed["method"], projects=parsed["projects"],
    )
    out["baseline"] = _payload(baseline_result, parsed["method"])

    if not parsed["roster_declared"]:
        out["reason"] = "roster_not_declared"
        return out
    if not parsed["pending"]:
        out["reason"] = "no_pending_slots"
        return out

    baseline_winners = _winners(baseline_result.rank)
    candidates = _build_candidates(parsed["pending"], parsed["criteria"])[:max_scenarios]
    considered = 0
    for pending_reviews in candidates:
        considered += 1
        full_reviews = parsed["reviews"] + pending_reviews
        cand_result = E.evaluate(
            full_reviews, parsed["criteria"], lam=parsed["lam"],
            target=parsed["target"], method=parsed["method"], projects=parsed["projects"],
        )
        if _winners(cand_result.rank) != baseline_winners:
            witness = _payload(cand_result, parsed["method"])
            witness["pending"] = [
                {
                    "slot_id": slot["slot_id"], "review_id": review.review_id,
                    "judge_id": review.judge_id, "project_id": review.project_id,
                    "values": dict(review.values),
                }
                for slot, review in zip(parsed["pending"], pending_reviews)
            ]
            witness["status"] = (
                "hypothetical" if parsed["roster_kind"] == "hypothetical" else "conditional"
            )
            out["status"] = "counterexample_found"
            out["scenarios_considered"] = considered
            out["witness"] = witness
            return out

    out["scenarios_considered"] = considered
    out["reason"] = "no_counterexample_in_budget"
    return out


def replay(snapshot: dict, witness: dict) -> dict:
    """Independently recompute a witness: never trust its claimed output.

    Validates exactly one entry per declared pending slot, matching the
    full slot/review/judge/project identity, then reruns the exact
    scoring engine over the observed reviews plus the witness's
    completion. Neither ``snapshot`` nor ``witness`` is mutated.
    """
    parsed = _validate_snapshot(snapshot)
    _check(parsed["method"] != "pairwise", "pairwise method is unsupported for replay")
    _check(parsed["roster_declared"],
           "replay requires a declared pending roster (analyze reported roster_not_declared)")
    _check(parsed["pending"], "replay requires at least one pending slot to complete")
    _check(isinstance(witness, dict), "witness must be an object")
    entries = witness.get("pending")
    _check(isinstance(entries, list), "witness.pending must be a list")

    declared = {slot["slot_id"]: slot for slot in parsed["pending"]}
    _check(len(entries) == len(declared), "witness must supply exactly one entry per pending slot")

    criteria_by_key = {c.key: c for c in parsed["criteria"]}
    filled: dict = {}
    for entry in entries:
        _check(isinstance(entry, dict), "witness pending entry must be an object")
        slot_id = entry.get("slot_id")
        _check(_is_str(slot_id),
               f"witness pending entry slot_id must be a non-empty string, got {slot_id!r}")
        slot = declared.get(slot_id)
        _check(slot is not None, f"witness references unknown pending slot {slot_id!r}")
        _check(slot_id not in filled, f"duplicate witness entry for slot {slot_id!r}")
        _check(
            entry.get("review_id") == slot["review_id"]
            and entry.get("judge_id") == slot["judge_id"]
            and entry.get("project_id") == slot["project_id"],
            f"witness identity mismatch for slot {slot_id!r}",
        )
        _validate_values(entry.get("values"), criteria_by_key)
        filled[slot_id] = E.ReviewInput(
            slot["review_id"], slot["judge_id"], slot["project_id"], dict(entry["values"]),
        )
    _check(set(filled) == set(declared), "witness is missing pending slot(s)")

    full_reviews = parsed["reviews"] + [filled[s["slot_id"]] for s in parsed["pending"]]
    result = E.evaluate(
        full_reviews, parsed["criteria"], lam=parsed["lam"],
        target=parsed["target"], method=parsed["method"], projects=parsed["projects"],
    )
    payload = _payload(result, parsed["method"])
    payload["digest"] = _digest(snapshot)
    return payload
