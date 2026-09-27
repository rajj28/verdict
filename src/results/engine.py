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
    the full-data fit at the same lambda.
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
        nan_table = {lam: float("nan") for lam in grid_t}
        return LambdaChoice(
            value=max(grid_t),
            cv_rmse=nan_table,
            baseline_rmse=float("nan"),
            folds=folds,
            n=0,
            skipped=skipped,
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

#: Max winner reviews moved in the flip-margin probe; a winner that still
#: holds after that is reported as needing more than the cap (``"> 5"``).
FLIP_CAP = 5


@dataclass(frozen=True)
class Robustness:
    """Winner-robustness certificate for one fitted ranking (see :func:`robustness`)."""

    winner: str | None  # official 1st place (None when there are no reviews)
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
    flip_margin: int | None  # smallest flipping prefix; None means > flip_cap
    flip_cap: int
    flip_reviews: tuple[str, ...]  # winner reviews moved at the margin
    flip_midpoint: float
    summary: str
    judge_summary: str
    review_summary: str
    flip_summary: str


def _ordered(mu: Mapping[str, float]) -> list[str]:
    """Best-first project order; ties break by project id (deterministic)."""
    return sorted(mu, key=lambda p: (-mu[p], p))


def robustness(
    reviews: Sequence[ReviewInput | ScoredReview],
    criteria: Sequence[Criterion] | None,
    lam: float,
    top_k: int = 3,
    flip_cap: int = FLIP_CAP,
) -> Robustness:
    """Certify how hard the fitted winner is to dislodge.

    Three probes, all refits reusing the official ``lam`` (no lambda
    re-selection: the certificate is about the published ranking, and it
    keeps the fixture cost to ~150 warm-started fits) and warm-starting
    from the full-data fit. Iteration is in sorted-id order throughout,
    so the certificate is deterministic.

    - Leave-one-judge-out: drop each judge's reviews, refit, record the
      new winner and top-k.
    - Leave-one-review-out: same per review.
    - Flip margin: move the winner's reviews to the rubric midpoint
      (0-100 score 50, whatever the weights) greedily, most favourable
      first; the margin is the smallest prefix that drops the winner
      from 1st place, capped at ``flip_cap`` (``None`` means more than
      the cap is needed).

    ``reviews`` may be :class:`ReviewInput` (then ``criteria`` is
    required to score them) or already-scored :class:`ScoredReview`
    (then ``criteria`` is unused). ``top_k`` must be at least 1.
    """
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    if flip_cap < 1:
        raise ValueError("flip_cap must be at least 1")
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

    midpoint = FLIP_MIDPOINT
    base = fit_additive(scored, lam)
    ordered = _ordered(base.mu)
    winner = ordered[0] if ordered else None
    topk = tuple(ordered[:top_k])
    topk_set = set(topk)

    def empty(summary: str, part: str) -> Robustness:
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
        )

    if winner is None:
        return empty(
            "No included reviews, so there is no winner to defend.",
            "No included reviews.",
        )

    judges = sorted({r.judge_id for r in scored})
    judge_winner: dict[str, str | None] = {}
    judge_topk: dict[str, tuple[str, ...]] = {}
    for j in judges:
        refit = fit_additive(
            [r for r in scored if r.judge_id != j], lam, init=base
        )
        sub = _ordered(refit.mu)
        judge_winner[j] = sub[0] if sub else None
        judge_topk[j] = tuple(sub[:top_k])
    judges_flip = tuple(j for j in judges if judge_winner[j] != winner)
    judge_holds = len(judges) - len(judges_flip)
    topk_holds = sum(
        1 for j in judges if set(judge_topk[j]) == topk_set
    )

    rids = sorted(r.review_id for r in scored)
    review_winner: dict[str, str | None] = {}
    for rid in rids:
        refit = fit_additive(
            [r for r in scored if r.review_id != rid], lam, init=base
        )
        sub = _ordered(refit.mu)
        review_winner[rid] = sub[0] if sub else None
    reviews_flip = tuple(rid for rid in rids if review_winner[rid] != winner)
    review_holds = len(rids) - len(reviews_flip)

    flip_margin: int | None = None
    flip_reviews: tuple[str, ...] = ()
    won = sorted(
        (r for r in scored if r.project_id == winner),
        key=lambda r: (-r.score, r.review_id),
    )
    tried = min(flip_cap, len(won))
    for m in range(1, tried + 1):
        moved = {r.review_id for r in won[:m]}
        altered = [
            ScoredReview(r.review_id, r.judge_id, r.project_id,
                         midpoint if r.review_id in moved else r.score)
            for r in scored
        ]
        refit = fit_additive(altered, lam, init=base)
        sub = _ordered(refit.mu)
        if not sub or sub[0] != winner:
            flip_margin = m
            flip_reviews = tuple(r.review_id for r in won[:m])
            break

    if judges_flip:
        detail = "; " + "; ".join(
            f"without {j} 1st goes to {judge_winner[j]}" for j in judges_flip
        )
    else:
        detail = "; no single-judge removal changes the winner"
    judge_summary = (
        f"1st place ({winner}) holds in {judge_holds} of {len(judges)} "
        f"single-judge removals; the top-{top_k} set holds in "
        f"{topk_holds} of {len(judges)}{detail}."
    )
    if reviews_flip:
        rdetail = "; flipping removals: " + ", ".join(
            f"{rid} -> {review_winner[rid]}" for rid in reviews_flip
        )
    else:
        rdetail = "; no single-review removal changes the winner"
    review_summary = (
        f"1st place ({winner}) holds in {review_holds} of {len(rids)} "
        f"single-review removals{rdetail}."
    )
    if flip_margin is None:
        flip_summary = (
            f"1st place ({winner}) holds even when {tried} of its reviews "
            f"move to the rubric midpoint ({midpoint:g}); "
            f"flip margin > {flip_cap}."
        )
    elif flip_margin == 1:
        flip_summary = (
            f"Moving 1 review of {winner} to the rubric midpoint "
            f"({midpoint:g}) flips 1st place (review: {flip_reviews[0]})."
        )
    else:
        flip_summary = (
            f"Moving {flip_margin} reviews of {winner} to the rubric "
            f"midpoint ({midpoint:g}) flips 1st place "
            f"(reviews: {', '.join(flip_reviews)})."
        )
    summary = f"{judge_summary} {review_summary} {flip_summary}"
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
    )


def evaluate(
    reviews: Sequence[ReviewInput],
    criteria: Sequence[Criterion],
    lam: float | str = 2.0,
    target: int = 3,
    method: str = "normalized",
    projects: Collection[str] | None = None,
) -> Result:
    """Bundle every engine output for a results preview.

    ``method`` (normalized | raw | pairwise) selects which ranking is
    primary; all three are always computed for the cross-check display.
    ``lam`` is a shrinkage penalty or ``"auto"`` for the predeclared
    :func:`select_lambda` procedure; the chosen value is stored on
    ``Result.lam`` and the full choice on ``Result.lambda_choice``.
    ``Result.robustness`` always carries the :func:`robustness`
    certificate for the fitted (normalized) ranking at that lambda.
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
    known = sorted(set(projects or ()) | {r.project_id for r in reviews})
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
    primary = {"normalized": rank_norm, "raw": rank_raw, "pairwise": rank_bt}[
        method
    ]
    judges = judge_table(scored, fit)
    diag = diagnostics(reviews, target, known)
    sp = spread(scored, fit)
    rob = robustness(reviews, criteria, lam_value)
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
