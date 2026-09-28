"""VERDICT judging engine: pure score-normalization maths.

Standard library only, no Django imports, so the portal (`results`
views/services) and the offline proof script share this exact code.
Deterministic: every iteration runs in sorted-id order and no step uses
unseeded randomness.

Model: a review score ``s_r`` (0-100, see :func:`review_score`) is
explained as ``s_r = mu_p + b_j + noise`` (project quality plus a judge
offset). The normalized score of a project is ``mu_p`` from the
shrinkage-penalized least-squares fit (BUILD-SPEC section 9).
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from typing import Collection, Iterable, Mapping, Sequence

#: Convergence tolerance for the iterative fits (BUILD-SPEC 9).
TOL = 1e-10
#: Hard iteration cap for the iterative fits (BUILD-SPEC 9).
MAX_ITER = 10_000

_OFFICIAL_METHODS = ("normalized", "raw", "pairwise")


@dataclass(frozen=True)
class Criterion:
    """One rubric criterion: weight plus the integer scale bounds."""

    key: str
    weight: float = 1.0
    min_score: int = 1
    max_score: int = 5


@dataclass(frozen=True)
class ReviewInput:
    """One submitted review: criterion values keyed by criterion key."""

    review_id: str
    judge_id: str
    project_id: str
    values: Mapping[str, int]


@dataclass(frozen=True)
class ScoredReview:
    """A review reduced to its 0-100 score (see :func:`review_score`)."""

    review_id: str
    judge_id: str
    project_id: str
    score: float


@dataclass(frozen=True)
class Comparison:
    """One pairwise outcome: ``winner`` beat ``loser`` (weight 1, or 0.5 each way for a tie)."""

    winner: str
    loser: str
    weight: float = 1.0


@dataclass
class Fit:
    """Additive-fit result: ``mu`` per project, ``offset`` per judge."""

    mu: dict[str, float] = field(default_factory=dict)
    offset: dict[str, float] = field(default_factory=dict)
    iterations: int = 0
    converged: bool = True


#: Grid for the predeclared adaptive-lambda procedure (BUILD-SPEC 19).
DEFAULT_LAMBDA_GRID: tuple[float, ...] = (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0)

#: Tie tolerance when comparing CV RMSEs: differences at or below this
#: count as ties and resolve to the larger (more conservative) lambda.
_LAMBDA_TIE_TOL = 1e-12


@dataclass(frozen=True)
class LambdaChoice:
    """Outcome of :func:`select_lambda`: chosen value plus the CV table."""

    value: float
    cv_rmse: dict[float, float]
    baseline_rmse: float
    folds: int
    n: int = 0
    skipped: int = 0
    note: str = ""


@dataclass(frozen=True)
class JudgeRow:
    """One row of the organizer judge table."""

    judge_id: str
    n: int
    mean: float
    offset: float
    label: str  # harsh | generous | typical
    spread: float


@dataclass(frozen=True)
class Explanation:
    """How one review feeds one project's normalized score."""

    review_id: str
    judge_id: str
    score: float
    offset: float
    adjusted: float  # score - offset


@dataclass(frozen=True)
class Outlier:
    """A review far from the consensus for a well-reviewed project."""

    review_id: str
    judge_id: str
    project_id: str
    score: float
    residual: float
    sentence: str


@dataclass(frozen=True)
class Diagnostics:
    """Coverage/quality flags over the included reviews."""

    under_reviewed: list[str]
    single_review_judges: list[str]
    constant_scorers: list[str]
    n_components: int


@dataclass(frozen=True)
class Spread:
    """Sigma of per-judge mean scores, raw vs after offset removal."""

    before: float
    after: float


@dataclass
class Result:
    """Everything :func:`evaluate` computes for a results preview."""

    method: str
    lam: float
    target: int
    lambda_choice: LambdaChoice | None = None
    scored: list[ScoredReview] = field(default_factory=list)
    raw: dict[str, tuple[float, int]] = field(default_factory=dict)
    fit: Fit = field(default_factory=Fit)
    normalized: dict[str, float] = field(default_factory=dict)
    rank_raw: dict[str, str] = field(default_factory=dict)
    rank_norm: dict[str, str] = field(default_factory=dict)
    rank_bt: dict[str, str] = field(default_factory=dict)
    rank: dict[str, str] = field(default_factory=dict)  # primary rank for `method`
    judges: dict[str, JudgeRow] = field(default_factory=dict)
    diagnostics: Diagnostics = field(
        default_factory=lambda: Diagnostics([], [], [], 0)
    )
    explanations: dict[str, list[Explanation]] = field(default_factory=dict)
    outliers: list[Outlier] = field(default_factory=list)
    spread_before: float = 0.0
    spread_after: float = 0.0
    strengths: dict[str, float | None] = field(default_factory=dict)
    live_strengths: dict[str, float | None] | None = None
    rank_live: dict[str, str] = field(default_factory=dict)
    pairwise_components: list[list[str]] = field(default_factory=list)
    components: list[set[str]] = field(default_factory=list)
    robustness: Robustness | None = None


def review_score(
    values: Mapping[str, int], criteria: Sequence[Criterion]
) -> float:
    """Map criterion values to a 0-100 score (BUILD-SPEC 9).

    ``100 * sum(w * (v - min) / (max - min)) / sum(w)``. Raises
    ``KeyError`` for a missing criterion and ``ValueError`` for an empty
    rubric, a non-positive weight total, a degenerate scale, or an
    out-of-range value, so bad input fails loudly instead of skewing a
    ranking.
    """
    if not criteria:
        raise ValueError("at least one criterion is required")
    total_weight = sum(c.weight for c in criteria)
    if total_weight <= 0:
        raise ValueError("criterion weights must sum to a positive value")
    scaled = 0.0
    for c in criteria:
        span = c.max_score - c.min_score
        if span <= 0:
            raise ValueError(f"criterion {c.key!r} has a degenerate scale")
        try:
            v = values[c.key]
        except KeyError:
            raise KeyError(f"missing value for criterion {c.key!r}") from None
        if v < c.min_score or v > c.max_score:
            raise ValueError(
                f"value {v} for criterion {c.key!r} outside "
                f"[{c.min_score}, {c.max_score}]"
            )
        scaled += c.weight * (v - c.min_score) / span
    return 100.0 * scaled / total_weight


def score_reviews(
    reviews: Iterable[ReviewInput], criteria: Sequence[Criterion]
) -> list[ScoredReview]:
    """Score every review; output order follows the input order."""
    return [
        ScoredReview(r.review_id, r.judge_id, r.project_id, review_score(r.values, criteria))
        for r in reviews
    ]


def raw_scores(
    reviews: Iterable[ReviewInput], criteria: Sequence[Criterion]
) -> dict[str, tuple[float, int]]:
    """Mean 0-100 score and review count per project, keyed by project id."""
    totals: dict[str, list[float]] = {}
    for r in score_reviews(reviews, criteria):
        totals.setdefault(r.project_id, []).append(r.score)
    return {
        p: (sum(ss) / len(ss), len(ss)) for p, ss in sorted(totals.items())
    }


def fit_additive(
    scored: Sequence[ScoredReview], lam: float, init: Fit | None = None
) -> Fit:
    """Fit ``s_r = mu_p + b_j`` minimizing ``sum (s-mu-b)^2 + lam*sum b^2``.

    Block coordinate descent in sorted-id order (BUILD-SPEC 9): start at
    ``b = 0`` and alternate ``mu_p = mean(s_r - b_j)`` with
    ``b_j = sum(s_r - mu_p) / (n_j + lam)`` until the max change drops
    below 1e-10 (cap 10 000 iterations). With ``lam == 0`` the level is
    unidentified, so re-centre to ``sum_j n_j b_j = 0`` (a uniform shift
    that leaves every ranking unchanged).

    ``init`` is an optional warm start (used by :func:`select_lambda`):
    project means and judge offsets default to its values where the ids
    overlap, otherwise to the cold start above. The fixed point is the
    same either way for ``lam > 0``; the warm start just needs fewer
    iterations.
    """
    if lam < 0:
        raise ValueError("lam must be non-negative")
    scored = list(scored)
    if not scored:
        return Fit(mu={}, offset={}, iterations=0, converged=True)
    proj_ids = sorted({r.project_id for r in scored})
    judge_ids = sorted({r.judge_id for r in scored})
    by_proj: dict[str, list[ScoredReview]] = {p: [] for p in proj_ids}
    by_judge: dict[str, list[ScoredReview]] = {j: [] for j in judge_ids}
    for r in scored:
        by_proj[r.project_id].append(r)
        by_judge[r.judge_id].append(r)
    n_judge = {j: len(rs) for j, rs in by_judge.items()}
    if init is None:
        mu = {p: sum(r.score for r in rs) / len(rs) for p, rs in by_proj.items()}
        offset = {j: 0.0 for j in judge_ids}
    else:
        mu = {
            p: init.mu.get(
                p, sum(r.score for r in by_proj[p]) / len(by_proj[p])
            )
            for p in proj_ids
        }
        offset = {j: init.offset.get(j, 0.0) for j in judge_ids}
    converged = False
    iterations = 0
    for iterations in range(1, MAX_ITER + 1):
        change = 0.0
        for p in proj_ids:
            rs = by_proj[p]
            new = sum(r.score - offset[r.judge_id] for r in rs) / len(rs)
            change = max(change, abs(new - mu[p]))
            mu[p] = new
        for j in judge_ids:
            rs = by_judge[j]
            new = sum(r.score - mu[r.project_id] for r in rs) / (
                n_judge[j] + lam
            )
            change = max(change, abs(new - offset[j]))
            offset[j] = new
        if change < TOL:
            converged = True
            break
    if lam == 0:
        total_n = sum(n_judge.values())
        shift = sum(n_judge[j] * offset[j] for j in judge_ids) / total_n
        for j in judge_ids:
            offset[j] -= shift
        for p in proj_ids:
            mu[p] += shift
    return Fit(mu=mu, offset=offset, iterations=iterations, converged=converged)


def components(reviews: Iterable[ReviewInput | ScoredReview]) -> list[set[str]]:
    """Connected components of the judge-project graph (union-find).

    Nodes are judge and project ids; each review is an edge. Components
    are returned in deterministic order (sorted by their smallest id).
    Cross-component comparisons are only weakly supported, so the count
    is reported alongside every ranking.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in reviews:
        for node in (r.judge_id, r.project_id):
            parent.setdefault(node, node)
        a, b = find(r.judge_id), find(r.project_id)
        if a != b:
            parent[max(a, b)] = min(a, b)
    groups: dict[str, set[str]] = {}
    for node in parent:
        groups.setdefault(find(node), set()).add(node)
    return sorted(groups.values(), key=lambda g: min(g))


def diagnostics(
    reviews: Sequence[ReviewInput],
    target: int,
    all_projects: Collection[str] | None = None,
) -> Diagnostics:
    """Coverage flags: under-reviewed projects, single-review judges, constant scorers.

    A constant scorer has >= 2 reviews with every criterion value
    identical across all of them (fixture: jdg_07); their level is
    absorbed by the offset but they add no ordering information.
    """
    reviews = list(reviews)
    per_project: dict[str, int] = Counter(r.project_id for r in reviews)
    per_judge: dict[str, list[ReviewInput]] = {}
    for r in reviews:
        per_judge.setdefault(r.judge_id, []).append(r)
    known = set(per_project) | set(all_projects or ())
    under = sorted(
        p for p in known if 0 <= per_project.get(p, 0) < target
    )
    single = sorted(j for j, rs in per_judge.items() if len(rs) == 1)
    constant = sorted(
        j
        for j, rs in per_judge.items()
        if len(rs) >= 2
        and len({v for r in rs for v in r.values.values()}) == 1
    )
    return Diagnostics(
        under_reviewed=under,
        single_review_judges=single,
        constant_scorers=constant,
        n_components=len(components(reviews)),
    )


def _pstdev(xs: Sequence[float]) -> float:
    """Population standard deviation (0.0 for fewer than 2 values)."""
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / n)


def judge_table(
    scored: Sequence[ScoredReview], fit: Fit
) -> dict[str, JudgeRow]:
    """Per-judge row: n, mean score given, offset, harsh/generous label, spread."""
    by_judge: dict[str, list[float]] = {}
    for r in scored:
        by_judge.setdefault(r.judge_id, []).append(r.score)
    rows = {}
    for j in sorted(by_judge):
        ss = by_judge[j]
        b = fit.offset.get(j, 0.0)
        label = "harsh" if b < -5 else ("generous" if b > 5 else "typical")
        rows[j] = JudgeRow(
            judge_id=j,
            n=len(ss),
            mean=sum(ss) / len(ss),
            offset=b,
            label=label,
            spread=_pstdev(ss),
        )
    return rows


def explain(
    project_id: str, scored: Sequence[ScoredReview], fit: Fit
) -> list[Explanation]:
    """One row per review of the project: score, judge offset, adjusted score."""
    rows = [
        Explanation(
            review_id=r.review_id,
            judge_id=r.judge_id,
            score=r.score,
            offset=fit.offset.get(r.judge_id, 0.0),
            adjusted=r.score - fit.offset.get(r.judge_id, 0.0),
        )
        for r in scored
        if r.project_id == project_id
    ]
    return sorted(rows, key=lambda e: e.review_id)


def rank(values: Mapping[str, float]) -> dict[str, str]:
    """Competition ranks over values rounded to 2 dp.

    Equal-at-2dp items share a rank prefixed with ``=`` (``"=3"``) and
    the next rank skips accordingly (1, 2, =2, 4 ...); display order
    breaks ties by id so output is deterministic.
    """
    rounded = {k: round(v, 2) for k, v in values.items()}
    counts = Counter(rounded.values())
    out = {}
    for k in values:
        r = 1 + sum(1 for v in rounded.values() if v > rounded[k])
        out[k] = f"={r}" if counts[rounded[k]] > 1 else str(r)
    return out


def _average_ranks(xs: Sequence[float]) -> list[float]:
    """Average ranks (1-based, ties share the mean rank), in input order."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Spearman rho: Pearson correlation of average ranks (ties averaged).

    Returns NaN when either side is constant. Raises ``ValueError`` on
    length mismatch or empty input.
    """
    xs, ys = list(xs), list(ys)
    if len(xs) != len(ys) or not xs:
        raise ValueError("spearman needs two non-empty equal-length inputs")
    ra, rb = _average_ranks(xs), _average_ranks(ys)
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra)
    vb = sum((x - mb) ** 2 for x in rb)
    if va <= 0 or vb <= 0:
        return float("nan")
    return cov / math.sqrt(va * vb)


def kendall_tau(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Kendall tau-b between two score lists (ties corrected).

    Pairs tied on both sides are ignored; pairs tied on one side count
    in that side's denominator only. Returns NaN when undefined (fewer
    than 2 items or no untied pair on a side). Raises ``ValueError`` on
    length mismatch or empty input.
    """
    xs, ys = list(xs), list(ys)
    if len(xs) != len(ys) or not xs:
        raise ValueError("kendall_tau needs two non-empty equal-length inputs")
    conc = disc = tie_x = tie_y = 0
    n = len(xs)
    for i in range(n):
        for j in range(i + 1, n):
            sx = (xs[i] > xs[j]) - (xs[i] < xs[j])
            sy = (ys[i] > ys[j]) - (ys[i] < ys[j])
            if sx != 0 and sy != 0:
                if sx == sy:
                    conc += 1
                else:
                    disc += 1
            elif sx == 0 and sy == 0:
                continue
            elif sx == 0:
                tie_x += 1
            else:
                tie_y += 1
    denom = math.sqrt((conc + disc + tie_x) * (conc + disc + tie_y))
    if denom <= 0:
        return float("nan")
    return (conc - disc) / denom


def bradley_terry_raw(
    comparisons: Sequence[Comparison], items: Collection[str]
) -> dict[str, float | None]:
    """Unnormalized Bradley-Terry strengths at the exact MM fixed point.

    Hunter (2004) MM update with a fixed virtual opponent of strength 1
    (each item gets 1 extra win and 2 extra games against it, so
    undefeated items stay finite). The virtual opponent already fixes the
    scale, so the plain iteration converges to the exact MAP estimate on
    the (augmented, hence strongly connected) graph: no in-loop
    rescaling. Items with no comparisons map to None. Converges at max
    change < 1e-10, cap 10 000.
    """
    items = list(items)
    known = set(items)
    wins: dict[str, float] = {i: 0.0 for i in items}
    pairs: dict[tuple[str, str], list[float]] = {}
    involved: set[str] = set()
    for c in comparisons:
        if c.winner == c.loser or c.weight <= 0:
            continue
        if c.winner not in known or c.loser not in known:
            continue
        wins[c.winner] += c.weight
        a, b = (
            (c.winner, c.loser)
            if c.winner < c.loser
            else (c.loser, c.winner)
        )
        cell = pairs.setdefault((a, b), [0.0, 0.0])
        if a == c.winner:
            cell[0] += c.weight
        else:
            cell[1] += c.weight
        involved.add(c.winner)
        involved.add(c.loser)
    out: dict[str, float | None] = {i: None for i in items}
    active = sorted(involved)
    if not active:
        return out
    opponents: dict[str, list[tuple[str, float]]] = {i: [] for i in active}
    for (a, b), (w_ab, w_ba) in pairs.items():
        opponents[a].append((b, w_ab + w_ba))
        opponents[b].append((a, w_ab + w_ba))
    total_wins = {i: wins[i] + 1.0 for i in active}  # +1 virtual win
    strength = {i: 1.0 for i in active}
    for _ in range(MAX_ITER):
        new = {}
        for i in active:
            denom = 2.0 / (strength[i] + 1.0)  # 2 virtual games, avg strength 1
            for o, games in opponents[i]:
                denom += games / (strength[i] + strength[o])
            new[i] = total_wins[i] / denom if denom > 0 else strength[i]
        change = max(abs(new[i] - strength[i]) for i in active)
        strength = new
        if change < TOL:
            break
    for i in active:
        out[i] = strength[i]
    return out


def bradley_terry(
    comparisons: Sequence[Comparison], items: Collection[str]
) -> dict[str, float | None]:
    """Bradley-Terry strengths via Hunter (2004) MM, with a virtual-opponent prior.

    Every item with at least one real comparison gets 1 extra win and 1
    extra loss against a fixed average-strength virtual opponent, so
    undefeated items stay finite. Output is log-strength (log of the
    normalized strength); items with no comparisons map to None
    (unranked). Converges at max change < 1e-10, cap 10 000.
    """
    raw = bradley_terry_raw(comparisons, items)
    total = sum(s for s in raw.values() if s is not None)
    return {
        i: (math.log(s / total) if s is not None else None)
        for i, s in raw.items()
    }


def derived_comparisons(scored: Sequence[ScoredReview]) -> list[Comparison]:
    """Within-judge pairs from rubric reviews; judge levels cancel out.

    For each judge, every pair of their reviews becomes a comparison won
    by the higher score; exact ties contribute half a win each way.
    """
    by_judge: dict[str, list[ScoredReview]] = {}
    for r in scored:
        by_judge.setdefault(r.judge_id, []).append(r)
    out = []
    for j in sorted(by_judge):
        rs = sorted(by_judge[j], key=lambda r: r.review_id)
        for x in range(len(rs)):
            for y in range(x + 1, len(rs)):
                a, b = rs[x], rs[y]
                if a.project_id == b.project_id:
                    continue
                if a.score > b.score:
                    out.append(Comparison(a.project_id, b.project_id, 1.0))
                elif b.score > a.score:
                    out.append(Comparison(b.project_id, a.project_id, 1.0))
                else:
                    out.append(Comparison(a.project_id, b.project_id, 0.5))
                    out.append(Comparison(b.project_id, a.project_id, 0.5))
    return out


def outliers(
    scored: Sequence[ScoredReview],
    fit: Fit,
    min_reviews: int = 3,
    threshold: float = 2.5,
) -> list[Outlier]:
    """Flag reviews with ``|s - mu_p - b_j| > threshold * residual SD``.

    Only projects with at least ``min_reviews`` reviews qualify, so a
    lone dissenter on a thinly reviewed project is never flagged. Flags
    are detection-only signals for organizers, never auto-exclusions.
    """
    scored = list(scored)
    if not scored:
        return []
    counts = Counter(r.project_id for r in scored)
    residuals = [
        (r, r.score - fit.mu[r.project_id] - fit.offset[r.judge_id])
        for r in scored
    ]
    sd = _pstdev([v for _, v in residuals])
    if sd <= 0:
        return []
    flagged = []
    for r, v in residuals:
        if counts[r.project_id] >= min_reviews and abs(v) > threshold * sd:
            direction = "above" if v > 0 else "below"
            flagged.append(
                Outlier(
                    review_id=r.review_id,
                    judge_id=r.judge_id,
                    project_id=r.project_id,
                    score=r.score,
                    residual=v,
                    sentence=(
                        f"{r.judge_id} scored {r.project_id} "
                        f"{abs(v):.1f} points {direction} consensus"
                    ),
                )
            )
    flagged.sort(key=lambda o: -abs(o.residual))
    return flagged


def spread(scored: Sequence[ScoredReview], fit: Fit) -> Spread:
    """Sigma of per-judge mean scores, raw vs after offset removal.

    Shrinking here is the brief's own figure: judge-level level
    differences should mostly vanish once offsets are removed.
    """
    by_judge: dict[str, list[ScoredReview]] = {}
    for r in scored:
        by_judge.setdefault(r.judge_id, []).append(r)
    raw_means = [
        sum(r.score for r in rs) / len(rs) for rs in by_judge.values()
    ]
    adj_means = [
        sum(r.score - fit.offset.get(r.judge_id, 0.0) for r in rs) / len(rs)
        for rs in by_judge.values()
    ]
    return Spread(before=_pstdev(raw_means), after=_pstdev(adj_means))


@dataclass(frozen=True)
class LooResult:
    """LOO prediction error for one predictor: RMSE, MAE over ``n`` held-out reviews."""

    rmse: float
    mae: float
    n: int


@dataclass(frozen=True)
class LooReport:
    """Leave-one-review-out cross-validation: project-mean baseline plus additive fits."""

    mean_only: LooResult
    additive: dict[float, LooResult]
    skipped: int


def leave_one_out(
    scored: Sequence[ScoredReview],
    lambdas: Sequence[float] = (0.0, 1.0, 2.0, 5.0, 10.0),
) -> LooReport:
    """Refit without each review and predict it as ``mu_p + b_j``.

    The baseline predicts the mean of the held-out review's project
    mates. Reviews whose project or judge has no other review are
    skipped and counted (their level cannot be estimated without them).
    Iteration follows the input order, so results are deterministic.
    """
    scored = list(scored)
    lambdas = tuple(float(lam) for lam in lambdas)
    se_mean = ae_mean = 0.0
    se = {lam: 0.0 for lam in lambdas}
    ae = {lam: 0.0 for lam in lambdas}
    n = skipped = 0
    for i, held in enumerate(scored):
        rest = scored[:i] + scored[i + 1 :]
        mates = [r.score for r in rest if r.project_id == held.project_id]
        judge_kept = any(r.judge_id == held.judge_id for r in rest)
        if not mates or not judge_kept:
            skipped += 1
            continue
        n += 1
        pred_mean = sum(mates) / len(mates)
        se_mean += (pred_mean - held.score) ** 2
        ae_mean += abs(pred_mean - held.score)
        for lam in lambdas:
            fit = fit_additive(rest, lam)
            pred = fit.mu[held.project_id] + fit.offset[held.judge_id]
            se[lam] += (pred - held.score) ** 2
            ae[lam] += abs(pred - held.score)

    def pack(s: float, a: float) -> LooResult:
        if n == 0:
            return LooResult(rmse=float("nan"), mae=float("nan"), n=0)
        return LooResult(rmse=math.sqrt(s / n), mae=a / n, n=n)

    return LooReport(
        mean_only=pack(se_mean, ae_mean),
        additive={lam: pack(se[lam], ae[lam]) for lam in lambdas},
        skipped=skipped,
    )


def offset_variance(scored: Sequence[ScoredReview], lam: float) -> float:
    """Population variance of the fitted judge offsets (the permutation statistic)."""
    fit = fit_additive(list(scored), lam)
    if not fit.offset:
        return 0.0
    xs = sorted(fit.offset.values())
    mean = sum(xs) / len(xs)
    return sum((x - mean) ** 2 for x in xs) / len(xs)


#: Quantile levels reported for the permutation null distribution.
PERM_QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95, 0.99)


@dataclass(frozen=True)
class PermutationResult:
    """Judge-effect permutation test: observed offset variance vs the relabelled null."""

    observed: float
    quantiles: dict[float, float]
    p_value: float  # fraction of null draws >= observed
    n_perm: int


def permutation_test(
    scored: Sequence[ScoredReview],
    project_group: Mapping[str, str],
    n_perm: int = 2000,
    seed: int = 0,
    lam: float = 2.0,
) -> PermutationResult:
    """Shuffle judge labels within groups and refit (seeded, deterministic).

    Each review keeps its project and score; judge labels are permuted
    among reviews in the same ``project_group`` (projects missing from
    the map form singleton groups, so their labels never move). The
    statistic is :func:`offset_variance`: real judge habits inflate the
    observed value, while relabelling destroys them. Groups iterate in
    sorted order and labels shuffle in review order from ``seed``.
    """
    scored = list(scored)
    observed = offset_variance(scored, lam)
    by_group: dict[str, list[int]] = {}
    for i, r in enumerate(scored):
        by_group.setdefault(project_group.get(r.project_id, r.project_id), []).append(
            i
        )
    ordered = [by_group[g] for g in sorted(by_group)]
    rng = random.Random(seed)
    nulls: list[float] = []
    for _ in range(n_perm):
        new_judge = [""] * len(scored)
        for idxs in ordered:
            labels = [scored[i].judge_id for i in idxs]
            rng.shuffle(labels)
            for i, lab in zip(idxs, labels):
                new_judge[i] = lab
        shuffled = [
            ScoredReview(r.review_id, new_judge[i], r.project_id, r.score)
            for i, r in enumerate(scored)
        ]
        nulls.append(offset_variance(shuffled, lam))
    nulls.sort()
    quantiles = (
        {q: nulls[min(n_perm - 1, int(q * n_perm))] for q in PERM_QUANTILES}
        if n_perm
        else {}
    )
    p_value = sum(1 for v in nulls if v >= observed) / n_perm if n_perm else float(
        "nan"
    )
    return PermutationResult(
        observed=observed, quantiles=quantiles, p_value=p_value, n_perm=n_perm
    )


@dataclass(frozen=True)
class RankMover:
    """A project ranked far apart by the normalized and Bradley-Terry orders."""

    project_id: str
    norm_pos: int
    bt_pos: int
    gap: int


@dataclass(frozen=True)
class Agreement:
    """Normalized vs Bradley-Terry ranking agreement over projects ranked by both."""

    rho: float
    tau: float
    n: int
    movers: tuple[RankMover, ...]


def rank_agreement(
    normalized: Mapping[str, float],
    strengths: Mapping[str, float | None],
    threshold: int = 5,
) -> Agreement:
    """Spearman rho / Kendall tau between normalized scores and BT strengths.

    Only projects with a BT strength take part in the correlations.
    ``movers`` lists projects whose competition-rank positions (see
    :func:`rank`) differ by more than ``threshold`` places, worst first.
    """
    paired = sorted(
        (p, mu, strengths[p]) for p, mu in normalized.items() if strengths.get(p) is not None
    )
    xs = [mu for _, mu, _ in paired]
    ys = [s for _, _, s in paired]
    r_norm = {p: int(r.lstrip("=")) for p, r in rank({p: mu for p, mu, _ in paired}).items()}
    r_bt = {p: int(r.lstrip("=")) for p, r in rank({p: s for p, _, s in paired}).items()}
    movers = sorted(
        (
            RankMover(p, r_norm[p], r_bt[p], abs(r_norm[p] - r_bt[p]))
            for p, _, _ in paired
            if abs(r_norm[p] - r_bt[p]) > threshold
        ),
        key=lambda m: (-m.gap, m.project_id),
    )
    if not paired:
        return Agreement(rho=float("nan"), tau=float("nan"), n=0, movers=())
    return Agreement(
        rho=spearman(xs, ys), tau=kendall_tau(xs, ys), n=len(paired), movers=tuple(movers)
    )


def select_lambda(
    scored: Sequence[ScoredReview],
    grid: Sequence[float] = DEFAULT_LAMBDA_GRID,
    folds: int = 5,
    seed: str | int = "verdict",
) -> LambdaChoice:
    """Pick lambda by seeded K-fold cross-validation (BUILD-SPEC 19).

    Fold assignment is deterministic: reviews sort by ``review_id``,
    shuffle with ``random.Random(seed)``, then fold ``k`` holds out
    positions ``i % folds == k``. Each held-out review is predicted as
    ``mu_p + b_j`` from the training fit; reviews whose project or
    judge has no training review are skipped and counted. The baseline
    predicts the training mean of the held-out review's project mates.
    The winner is the argmin CV RMSE; ties (within 1e-12) go to the
    larger, more conservative lambda. Each fold fit warm-starts from
    the full-data fit at the same lambda. When no held-out review can
    be predicted (``n == 0``, e.g. a single judge or every held-out
    review lacking training data), the largest grid lambda is returned
    with ``note`` set and finite (zero) RMSE placeholders, so the
    result stays valid JSON with no NaN.
    """
    grid_t = tuple(float(g) for g in grid)
    if not grid_t:
        raise ValueError("select_lambda needs a non-empty grid")
    if any(g < 0 for g in grid_t):
        raise ValueError("lambda grid values must be non-negative")
    if folds < 2:
        raise ValueError("select_lambda needs at least 2 folds")
    data = list(scored)
    if not data:
        raise ValueError("select_lambda needs at least one scored review")
    ordered = sorted(data, key=lambda r: r.review_id)
    rng = random.Random(seed)
    rng.shuffle(ordered)
    full = {lam: fit_additive(data, lam) for lam in grid_t}
    se_base = 0.0
    se: dict[float, float] = {lam: 0.0 for lam in grid_t}
    n = 0
    skipped = 0
    for k in range(folds):
        train = [r for i, r in enumerate(ordered) if i % folds != k]
        test = [r for i, r in enumerate(ordered) if i % folds == k]
        if not train or not test:
            skipped += len(test)
            continue
        train_projects = {r.project_id for r in train}
        train_judges = {r.judge_id for r in train}
        proj_scores: dict[str, list[float]] = {}
        for r in train:
            proj_scores.setdefault(r.project_id, []).append(r.score)
        proj_mean = {p: sum(ss) / len(ss) for p, ss in proj_scores.items()}
        fold_fits = {lam: fit_additive(train, lam, init=full[lam]) for lam in grid_t}
        for r in test:
            if r.project_id not in train_projects or r.judge_id not in train_judges:
                skipped += 1
                continue
            n += 1
            err_base = proj_mean[r.project_id] - r.score
            se_base += err_base * err_base
            for lam in grid_t:
                f = fold_fits[lam]
                pred = f.mu[r.project_id] + f.offset[r.judge_id]
                err = pred - r.score
                se[lam] += err * err
    if n == 0:
        return LambdaChoice(
            value=max(grid_t),
            cv_rmse={lam: 0.0 for lam in grid_t},
            baseline_rmse=0.0,
            folds=folds,
            n=0,
            skipped=skipped,
            note=(
                "cross-validation not possible; "
                "most conservative lambda used"
            ),
        )
    cv_rmse = {lam: math.sqrt(se[lam] / n) for lam in grid_t}
    baseline_rmse = math.sqrt(se_base / n)
    best = min(grid_t, key=lambda lam: cv_rmse[lam])
    best_rmse = cv_rmse[best]
    tied = [
        lam
        for lam in grid_t
        if cv_rmse[lam] <= best_rmse + _LAMBDA_TIE_TOL
    ]
    value = max(tied)
    return LambdaChoice(
        value=value,
        cv_rmse=cv_rmse,
        baseline_rmse=baseline_rmse,
        folds=folds,
        n=n,
        skipped=skipped,
    )


#: Score a review takes when moved to the rubric midpoint in the
#: flip-margin probe. Every criterion at its scale midpoint contributes
#: half its weight, so the 0-100 score is exactly 50 whatever the weights.
FLIP_MIDPOINT = 50.0

#: Maximum subset size and total refits in the midpoint perturbation search.
FLIP_CAP = 5
FLIP_MAX_EVALUATIONS = 256


@dataclass(frozen=True)
class Robustness:
    """Winner-robustness certificate for one fitted ranking (see :func:`robustness`)."""

    winner: str | None  # unique first place only; None for ties or unavailable
    top_k: tuple[str, ...]  # official top-k, best first
    lam: float  # the reused official lambda (never re-selected here)
    k: int
    n_judges: int  # judges with included reviews
    judge_holds: int  # single-judge removals where 1st place holds
    judges_flip: tuple[str, ...]  # judges whose removal changes the winner
    topk_holds: int  # single-judge removals where the top-k set is unchanged
    judge_winner: dict[str, str | None]  # judge id -> new winner
    judge_topk: dict[str, tuple[str, ...]]  # judge id -> new top-k, best first
    n_reviews: int
    review_holds: int  # single-review removals where 1st place holds
    reviews_flip: tuple[str, ...]  # review ids whose removal changes the winner
    review_winner: dict[str, str | None]  # review id -> new winner
    flip_margin: int | None  # exact minimum in the named search; see flip_status
    flip_cap: int
    flip_reviews: tuple[str, ...]  # winner reviews moved at the margin
    flip_midpoint: float
    summary: str
    judge_summary: str
    review_summary: str
    flip_summary: str
    method: str = "normalized"
    available: bool = True
    winners: tuple[str, ...] = ()
    judge_winners: dict[str, tuple[str, ...]] = field(default_factory=dict)
    review_winners: dict[str, tuple[str, ...]] = field(default_factory=dict)
    assumption: str = ""
    flip_status: str = "not_applicable"
    flip_evaluations: int = 0
    flip_max_evaluations: int = FLIP_MAX_EVALUATIONS
    flip_searched_through: int = 0


def _rank_sets(values: Mapping[str, float], top_k: int):
    """Official rounded winner/top-k sets, including ties at the boundary."""
    ranks = rank(values)
    ordered = sorted(values, key=lambda p: (-round(values[p], 2), p))
    return (
        tuple(p for p in ordered if ranks[p].lstrip("=") == "1"),
        tuple(p for p in ordered if int(ranks[p].lstrip("=")) <= top_k),
    )


def robustness(
    reviews: Sequence[ReviewInput | ScoredReview],
    criteria: Sequence[Criterion] | None,
    lam: float,
    top_k: int = 3,
    flip_cap: int = FLIP_CAP,
    *,
    method: str = "normalized",
    flip_max_evaluations: int = FLIP_MAX_EVALUATIONS,
) -> Robustness:
    """Conditional sensitivity under the official rounded ranking rule.

    Raw means or normalized scores are recomputed after each removal.
    Normalized refits hold the selected lambda fixed, not the complete
    adaptive procedure. Pairwise sensitivity is explicitly unavailable.
    Winner sets (including ties) must be identical for a removal to hold.

    For a unique winner, test subsets of its above-midpoint reviews in
    increasing size, replacing those scores with 50. A found margin is
    the exact minimum for this named perturbation, including creating a
    first-place tie. ``None`` means consult ``flip_status``; the search
    stops at either cap and makes no claim about untested subsets.
    """
    if method not in _OFFICIAL_METHODS:
        raise ValueError(f"method must be one of {_OFFICIAL_METHODS}")
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    if flip_cap < 1:
        raise ValueError("flip_cap must be at least 1")
    if flip_max_evaluations < 1:
        raise ValueError("flip_max_evaluations must be at least 1")
    items = list(reviews)
    if items and isinstance(items[0], ReviewInput):
        if criteria is None:
            raise ValueError("criteria are required to score ReviewInput reviews")
        scored = score_reviews(items, criteria)
    elif items and isinstance(items[0], ScoredReview):
        scored = list(items)
    else:
        scored = []
    if lam < 0:
        raise ValueError("lam must be non-negative")

    scored.sort(key=lambda r: r.review_id)
    midpoint = FLIP_MIDPOINT
    assumption = (
        f"Official normalized ranking, rounded to 2 decimals; conditional on "
        f"the selected lambda = {lam:g}, held fixed in every refit. "
        "The adaptive lambda selection procedure is not rerun."
        if method == "normalized" else
        "Official raw means, rounded to 2 decimals; no lambda adjustment."
    )

    def empty(summary: str, part: str, available: bool = True) -> Robustness:
        return Robustness(
            winner=None,
            top_k=(),
            lam=float(lam),
            k=top_k,
            n_judges=0,
            judge_holds=0,
            judges_flip=(),
            topk_holds=0,
            judge_winner={},
            judge_topk={},
            n_reviews=0,
            review_holds=0,
            reviews_flip=(),
            review_winner={},
            flip_margin=None,
            flip_cap=flip_cap,
            flip_reviews=(),
            flip_midpoint=midpoint,
            summary=summary,
            judge_summary=part,
            review_summary=part,
            flip_summary=part,
            method=method,
            available=available,
            assumption=assumption if available else part,
            flip_max_evaluations=flip_max_evaluations,
        )

    if method == "pairwise":
        return empty(
            "Robustness unavailable for the official pairwise ranking.",
            "Pairwise removal and midpoint sensitivity are not implemented; "
            "rubric normalization is not a substitute for pairwise outcomes.",
            available=False,
        )
    if not scored:
        return empty(
            "No included reviews, so there is no winner to defend.",
            "No included reviews.",
        )

    base = fit_additive(scored, lam) if method == "normalized" else None

    def ranking(data):
        if method == "normalized":
            # A removed edge can split the graph. With lambda=0, a warm
            # start can preserve component levels that a fresh fit would
            # not choose; follow the official cold-start convention.
            values = fit_additive(data, lam, init=base if lam > 0 else None).mu
        else:
            totals: dict[str, list[float]] = {}
            for r in data:
                totals.setdefault(r.project_id, []).append(r.score)
            values = {p: sum(xs) / len(xs) for p, xs in totals.items()}
        return _rank_sets(values, top_k)

    winners, topk = _rank_sets(base.mu, top_k) if base is not None else ranking(scored)
    winner = winners[0] if len(winners) == 1 else None
    topk_set = set(topk)

    def label(ids):
        return ", ".join(ids) if ids else "no ranked project"

    judges = sorted({r.judge_id for r in scored})
    judge_winner: dict[str, str | None] = {}
    judge_winners: dict[str, tuple[str, ...]] = {}
    judge_topk: dict[str, tuple[str, ...]] = {}
    for j in judges:
        first, top = ranking([r for r in scored if r.judge_id != j])
        judge_winners[j] = first
        judge_winner[j] = first[0] if len(first) == 1 else None
        judge_topk[j] = top
    judges_flip = tuple(j for j in judges if judge_winners[j] != winners)
    judge_holds = len(judges) - len(judges_flip)
    topk_holds = sum(
        1 for j in judges if set(judge_topk[j]) == topk_set
    )

    rids = sorted(r.review_id for r in scored)
    review_winner: dict[str, str | None] = {}
    review_winners: dict[str, tuple[str, ...]] = {}
    for rid in rids:
        first, _ = ranking([r for r in scored if r.review_id != rid])
        review_winners[rid] = first
        review_winner[rid] = first[0] if len(first) == 1 else None
    reviews_flip = tuple(rid for rid in rids if review_winners[rid] != winners)
    review_holds = len(rids) - len(reviews_flip)

    flip_margin: int | None = None
    flip_reviews: tuple[str, ...] = ()
    won = sorted(
        (r for r in scored if r.project_id == winner and r.score > midpoint),
        key=lambda r: r.review_id,
    )
    evaluations = 0
    searched_through = 0
    flip_status = "no_change_possible" if len(won) <= flip_cap else "size_capped"
    for m in range(1, min(flip_cap, len(won)) + 1):
        for subset in combinations(won, m):
            if evaluations >= flip_max_evaluations:
                flip_status = "search_capped"
                break
            moved = {r.review_id for r in subset}
            altered = [
                ScoredReview(r.review_id, r.judge_id, r.project_id,
                             midpoint if r.review_id in moved else r.score)
                for r in scored
            ]
            evaluations += 1
            first, _ = ranking(altered)
            if first != winners:
                flip_margin = m
                flip_reviews = tuple(r.review_id for r in subset)
                flip_status = "found"
                break
        if flip_status in ("found", "search_capped"):
            break
        searched_through = m

    if judges_flip:
        detail = "; " + "; ".join(
            f"without {j} 1st goes to {label(judge_winners[j])}" for j in judges_flip
        )
    else:
        detail = "; no single-judge removal changes the first-place set"
    judge_summary = (
        f"1st place ({label(winners)}) holds in {judge_holds} of {len(judges)} "
        f"single-judge removals; the top-{top_k} set (including boundary ties) holds in "
        f"{topk_holds} of {len(judges)}{detail}."
    )
    if reviews_flip:
        rdetail = "; flipping removals: " + ", ".join(
            f"{rid} -> {label(review_winners[rid])}" for rid in reviews_flip
        )
    else:
        rdetail = "; no single-review removal changes the first-place set"
    review_summary = (
        f"1st place ({label(winners)}) holds in {review_holds} of {len(rids)} "
        f"single-review removals{rdetail}."
    )
    if winner is None:
        flip_status = "not_applicable"
        flip_summary = "Shared first place: no unique winner to perturb."
    elif flip_status == "no_change_possible":
        flip_summary = (
            f"No first-place change in any subset of the {len(won)} "
            f"above-midpoint reviews of {winner} moved down to {midpoint:g}. "
            "This is only a midpoint scenario, not a general robustness guarantee."
        )
    elif flip_margin is None:
        flip_summary = (
            f"Midpoint search capped after {evaluations} subsets; all subsets "
            f"of size up to {searched_through} were tested without a change. "
            "The minimum changed-review count is unknown."
        )
    else:
        flip_summary = (
            f"Moving {flip_margin} review(s) of {winner} down to the rubric "
            f"midpoint ({midpoint:g}) changes the first-place set "
            f"(reviews: {', '.join(flip_reviews)}). This is the exact minimum "
            "among subsets of this winner's above-midpoint reviews; "
            "creating a first-place tie counts as a change."
        )
    tie_note = "Shared first place; no unique winner. " if winner is None else ""
    summary = f"{assumption} {tie_note}{judge_summary} {review_summary} {flip_summary}"
    return Robustness(
        winner=winner,
        top_k=topk,
        lam=float(lam),
        k=top_k,
        n_judges=len(judges),
        judge_holds=judge_holds,
        judges_flip=judges_flip,
        topk_holds=topk_holds,
        judge_winner=judge_winner,
        judge_topk=judge_topk,
        n_reviews=len(rids),
        review_holds=review_holds,
        reviews_flip=reviews_flip,
        review_winner=review_winner,
        flip_margin=flip_margin,
        flip_cap=flip_cap,
        flip_reviews=flip_reviews,
        flip_midpoint=midpoint,
        summary=summary,
        judge_summary=judge_summary,
        review_summary=review_summary,
        flip_summary=flip_summary,
        method=method,
        winners=winners,
        judge_winners=judge_winners,
        review_winners=review_winners,
        assumption=assumption,
        flip_status=flip_status,
        flip_evaluations=evaluations,
        flip_max_evaluations=flip_max_evaluations,
        flip_searched_through=searched_through,
    )


def _comparison_components(
    comparisons: Sequence[Comparison], projects: Collection[str]
) -> list[list[str]]:
    """Observed project components; the virtual prior creates no real edges."""
    known = set(projects)
    adjacency: dict[str, set[str]] = {}
    for outcome in comparisons:
        if (outcome.weight <= 0 or outcome.winner == outcome.loser
                or outcome.winner not in known or outcome.loser not in known):
            continue
        adjacency.setdefault(outcome.winner, set()).add(outcome.loser)
        adjacency.setdefault(outcome.loser, set()).add(outcome.winner)
    unseen = set(adjacency)
    groups = []
    while unseen:
        pending = [min(unseen)]
        group = set()
        while pending:
            project = pending.pop()
            if project in group:
                continue
            group.add(project)
            pending.extend(adjacency[project] - group)
        unseen -= group
        groups.append(sorted(group))
    return groups


def evaluate(
    reviews: Sequence[ReviewInput],
    criteria: Sequence[Criterion],
    lam: float | str = 2.0,
    target: int = 3,
    method: str = "normalized",
    projects: Collection[str] | None = None,
    comparisons: Sequence[Comparison] | None = None,
) -> Result:
    """Bundle every engine output for a results preview.

    ``method`` (normalized | raw | pairwise) selects which ranking is
    primary; all three are always computed for the cross-check display.
    ``lam`` is a shrinkage penalty or ``"auto"`` for the predeclared
    :func:`select_lambda` procedure; the chosen value is stored on
    ``Result.lam`` and the full choice on ``Result.lambda_choice``.
    ``Result.robustness`` always carries the :func:`robustness`
    certificate for the official rule, or an explicit unavailable result
    for pairwise sensitivity. Normalized sensitivity holds lambda fixed.

    Explicit ``comparisons`` selects the live source for a pairwise official
    ranking, including an empty list (all unranked). ``None`` preserves the
    legacy rubric-derived source. ``strengths`` and ``rank_bt`` always remain
    the derived cross-check; ``live_strengths``/``rank_live`` hold live output.
    ``pairwise_components`` includes only observed outcome vertices. The
    prior cannot establish comparison evidence between those groups; callers
    must withhold an overall official order when there are multiple groups.
    """
    if method not in _OFFICIAL_METHODS:
        raise ValueError(f"method must be one of {_OFFICIAL_METHODS}")
    reviews = list(reviews)
    scored = score_reviews(reviews, criteria)
    lambda_choice: LambdaChoice | None = None
    if isinstance(lam, str):
        if lam != "auto":
            raise ValueError('lam must be a non-negative number or "auto"')
        if not scored:
            raise ValueError('lam="auto" needs at least one review')
        lambda_choice = select_lambda(scored)
        lam_value = lambda_choice.value
    else:
        lam_value = float(lam)
    live = None if comparisons is None else sorted(
        comparisons, key=lambda c: (c.winner, c.loser, c.weight)
    )
    if live is not None and any(not math.isfinite(c.weight) for c in live):
        raise ValueError("comparison weights must be finite")
    known_set = set(projects or ()) | {r.project_id for r in reviews}
    if projects is None and live is not None:
        known_set.update(c.winner for c in live)
        known_set.update(c.loser for c in live)
    known = sorted(known_set)
    raw = {
        p: (sum(r.score for r in scored if r.project_id == p) / n, n)
        for p in known
        if (n := sum(1 for r in scored if r.project_id == p))
    }
    fit = fit_additive(scored, lam_value)
    normalized = dict(fit.mu)
    rank_raw = rank({p: mean for p, (mean, _n) in raw.items()})
    rank_norm = rank(normalized)
    strengths = bradley_terry(derived_comparisons(scored), known)
    rank_bt = rank({i: s for i, s in strengths.items() if s is not None})
    for i in known:
        rank_bt.setdefault(i, "unranked")
    live_strengths = None if live is None else bradley_terry(live, known)
    rank_live = {}
    live_components = []
    if live_strengths is not None:
        rank_live = rank({p: s for p, s in live_strengths.items() if s is not None})
        for p in known:
            rank_live.setdefault(p, "unranked")
        live_components = _comparison_components(live, known)
    primary = {"normalized": rank_norm, "raw": rank_raw,
               "pairwise": rank_live if live is not None else rank_bt}[
        method
    ]
    judges = judge_table(scored, fit)
    diag = diagnostics(reviews, target, known)
    sp = spread(scored, fit)
    rob = robustness(reviews, criteria, lam_value, method=method)
    return Result(
        method=method,
        lam=lam_value,
        target=target,
        lambda_choice=lambda_choice,
        scored=scored,
        raw=raw,
        fit=fit,
        normalized=normalized,
        rank_raw=rank_raw,
        rank_norm=rank_norm,
        rank_bt=rank_bt,
        rank=dict(primary),
        judges=judges,
        diagnostics=diag,
        explanations={p: explain(p, scored, fit) for p in known},
        outliers=outliers(scored, fit),
        spread_before=sp.before,
        spread_after=sp.after,
        strengths=strengths,
        live_strengths=live_strengths,
        rank_live=rank_live,
        pairwise_components=live_components,
        components=components(scored),
        robustness=rob,
    )


# ---------------------------------------------------------------------------
# Estimability meter (packet P7-ANCHORS, step 2).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EstimabilityJudge:
    """Expected offset precision and detection power for one judge."""

    judge_id: str
    n: int  # reviews by this judge in the design
    se: float  # expected SE of the fitted offset (null-simulation SD)
    power: dict[float, float]  # injected bias size -> detection share


@dataclass(frozen=True)
class EstimabilitySummary:
    """Per-judge estimability plus event-level medians/means."""

    judges: dict[str, EstimabilityJudge]
    median_se: float  # median of per-judge SEs
    power_at_8: float  # mean power at the grid point nearest 8.0
    power_bias: float  # the grid point power_at_8 was read at
    sigma_noise: float
    reps: int
    lam: float
    n_pairs: int


def _median(xs: Sequence[float]) -> float:
    """Median of a non-empty list; NaN when empty."""
    ys = sorted(xs)
    n = len(ys)
    if n == 0:
        return float("nan")
    mid = n // 2
    if n % 2:
        return ys[mid]
    return (ys[mid - 1] + ys[mid]) / 2.0


def estimability(
    design: Sequence[tuple[str, str]],
    sigma_noise: float = 15.0,
    bias_grid: Sequence[float] = (4, 8, 12),
    reps: int = 200,
    seed: str | int = "verdict",
    lam: float = 2.0,
) -> EstimabilitySummary:
    """Expected judge-offset SE and bias-detection power for a design.

    ``design`` is a list of ``(judge_id, project_id)`` pairs (the review
    pattern). The procedure is seeded simulation of the additive model
    used by :func:`fit_additive` at penalty ``lam`` (default 2.0):

    - Null reps: every review scores ``50 + N(0, sigma_noise)``; the
      fitted offset of each judge is recorded. Its SD across reps is
      the expected SE of that judge's offset.
    - The estimator is linear in the scores, so injecting a true bias
      ``b`` into one judge's reviews shifts their fitted offset by
      ``b`` times a deterministic attenuation factor (measured once per
      judge with a noiseless probe). Power at ``b`` is the share of
      null reps where the shifted offset exceeds ``2 * SE``.

    Noise draws run in sorted-pair order from ``random.Random(seed)``;
    refits iterate in sorted-id order, so the result is deterministic.
    Null refits warm-start from the previous rep's fit (same fixed
    point for ``lam > 0``, fewer iterations); the noiseless probes are
    exact up to the 1e-10 fit tolerance.
    """
    grid = tuple(float(b) for b in bias_grid)
    if not grid:
        raise ValueError("estimability needs a non-empty bias_grid")
    if any(b <= 0 for b in grid):
        raise ValueError("bias_grid values must be positive")
    if reps < 1:
        raise ValueError("estimability needs at least 1 rep")
    if sigma_noise < 0:
        raise ValueError("sigma_noise must be non-negative")
    if lam < 0:
        raise ValueError("lam must be non-negative")
    pairs = sorted({(str(j), str(p)) for j, p in design})
    judges = sorted({j for j, _ in pairs})
    if not pairs:
        return EstimabilitySummary(
            judges={},
            median_se=float("nan"),
            power_at_8=float("nan"),
            power_bias=8.0,
            sigma_noise=float(sigma_noise),
            reps=reps,
            lam=float(lam),
            n_pairs=0,
        )
    n_per_judge = {j: sum(1 for jj, _ in pairs if jj == j) for j in judges}
    rng = random.Random(seed)
    null_offsets: dict[str, list[float]] = {j: [] for j in judges}
    prev: Fit | None = None
    for _ in range(reps):
        scored_rep = [
            ScoredReview(
                f"{j}__{p}", j, p, 50.0 + rng.gauss(0.0, sigma_noise)
            )
            for j, p in pairs
        ]
        if prev is not None and lam > 0:
            fit = fit_additive(scored_rep, lam, init=prev)
        else:
            fit = fit_additive(scored_rep, lam)
        prev = fit
        for j in judges:
            null_offsets[j].append(fit.offset.get(j, 0.0))
    se = {j: _pstdev(xs) for j, xs in null_offsets.items()}
    probe = 10.0
    atten: dict[str, float] = {}
    for j in judges:
        probe_scored = [
            ScoredReview(f"{jj}__{pp}", jj, pp, probe if jj == j else 0.0)
            for jj, pp in pairs
        ]
        atten[j] = fit_additive(probe_scored, lam).offset.get(j, 0.0) / probe
    per_judge: dict[str, EstimabilityJudge] = {}
    for j in judges:
        threshold = 2.0 * se[j]
        shift_of = atten[j]
        powers: dict[float, float] = {}
        for b in grid:
            shift = shift_of * b
            if se[j] <= 0:
                powers[b] = 1.0 if shift > threshold else 0.0
            else:
                hits = sum(
                    1 for v in null_offsets[j] if v + shift > threshold
                )
                powers[b] = hits / reps
        per_judge[j] = EstimabilityJudge(
            judge_id=j, n=n_per_judge[j], se=se[j], power=powers
        )
    power_bias = min(grid, key=lambda b: abs(b - 8.0))
    return EstimabilitySummary(
        judges=per_judge,
        median_se=_median([se[j] for j in judges]),
        power_at_8=(
            sum(per_judge[j].power[power_bias] for j in judges) / len(judges)
        ),
        power_bias=power_bias,
        sigma_noise=float(sigma_noise),
        reps=reps,
        lam=float(lam),
        n_pairs=len(pairs),
    )


# ---------------------------------------------------------------------------
# Review-budget planner (packet P7-PLANNER, step 1).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetRow:
    """One grid point of the review-budget curve."""

    reviews_per_judge: int
    median_se: float
    power: dict[float, float]  # injected bias -> mean detection share
    n_pairs: int


@dataclass(frozen=True)
class BudgetCurve:
    """Experimental simulation scenarios, not calibrated live-event power."""

    n_judges: int
    n_projects: int
    grid: tuple[int, ...]
    bias: float  # primary bias for reviews_needed()
    biases: tuple[float, ...]  # all reported bias points
    sigma_noise: float
    sigma_estimated: bool  # retained for compatibility; now always False
    reps: int
    seed: str | int
    lam: float
    rows: tuple[BudgetRow, ...]
    residual_dispersion: float | None = None
    noise_source: str = "explicit_simulation_assumption"
    experimental: bool = True
    assumptions: tuple[str, ...] = (
        "Fixed lambda; adaptive selection is not simulated.",
        "Balanced track-agnostic assignments, one seeded design per budget.",
        "Independent homoskedastic Gaussian errors; scores are not clipped.",
        "One positive judge offset injected at a time; threshold is +2 null SD.",
        "Monte Carlo detection shares are conditional, not calibrated guarantees.",
    )

    def reviews_needed(self, power: float = 0.8) -> int | str:
        """Smallest tested budget reaching ``power`` in this assumed scenario.

        No extrapolation to untested budgets or real-event detection.
        """
        if not 0 <= power <= 1:
            raise ValueError("power must be between 0 and 1")
        for row in sorted(self.rows, key=lambda row: row.reviews_per_judge):
            if row.power.get(self.bias, 0.0) >= power:
                return row.reviews_per_judge
        return "not reached on tested grid"


def pooled_residual_sd(
    scored: Sequence[ScoredReview], lam: float = 2.0
) -> float:
    """In-sample residual dispersion of the additive fit at ``lam``.

    Residuals are ``s_r - mu_p - b_j``; return ``sqrt(SSE / n)``.
    This is not a calibrated estimate of generating noise: fitting
    consumes degrees of freedom and shrinkage affects the residuals.
    """
    data = list(scored)
    if not data:
        raise ValueError("pooled_residual_sd needs at least one review")
    if lam < 0:
        raise ValueError("lam must be non-negative")
    fit = fit_additive(data, lam)
    sse = sum(
        (r.score - fit.mu[r.project_id] - fit.offset[r.judge_id]) ** 2
        for r in data
    )
    return math.sqrt(sse / len(data))


def _balanced_design(
    n_judges: int, n_projects: int, reviews_per_judge: int, seed: str | int
) -> list[tuple[str, str]]:
    """Balanced random design: each judge reviews ``k`` distinct projects.

    Judges/projects are ``J00...``/``P00...`` (track-agnostic, same sets
    across grid values). Each judge's picks go to the currently
    least-covered projects (random choice among ties from ``seed``), so
    project coverage differs by at most ~1 and every pair is unique.
    """
    k = min(reviews_per_judge, n_projects)
    judges = [f"J{i:02d}" for i in range(n_judges)]
    projects = [f"P{i:02d}" for i in range(n_projects)]
    rng = random.Random(f"{seed}:{reviews_per_judge}")
    order = judges[:]
    rng.shuffle(order)
    coverage = {p: 0 for p in projects}
    pairs: set[tuple[str, str]] = set()
    for j in order:
        picked: set[str] = set()
        for _ in range(k):
            cands = [p for p in projects if p not in picked]
            floor = min(coverage[p] for p in cands)
            least = [p for p in cands if coverage[p] == floor]
            choice = least[rng.randrange(len(least))]
            picked.add(choice)
            coverage[choice] += 1
            pairs.add((j, choice))
    return sorted(pairs)


def review_budget_curve(
    n_judges: int,
    n_projects: int,
    reviews_per_judge_grid: Sequence[int] = (3, 4, 6, 8, 10, 12, 16),
    bias: float = 8.0,
    sigma_noise: float | None = None,
    scored: Sequence[ScoredReview] | None = None,
    reps: int = 200,
    seed: str | int = "verdict",
    lam: float = 2.0,
    bias_grid: Sequence[float] | None = None,
) -> BudgetCurve:
    """Experimental conditional detection shares vs reviews per judge.

    For each grid value a balanced random design with ``n_judges``
    judges and ``n_projects`` projects (same id sets, track-agnostic;
    see :func:`_balanced_design`) is scored through :func:`estimability`
    at penalty ``lam``. Each row reports the median expected offset SE
    and the mean detection power at each bias in ``bias_grid``
    (default: the primary ``bias`` plus 12.0, so the proof's power-at-8
    and power-at-12 columns come from one call).

    ``sigma_noise`` must be an explicit simulation assumption. Optional
    ``scored`` inputs report in-sample residual dispersion separately;
    this dispersion is never silently used as generating noise. Compare
    calls at different assumed noise levels to assess sensitivity. This
    does not calibrate the full adaptive live procedure. Pure and
    deterministic: designs derive from
    ``seed`` per grid value and estimability reseeds per row.
    """
    grid = tuple(sorted({int(k) for k in reviews_per_judge_grid}))
    if n_judges < 1:
        raise ValueError("review_budget_curve needs at least 1 judge")
    if n_projects < 1:
        raise ValueError("review_budget_curve needs at least 1 project")
    if not grid:
        raise ValueError("review_budget_curve needs a non-empty grid")
    if any(k < 1 for k in grid):
        raise ValueError("reviews-per-judge grid values must be positive")
    if any(k > n_projects for k in grid):
        raise ValueError("reviews per judge cannot exceed distinct projects")
    if bias <= 0:
        raise ValueError("bias must be positive")
    if reps < 1:
        raise ValueError("review_budget_curve needs at least 1 rep")
    if lam < 0:
        raise ValueError("lam must be non-negative")
    if sigma_noise is None or not math.isfinite(sigma_noise) or sigma_noise < 0:
        raise ValueError("sigma_noise must be an explicit finite non-negative assumption")
    if bias_grid is None:
        biases = tuple(sorted({float(bias), 12.0}))
    else:
        biases = tuple(float(b) for b in bias_grid)
        if not biases:
            raise ValueError("bias_grid must be non-empty")
        if any(b <= 0 for b in biases):
            raise ValueError("bias_grid values must be positive")
    residual_dispersion = pooled_residual_sd(scored, lam) if scored else None
    sigma = float(sigma_noise)
    rows: list[BudgetRow] = []
    for k in grid:
        design = _balanced_design(n_judges, n_projects, k, seed)
        summary = estimability(
            design,
            sigma_noise=sigma,
            bias_grid=biases,
            reps=reps,
            seed=f"{seed}:{k}",
            lam=lam,
        )
        by_bias = {
            b: sum(r.power[b] for r in summary.judges.values())
            / len(summary.judges)
            for b in biases
        }
        rows.append(
            BudgetRow(
                reviews_per_judge=k,
                median_se=summary.median_se,
                power=by_bias,
                n_pairs=summary.n_pairs,
            )
        )
    return BudgetCurve(
        n_judges=n_judges,
        n_projects=n_projects,
        grid=grid,
        bias=float(bias),
        biases=biases,
        sigma_noise=sigma,
        sigma_estimated=False,
        reps=reps,
        seed=seed,
        lam=float(lam),
        rows=tuple(rows),
        residual_dispersion=residual_dispersion,
    )
