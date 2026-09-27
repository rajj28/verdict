"""Prize allocation: a pure function from official ranked rows to awards.

No Django imports, so the portal, the offline proof script and the unit
tests all run this exact code (same rule as ``results.engine``).

Rules (BUILD-SPEC section 9 ranking, packet P8):

* only rows whose status is ``ranked`` can win. Unranked (no reviews),
  withdrawn, disqualified and superseded projects never win, whatever
  their score says.
* an overall prize draws from every ranked project; a track prize only
  from the ranked projects of its track.
* prizes are considered in ``position`` order and the places inside a
  prize ascend, so a one-prize-per-team skip always prefers the higher
  prize. Skipped winners are never silent: the award carries the reason.
* an exact tie at a cut (same official score rounded to 2 dp) is never
  broken by this function. The prize is left unawarded with both projects
  named, because only an organizer may break a tie.

Everything is deterministic: input order decides, no randomness, no
clock, no database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

#: The only row status that may win a prize.
RANKED = "ranked"

SCOPE_OVERALL = "overall"
SCOPE_TRACK = "track"

#: Official scores are compared at 2 dp, the same rounding the ranks use.
SCORE_DP = 2

#: Reason templates. Kept as constants so tests and the UI can match them.
TIE_REASON = "tie requires organizer decision: {projects} both score {score}"
SKIP_REASON = "{project} already won {prize}; next eligible is {winner}"
EXHAUSTED_REASON = "{project} already won {prize}; no eligible project remains{pool}"
EMPTY_OVERALL = "no ranked project remains"
EMPTY_TRACK = "no ranked project remains in {track}"


def official_score_key(score: float | None) -> float | None:
    """Round to the precision the engine ranks at, so ties compare equal."""
    return None if score is None else round(float(score), SCORE_DP)


@dataclass(frozen=True)
class RankedProject:
    """One project as the allocator sees it: public ids only, never PKs."""

    project_id: str
    title: str
    team_id: str
    team_name: str = ""
    track_id: str | None = None
    track_name: str | None = None
    score: float | None = None
    rank: str | None = None
    status: str = RANKED


@dataclass(frozen=True)
class PrizeSpec:
    """One prize: what it awards, how many places, and from which pool."""

    prize_id: str
    name: str
    scope: str = SCOPE_OVERALL
    track_id: str | None = None
    places: int = 1
    value: str = ""
    track_name: str = ""
    eligibility_note: str = ""
    position: int = 0


@dataclass(frozen=True)
class Award:
    """A prize handed to a project, with the reason the winner was chosen."""

    prize_id: str
    prize: str
    place: int
    project_id: str
    project: str
    team_id: str
    team: str
    track: str | None
    score: float | None
    rank: str | None
    reason: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "prize_id": self.prize_id,
            "prize": self.prize,
            "place": self.place,
            "project_id": self.project_id,
            "project": self.project,
            "team_id": self.team_id,
            "team": self.team,
            "track": self.track,
            "score": self.score,
            "rank": self.rank,
            "reason": self.reason,
            "note": self.note,
        }


@dataclass(frozen=True)
class Unawarded:
    """A prize that could not be awarded, and why."""

    prize_id: str
    prize: str
    reason: str
    place: int | None = None
    projects: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "prize_id": self.prize_id,
            "prize": self.prize,
            "reason": self.reason,
            "place": self.place,
            "projects": list(self.projects),
        }


@dataclass(frozen=True)
class Allocation:
    """Everything :func:`allocate` decided: awards plus unawarded prizes."""

    awards: tuple[Award, ...] = ()
    unawarded: tuple[Unawarded, ...] = ()

    def as_dict(self) -> dict:
        return {
            "awards": [a.as_dict() for a in self.awards],
            "unawarded": [u.as_dict() for u in self.unawarded],
        }


def _pool(prize: PrizeSpec, ranked: Sequence[RankedProject]) -> list[RankedProject]:
    """Candidates for one prize: everything ranked, or one track only."""
    if prize.scope == SCOPE_TRACK:
        return [p for p in ranked if p.track_id is not None and p.track_id == prize.track_id]
    return list(ranked)


def _tie_group(head: RankedProject, candidates: Sequence[RankedProject]) -> list[RankedProject]:
    """Every candidate sharing the head's rounded score, in ranking order."""
    key = official_score_key(head.score)
    return [p for p in candidates if official_score_key(p.score) == key]


def allocate(
    rows: Sequence[RankedProject],
    prizes: Sequence[PrizeSpec],
    one_per_team: bool = True,
) -> Allocation:
    """Award ``prizes`` (already in position order) from ranked ``rows``.

    ``rows`` must be in official ranking order: the allocator never
    re-sorts, it only reads. Returns the awards and the prizes left
    unawarded.
    """
    ranked = [r for r in rows if r.status == RANKED and r.score is not None]
    team_won: dict[str, str] = {}  # team_id -> prize name already held
    taken: set[str] = set()  # project ids that already hold a prize
    awards: list[Award] = []
    unawarded: list[Unawarded] = []

    for prize in prizes:
        candidates = _pool(prize, ranked)
        places = max(int(prize.places), 0)
        for place in range(1, places + 1):
            left = [p for p in candidates if p.project_id not in taken]
            if not left:
                unawarded.append(
                    Unawarded(prize.prize_id, prize.name, _empty_reason(prize), place)
                )
                break
            eligible = [p for p in left if not _blocked(p, team_won, one_per_team)]
            if not eligible:
                blocked = left[0]
                unawarded.append(
                    Unawarded(
                        prize.prize_id,
                        prize.name,
                        EXHAUSTED_REASON.format(
                            project=blocked.title,
                            prize=team_won[blocked.team_id],
                            pool=_pool_suffix(prize),
                        ),
                        place,
                    )
                )
                break
            head = eligible[0]
            group = _tie_group(head, eligible)
            if len({p.team_id for p in group}) > 1:
                # An exact tie across teams: only an organizer may break it.
                unawarded.append(
                    Unawarded(
                        prize.prize_id,
                        prize.name,
                        TIE_REASON.format(
                            projects=" and ".join(p.title for p in group),
                            score=f"{official_score_key(head.score):.2f}",
                        ),
                        place,
                        tuple(p.project_id for p in group),
                    )
                )
                break
            winner = head
            reason = ""
            if _blocked(left[0], team_won, one_per_team):
                reason = SKIP_REASON.format(
                    project=left[0].title,
                    prize=team_won[left[0].team_id],
                    winner=winner.title,
                )
            awards.append(
                Award(
                    prize_id=prize.prize_id,
                    prize=prize.name,
                    place=place,
                    project_id=winner.project_id,
                    project=winner.title,
                    team_id=winner.team_id,
                    team=winner.team_name,
                    track=winner.track_name,
                    score=winner.score,
                    rank=winner.rank,
                    reason=reason,
                    note=prize.eligibility_note,
                )
            )
            taken.add(winner.project_id)
            team_won[winner.team_id] = prize.name
    return Allocation(tuple(awards), tuple(unawarded))


def _blocked(project: RankedProject, team_won: dict[str, str], one_per_team: bool) -> bool:
    return one_per_team and project.team_id in team_won


def _empty_reason(prize: PrizeSpec) -> str:
    if prize.scope == SCOPE_TRACK:
        return EMPTY_TRACK.format(track=prize.track_name or prize.track_id or "this track")
    return EMPTY_OVERALL


def _pool_suffix(prize: PrizeSpec) -> str:
    if prize.scope == SCOPE_TRACK:
        return f" in {prize.track_name or prize.track_id or 'this track'}"
    return ""
