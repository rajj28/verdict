"""
Judging pace forecast and rebalance proposal (BUILD-SPEC sections 10 and 11).

Pure Python: no Django imports, no database, no clock. Every input is a plain
dataclass and every output is a plain dataclass, so the whole forecast can be
unit tested from synthetic timestamps. ``judging.services`` owns the ORM side:
loading rows, calling these functions, and persisting a rebalance.

Pace
----
A judge's pace is the **median gap between consecutive live submissions**,
where a gap of ``MAX_GAP_MINUTES`` minutes or more is not a gap at all but a
break (lunch, a session elsewhere), and is dropped before the median is taken.
A median is used rather than a mean because one very long or very short pause
must not move the forecast. Imported reviews carry no real timing - their
``submitted_at`` values are synthetic - so callers pass live submissions only,
and a judge with fewer than two of them simply has no pace yet.

Risk
----
A judge is at risk when
  * the projected finish (``now + remaining x pace``) is after the event's
    ``judging_close_at``,
  * nothing has been submitted ``STALLED_HOURS`` after their first assignment
    ("not started"), or
  * they still owe reviews, the window is already shut and they have no pace
    to project with (an overdue judge whose finish is unknown).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

MAX_GAP_MINUTES = 60.0
"""Gaps at or above this many minutes are breaks, not working pace."""

STALLED_HOURS = 12.0
"""A judge who has submitted nothing this long after their first assignment is not started."""


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgePaceInput:
    """What the forecast needs to know about one judge.

    ``live_submitted_at`` must contain only live (non-imported) submitted
    review timestamps; imported reviews have no real timing.
    """

    judge_id: str
    name: str = ""
    tracks: tuple[str, ...] = ()
    assigned: int = 0
    submitted: int = 0
    drafts: int = 0
    first_assigned_at: datetime | None = None
    live_submitted_at: tuple[datetime, ...] = ()


@dataclass(frozen=True)
class RebalanceJudge:
    """A judge who could receive work, and what they currently carry."""

    judge_id: str
    name: str = ""
    track_ids: frozenset[str] = frozenset()
    conflict_team_ids: frozenset[str] = frozenset()
    assigned: int = 0
    assigned_project_ids: frozenset[str] = frozenset()
    projected_finish: datetime | None = None
    at_risk: bool = False


@dataclass(frozen=True)
class MovableAssignment:
    """One assignment that has not been touched yet and could change hands.

    ``project_title`` and ``track_name`` are display only: the proposal is decided
    on ids, and the page prints the words a judge recognizes.
    """

    assignment_id: str
    judge_id: str
    project_id: str
    track_id: str
    team_id: str
    project_title: str = ""
    track_name: str = ""


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeForecast:
    judge_id: str
    name: str
    tracks: tuple[str, ...]
    assigned: int
    submitted: int
    drafts: int
    remaining: int
    status: str
    pace_minutes: float | None
    minutes_left: float | None
    projected_finish: datetime | None
    first_assigned_at: datetime | None
    first_submitted_at: datetime | None
    last_submitted_at: datetime | None
    at_risk: bool
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "judge": self.judge_id,
            "name": self.name,
            "tracks": list(self.tracks),
            "assigned": self.assigned,
            "submitted": self.submitted,
            "drafts": self.drafts,
            "remaining": self.remaining,
            "status": self.status,
            "pace_minutes": None if self.pace_minutes is None else round(self.pace_minutes, 1),
            "minutes_left": None if self.minutes_left is None else round(self.minutes_left, 1),
            "projected_finish": self.projected_finish,
            "first_assigned_at": self.first_assigned_at,
            "first_submitted_at": self.first_submitted_at,
            "last_submitted_at": self.last_submitted_at,
            "at_risk": self.at_risk,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class Forecast:
    """The whole event's forecast: one row per judge plus the event projection."""

    now: datetime
    judging_close_at: datetime | None
    stalled_hours: float
    judges: tuple[JudgeForecast, ...]
    projected_finish: datetime | None
    at_risk: bool

    def as_dict(self) -> dict:
        return {
            "now": self.now,
            "judging_close_at": self.judging_close_at,
            "stalled_hours": self.stalled_hours,
            "judges": [judge.as_dict() for judge in self.judges],
            "projected_finish": self.projected_finish,
            "at_risk": self.at_risk,
            "at_risk_count": sum(1 for judge in self.judges if judge.at_risk),
        }


@dataclass(frozen=True)
class RebalanceMove:
    assignment_id: str
    from_judge: str
    from_judge_name: str
    to_judge: str
    to_judge_name: str
    project_id: str
    track_id: str
    project_title: str = ""
    track_name: str = ""

    def as_dict(self) -> dict:
        return {
            "assignment": self.assignment_id,
            "from_judge": self.from_judge,
            "from_judge_name": self.from_judge_name,
            "to_judge": self.to_judge,
            "to_judge_name": self.to_judge_name,
            "project": self.project_id,
            "project_title": self.project_title,
            "track": self.track_id,
            "track_name": self.track_name,
        }


@dataclass(frozen=True)
class RebalanceProposal:
    """What a rebalance would do, and what it would leave alone."""

    max_load: int
    moves: tuple[RebalanceMove, ...] = ()
    skipped: tuple[dict, ...] = ()

    def as_dict(self) -> dict:
        return {
            "max_load": self.max_load,
            "moves": [move.as_dict() for move in self.moves],
            "skipped": [dict(item) for item in self.skipped],
        }


# ---------------------------------------------------------------------------
# Pace
# ---------------------------------------------------------------------------


def minutes_per_review(timestamps, max_gap_minutes: float = MAX_GAP_MINUTES) -> float | None:
    """Median minutes between consecutive submissions, or None without enough data.

    Gaps of ``max_gap_minutes`` or more are breaks and are dropped first, so a
    judge who reviewed all morning and nothing all afternoon is not projected at
    the pace of a fifteen-hour day. Fewer than two usable timestamps means no
    evidence, which is reported as None rather than as zero.
    """
    ordered = sorted(timestamps)
    gaps = [
        (later - earlier).total_seconds() / 60.0
        for earlier, later in zip(ordered, ordered[1:])
    ]
    usable = [gap for gap in gaps if 0 < gap < max_gap_minutes]
    if not usable:
        return None
    return float(statistics.median(usable))


def _status(assigned: int, submitted: int, drafts: int, remaining: int) -> str:
    if assigned == 0:
        return "no assignments"
    if remaining == 0:
        return "done"
    if submitted == 0 and drafts == 0:
        return "not started"
    return "in progress"


def judge_forecast(judge: JudgePaceInput, now: datetime,
                   judging_close_at: datetime | None = None,
                   stalled_hours: float = STALLED_HOURS) -> JudgeForecast:
    """Project one judge's finish from their own pace and their remaining load."""
    remaining = max(judge.assigned - judge.submitted, 0)
    stamps = tuple(sorted(judge.live_submitted_at))
    pace = minutes_per_review(stamps)
    minutes_left = None if pace is None else pace * remaining
    projected = None if minutes_left is None else now + timedelta(minutes=minutes_left)

    reasons: list[str] = []
    if remaining > 0:
        if projected is not None and judging_close_at is not None and projected > judging_close_at:
            reasons.append("projected_finish_after_close")
        if (
            pace is None and judging_close_at is not None and now >= judging_close_at
        ):
            reasons.append("overdue_without_pace")
        if (
            judge.submitted == 0 and judge.first_assigned_at is not None
            and now - judge.first_assigned_at >= timedelta(hours=stalled_hours)
        ):
            reasons.append("not_started")

    return JudgeForecast(
        judge_id=judge.judge_id,
        name=judge.name,
        tracks=tuple(judge.tracks),
        assigned=judge.assigned,
        submitted=judge.submitted,
        drafts=judge.drafts,
        remaining=remaining,
        status=_status(judge.assigned, judge.submitted, judge.drafts, remaining),
        pace_minutes=pace,
        minutes_left=minutes_left,
        projected_finish=projected,
        first_assigned_at=judge.first_assigned_at,
        first_submitted_at=stamps[0] if stamps else None,
        last_submitted_at=stamps[-1] if stamps else None,
        at_risk=bool(reasons),
        reasons=tuple(reasons),
    )


def build_forecast(judges, now: datetime, judging_close_at: datetime | None = None,
                   stalled_hours: float = STALLED_HOURS) -> Forecast:
    """Forecast every judge and roll the rows up to one event projection.

    The event is only as finished as its slowest judge, so the event projection
    is the latest projected finish; an unknown finish (a judge with no pace) is
    ignored here because the per-judge row already says the finish is unknown.
    Rows come back sorted worst-first: at risk, then the latest finish, then
    idle judges, then by name, so the page leads with the problem.
    """
    rows = [
        judge_forecast(judge, now, judging_close_at, stalled_hours)
        for judge in judges
    ]
    rows.sort(key=_risk_order)
    finishes = [row.projected_finish for row in rows if row.projected_finish is not None]
    projected_finish = max(finishes) if finishes else None
    return Forecast(
        now=now,
        judging_close_at=judging_close_at,
        stalled_hours=stalled_hours,
        judges=tuple(rows),
        projected_finish=projected_finish,
        at_risk=any(row.at_risk for row in rows),
    )


def _risk_order(row: JudgeForecast):
    finish = row.projected_finish
    return (
        not row.at_risk,
        finish is None,
        -finish.timestamp() if finish is not None else 0.0,
        row.name.casefold(),
        row.judge_id,
    )


# ---------------------------------------------------------------------------
# Rebalance
# ---------------------------------------------------------------------------


def default_max_load(judges) -> int:
    """The cap a rebalance may not push past: the busiest judge's current load.

    Rebalancing spreads work, it never overloads; without an explicit cap the
    safest reading of "keep max_load" is the load the busiest judge already
    carries, so a move can only land on a judge with room to spare.
    """
    return max([judge.assigned for judge in judges], default=0) or 1


def _receiver_order(judge: RebalanceJudge, load: int):
    """Earliest projected finish first; unknown finishes last, then load, then id."""
    finish = judge.projected_finish
    return (finish is None, finish.timestamp() if finish is not None else 0.0, load, judge.judge_id)


def _skip_reason(judges, movable: MovableAssignment, loads: dict[str, int],
                 project_ids: dict[str, set[str]], max_load: int) -> str:
    """Why this assignment could not move, in the organizer's words."""
    in_track = [j for j in judges if movable.track_id in j.track_ids]
    if not in_track:
        return (f"track {movable.track_id} has no other judge assigned to it; "
                "the work has to stay where it is")
    others = [
        j for j in in_track
        if j.judge_id != movable.judge_id
        and movable.team_id not in j.conflict_team_ids
    ]
    if not others:
        return (f"every other judge of track {movable.track_id} has a conflict with "
                f"team {movable.team_id}")
    already = [j for j in others if movable.project_id in project_ids[j.judge_id]]
    full = [j for j in others if loads[j.judge_id] >= max_load]
    parts = []
    if already:
        parts.append(f"{len(already)} already review this project")
    if full:
        parts.append(f"{len(full)} at max load {max_load}")
    if not parts:
        return "no judge of this track has room to take the work"
    return (f"no judge of track {movable.track_id} can take it: " + "; ".join(parts))


def propose_rebalance(judges, movables, max_load: int | None = None) -> RebalanceProposal:
    """Move untouched work off the at-risk judges onto the freest ones.

    Only assignments the judge has not started are eligible (``movables`` holds
    exactly those, decided by the caller), never a draft and never a submitted
    review. Work leaves at-risk judges and lands on an in-track, non-conflicted
    judge who does not already hold the project and is under ``max_load``,
    preferring the earliest projected finish. The result is deterministic:
    sources, assignments and receivers are all ordered by public id, and ties
    break on load then id.
    """
    judge_list = list(judges)
    judge_map = {judge.judge_id: judge for judge in judge_list}
    loads = {judge.judge_id: judge.assigned for judge in judge_list}
    project_ids = {judge.judge_id: set(judge.assigned_project_ids) for judge in judge_list}
    cap = max_load if max_load is not None else default_max_load(judge_list)

    moves: list[RebalanceMove] = []
    skipped: list[dict] = []
    for movable in sorted(movables, key=lambda row: row.assignment_id):
        source = judge_map.get(movable.judge_id)
        if source is None or not source.at_risk:
            continue
        candidates = [
            judge for judge in judge_list
            if judge.judge_id != movable.judge_id
            and movable.track_id in judge.track_ids
            and movable.team_id not in judge.conflict_team_ids
            and movable.project_id not in project_ids[judge.judge_id]
            and loads[judge.judge_id] < cap
        ]
        if not candidates:
            skipped.append({
                "assignment": movable.assignment_id,
                "from_judge": movable.judge_id,
                "project": movable.project_id,
                "project_title": movable.project_title,
                "reason": _skip_reason(judge_list, movable, loads, project_ids, cap),
            })
            continue
        chosen = min(candidates, key=lambda judge: _receiver_order(judge, loads[judge.judge_id]))
        loads[chosen.judge_id] += 1
        project_ids[chosen.judge_id].add(movable.project_id)
        moves.append(RebalanceMove(
            assignment_id=movable.assignment_id,
            from_judge=source.judge_id,
            from_judge_name=source.name,
            to_judge=chosen.judge_id,
            to_judge_name=chosen.name,
            project_id=movable.project_id,
            track_id=movable.track_id,
            project_title=movable.project_title,
            track_name=movable.track_name,
        ))
    return RebalanceProposal(max_load=cap, moves=tuple(moves), skipped=tuple(skipped))
