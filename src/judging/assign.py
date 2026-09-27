"""
Assignment algorithm for VERDICT (BUILD-SPEC §10).

Pure Python, no Django imports.  All inputs are plain dataclasses; the
caller (judging.services) is responsible for loading ORM objects and
mapping them to these types, and for persisting the returned proposals.

Algorithm summary
-----------------
1. need_p = target − count(active assignments of p)
2. Order projects by (fewest eligible judges asc, need desc, public_id asc)
   — most-constrained first.
3. For each unit of need, pick the eligible judge with:
   a. lowest current load (number of assignments they would carry after this run)
   b. fewest projects shared with the project's already-assigned reviewers
      (spreads overlap, improves graph connectivity)
   c. seeded deterministic RNG as final tie-break
4. Collect unfillable needs with human-readable reasons.
5. "fill-gaps" = run this algorithm on top of existing assignment data.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeInput:
    """A judge eligible to be assigned reviews.

    judge_id    : stable opaque id (e.g. EventRole.public_id)
    track_ids   : set of track ids this judge covers
    conflict_team_ids : set of team ids the judge has declared/recorded
                        conflicts with
    existing_assignment_project_ids : project ids already assigned to this
                        judge (from previous batches / manual assignments)
    """

    judge_id: str
    track_ids: frozenset[str]
    conflict_team_ids: frozenset[str] = field(default_factory=frozenset)
    existing_assignment_project_ids: frozenset[str] = field(
        default_factory=frozenset
    )


@dataclass(frozen=True)
class ProjectInput:
    """A submitted project eligible to receive reviews.

    project_id  : stable opaque id (e.g. Project.public_id)
    track_id    : the single track this project belongs to
    team_id     : used to check judge conflicts
    existing_reviewer_judge_ids : judge ids that already hold an assignment
                        for this project (from previous batches / manual)
    """

    project_id: str
    track_id: str
    team_id: str
    existing_reviewer_judge_ids: frozenset[str] = field(
        default_factory=frozenset
    )


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass
class ProposedAssignment:
    """A single new judge → project assignment proposed by the algorithm."""

    judge_id: str
    project_id: str


@dataclass
class UnfilledNeed:
    """A project that could not reach the target number of reviews."""

    project_id: str
    target: int
    assigned: int  # includes both existing and newly proposed
    reason: str


@dataclass
class AssignmentProposal:
    """The full output of one algorithm run.

    new_assignments : proposed (judge_id, project_id) pairs that do not
                      yet exist; the caller validates and persists these.
    unfilled        : projects whose review count will still be below target
                      after this proposal is applied.
    """

    new_assignments: list[ProposedAssignment]
    unfilled: list[UnfilledNeed]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def propose_assignments(
    judges: list[JudgeInput],
    projects: list[ProjectInput],
    target: int,
    max_load: int | None = None,
    seed: str = "verdict",
) -> AssignmentProposal:
    """Return a proposal of new assignments per BUILD-SPEC §10.

    Parameters
    ----------
    judges      : all judges for this event (eligible or not)
    projects    : all *submitted* projects for this event
    target      : desired number of reviewers per project (k)
    max_load    : hard cap on assignments per judge across the entire run;
                  defaults to ceil(total_needed / eligible_judges) + 1
    seed        : string seed for the deterministic RNG tie-break

    The algorithm only proposes *new* pairs – it never touches existing
    assignments.  Callers should pass in ``existing_assignment_project_ids``
    on each JudgeInput and ``existing_reviewer_judge_ids`` on each
    ProjectInput so that the counts stay accurate.
    """
    if target < 1:
        raise ValueError(f"target must be ≥ 1, got {target}")

    rng = random.Random(seed)

    # ------------------------------------------------------------------
    # 1. Compute per-project need (how many more reviewers are required)
    # ------------------------------------------------------------------
    project_map: dict[str, ProjectInput] = {p.project_id: p for p in projects}
    judge_map: dict[str, JudgeInput] = {j.judge_id: j for j in judges}

    # Mutable working state: current load and project-set per judge
    # (includes existing assignments from input data)
    judge_load: dict[str, int] = {
        j.judge_id: len(j.existing_assignment_project_ids) for j in judges
    }
    # projects currently held by each judge (existing + proposed this run)
    judge_projects: dict[str, set[str]] = {
        j.judge_id: set(j.existing_assignment_project_ids) for j in judges
    }
    # reviewers currently assigned to each project (existing + proposed)
    project_reviewers: dict[str, set[str]] = {
        p.project_id: set(p.existing_reviewer_judge_ids) for p in projects
    }

    # ------------------------------------------------------------------
    # 2. Compute eligibility: which judges can review which projects?
    # ------------------------------------------------------------------
    def eligible(judge_id: str, project_id: str) -> bool:
        """True iff judge can take on a new review of this project."""
        j = judge_map[judge_id]
        p = project_map[project_id]
        if p.track_id not in j.track_ids:
            return False
        if p.team_id in j.conflict_team_ids:
            return False
        if project_id in judge_projects[judge_id]:
            return False  # already assigned (existing or proposed this run)
        return True

    # ------------------------------------------------------------------
    # 3. Determine default max_load if not provided
    # ------------------------------------------------------------------
    total_need = sum(
        max(0, target - len(project_reviewers[p.project_id]))
        for p in projects
    )
    n_eligible_judges = sum(
        1
        for j in judges
        if any(eligible(j.judge_id, p.project_id) for p in projects)
    )
    if max_load is None:
        if n_eligible_judges > 0:
            max_load = math.ceil(total_need / n_eligible_judges) + 1
        else:
            max_load = target  # fallback; will surface as unfilled

    # ------------------------------------------------------------------
    # 4. Order projects most-constrained first
    #    key = (eligible_judge_count asc, need desc, project_id asc)
    # ------------------------------------------------------------------
    def project_sort_key(p: ProjectInput):
        need = max(0, target - len(project_reviewers[p.project_id]))
        elig_count = sum(
            1 for j in judges if eligible(j.judge_id, p.project_id)
        )
        return (elig_count, -need, p.project_id)

    new_assignments: list[ProposedAssignment] = []

    # We iterate until no more need exists or no progress was made.
    # A single pass can leave a project under-covered if a judge that
    # would have helped gets fully loaded by an earlier project; a
    # second pass fixes those cases without infinite loops.
    made_progress = True
    while made_progress:
        made_progress = False
        ordered = sorted(
            (p for p in projects if len(project_reviewers[p.project_id]) < target),
            key=project_sort_key,
        )
        for proj in ordered:
            while len(project_reviewers[proj.project_id]) < target:
                # Collect eligible judges respecting max_load
                candidates = [
                    j
                    for j in judges
                    if eligible(j.judge_id, proj.project_id)
                    and judge_load[j.judge_id] < max_load
                ]
                if not candidates:
                    break  # no eligible judge available right now

                # Tie-break 1: lowest load
                min_load = min(judge_load[j.judge_id] for j in candidates)
                candidates = [
                    j for j in candidates if judge_load[j.judge_id] == min_load
                ]

                # Tie-break 2: fewest projects shared with current reviewers
                # of this project (minimise overlap in the judge–project graph).
                # "Shared projects" = projects the candidate also holds that
                # are already held by at least one existing reviewer of this
                # project.  A lower count means less overlap → better
                # connectivity.
                current_reviewer_ids = project_reviewers[proj.project_id]
                if current_reviewer_ids:
                    # Union of all projects held by current reviewers of proj
                    reviewer_project_union: set[str] = set()
                    for rev_jid in current_reviewer_ids:
                        reviewer_project_union |= judge_projects[rev_jid]

                    def shared_count(j: JudgeInput) -> int:  # noqa: E306
                        return len(judge_projects[j.judge_id] & reviewer_project_union)

                    min_shared = min(shared_count(j) for j in candidates)
                    candidates = [
                        j for j in candidates if shared_count(j) == min_shared
                    ]

                # Tie-break 3: deterministic seeded RNG
                candidates_sorted = sorted(candidates, key=lambda j: j.judge_id)
                chosen = rng.choice(candidates_sorted)

                # Record the new assignment
                new_assignments.append(
                    ProposedAssignment(chosen.judge_id, proj.project_id)
                )
                judge_load[chosen.judge_id] += 1
                judge_projects[chosen.judge_id].add(proj.project_id)
                project_reviewers[proj.project_id].add(chosen.judge_id)
                made_progress = True

    # ------------------------------------------------------------------
    # 5. Collect unfilled needs with human-readable reasons
    # ------------------------------------------------------------------
    unfilled: list[UnfilledNeed] = []
    for proj in projects:
        assigned_count = len(project_reviewers[proj.project_id])
        if assigned_count < target:
            reason = _unfilled_reason(
                proj, judges, judge_map, judge_load, max_load, target,
                judge_projects, project_reviewers,
            )
            unfilled.append(
                UnfilledNeed(
                    project_id=proj.project_id,
                    target=target,
                    assigned=assigned_count,
                    reason=reason,
                )
            )

    # Sort for determinism
    unfilled.sort(key=lambda u: u.project_id)

    return AssignmentProposal(
        new_assignments=new_assignments,
        unfilled=unfilled,
    )


def _unfilled_reason(
    proj: ProjectInput,
    judges: list[JudgeInput],
    judge_map: dict[str, JudgeInput],
    judge_load: dict[str, int],
    max_load: int,
    target: int,
    judge_projects: dict[str, set[str]],
    project_reviewers: dict[str, set[str]],
) -> str:
    """Produce a human-readable explanation of why a project is under target."""
    total_in_track = sum(
        1 for j in judges if proj.track_id in j.track_ids
    )
    conflict_blocked = sum(
        1
        for j in judges
        if proj.track_id in j.track_ids and proj.team_id in j.conflict_team_ids
    )
    already_assigned = sum(
        1
        for j in judges
        if proj.track_id in j.track_ids
        and proj.project_id in judge_projects[j.judge_id]
    )
    load_blocked = sum(
        1
        for j in judges
        if proj.track_id in j.track_ids
        and proj.team_id not in j.conflict_team_ids
        and proj.project_id not in judge_projects[j.judge_id]
        and judge_load[j.judge_id] >= max_load
    )
    effective_eligible = sum(
        1
        for j in judges
        if proj.track_id in j.track_ids
        and proj.team_id not in j.conflict_team_ids
        and proj.project_id not in judge_projects[j.judge_id]
        and judge_load[j.judge_id] < max_load
    )

    assigned_now = len(project_reviewers[proj.project_id])
    still_needed = target - assigned_now

    parts: list[str] = []
    if total_in_track == 0:
        return (
            f"track {proj.track_id!r} has no judges assigned to it; "
            f"target {target} unreachable"
        )
    if effective_eligible == 0:
        detail_parts: list[str] = []
        if conflict_blocked:
            detail_parts.append(f"{conflict_blocked} blocked by conflict")
        if load_blocked:
            detail_parts.append(f"{load_blocked} at max_load ({max_load})")
        if already_assigned:
            detail_parts.append(f"{already_assigned} already assigned")
        detail = "; ".join(detail_parts) if detail_parts else "unknown reason"
        parts.append(
            f"track {proj.track_id!r} has {total_in_track} judge(s) "
            f"but none available ({detail}); "
            f"target {target} unreachable"
        )
    else:
        parts.append(
            f"track {proj.track_id!r} has only {effective_eligible} available "
            f"judge(s) after conflicts/load; "
            f"need {still_needed} more reviewer(s) to reach target {target}"
        )
    return " ".join(parts)
