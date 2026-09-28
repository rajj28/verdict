#!/usr/bin/env python3
"""Generate docs/NORMALIZATION-PROOF.md from fixtures.json (stdlib only).

Reads the fixture, applies the BUILD-SPEC 8 import rules (prj_07
superseded by prj_41 and excluded; equal-weight 1-5 rubric on
functionality/quality/innovation), runs the portal engine
(src/results/engine.py) on the included reviews, and writes the proof
deterministically (fixed seeds, sorted-id iteration, no timestamps).
"""

import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from results import engine as E  # noqa: E402
from judging import assign as A  # noqa: E402

FIXTURES_PATH = REPO / "fixtures.json"
OUT_PATH = REPO / "docs" / "NORMALIZATION-PROOF.md"

LAM_DEFAULT = 2.0
LAM_GRID = (0, 1, 2, 5)
LOO_LAMS = (0, 1, 2, 5, 10)
PERM_N = 2000
PERM_SEED = 97531
PERM_LAM = 2.0
TARGET = 3  # default event.reviews_per_project (BUILD-SPEC 4)
SUPERSEDED = "prj_07"
SUPERSEDED_BY = "prj_41"
CRITERIA_KEYS = ("functionality", "quality", "innovation")
CONSTANT_JUDGE = "jdg_07"  # the fixture's all-4s scorer

REPS = 100
SIGMAS = (0.0, 0.3, 0.6, 1.0)
SEEDS = {0.0: 4404, 0.3: 1101, 0.6: 2202, 1.0: 3303}

# Adaptive-lambda simulation budget: the 5-fold CV rule (same as the
# fixture) costs ~40 fits per replication, so it runs on a subset only.
ADAPTIVE_REPS = 20

# "Designing for calibration" (packet P7-ANCHORS, step 3): same review
# budget as the fixture design, fixture pattern vs an anchor pattern.
ANCHORS_PER_TRACK = 1
ANCHOR_TARGET = 3  # same per-project target as the fixture default
ANCHOR_SEED = "verdict"  # seed for the anchor-pick in assign.propose_assignments
CAL_EST_SEED = "verdict-cal"  # seed for both estimability() calls
CAL_RANK_REPS = 100
CAL_RANK_SIGMA_B = 0.6
CAL_RANK_SEED = 2202  # same seed as the sigma_b = 0.6 row above

# Review-budget planner (packet P7-PLANNER, steps 1-2): balanced random
# designs with the fixture's 30 judges / 40 projects. Generating noise
# is assumed explicitly across sensitivity scenarios, not estimated.
BUDGET_JUDGES = 30
BUDGET_PROJECTS = 40
BUDGET_GRID = (3, 4, 6, 8, 10, 12, 16)
BUDGET_BIAS = 8.0
BUDGET_REPS = 200
BUDGET_SEED = "verdict-budget"
BUDGET_NOISE_MULTIPLIERS = (0.75, 1.0, 1.5, 2.0)


# ---------------------------------------------------------------- fixtures

def load_fixture():
    """Apply the import rules; return criteria, reviews, titles, names, notes."""
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    criteria = [E.Criterion(key, 1.0, 1, 5) for key in CRITERIA_KEYS]
    titles = {p["id"]: p["title"] for p in data["projects"]}
    names = {j["id"]: j["name"] for j in data["judges"]}
    reviews = []
    excluded = 0
    for s in data["scores"]:
        if s["project"] == SUPERSEDED:
            excluded += 1
            continue
        reviews.append(
            E.ReviewInput(
                f"{s['judge']}__{s['project']}",
                s["judge"],
                s["project"],
                dict(s["criteria"]),
            )
        )
    return criteria, reviews, titles, names, excluded


def project_tracks():
    """Map project id to track id (for the within-track permutation test)."""
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    return {p["id"]: p["track"] for p in data["projects"]}


# ------------------------------------------------------------------ stats

spearman = E.spearman


def ranknum(rank_str):
    return int(rank_str.lstrip("="))


def order_projects(scores, projects):
    """Best-first project order; None scores tie last (by id)."""
    scored = [(0, -scores[p], p) for p in projects if scores.get(p) is not None]
    missing = [(1, 0.0, p) for p in projects if scores.get(p) is None]
    return [p for _, _, p in sorted(scored) + sorted(missing)]


def top5(ordered):
    return set(ordered[:5])


# --------------------------------------------------------------- simulation

def simulate(sigma, seed, design, projects):
    """One sigma setting: mean rho, top-5 recall, best-first rate per method.

    The "adaptive" method applies the same predeclared 5-fold CV rule as
    the fixture (:func:`select_lambda` over the default grid) inside each
    replication. It runs on the first ADAPTIVE_REPS replications only
    (subset for runtime); every other method uses all REPS.
    """
    rng = random.Random(seed)
    judges = sorted({j for j, _ in design})
    acc = {
        m: {"rho": 0.0, "top5": 0.0, "best": 0.0}
        for m in ("raw", "zscore", "add0", "add2", "bt")
    }
    acc_ad = {"rho": 0.0, "top5": 0.0, "best": 0.0}
    ad_lams: list[float] = []
    z_skips = 0
    for rep in range(REPS):
        truth = {p: rng.gauss(0.0, 1.0) for p in projects}
        bias = {j: rng.gauss(0.0, sigma) for j in judges}
        reviews = []
        for j, p in design:
            if j == CONSTANT_JUDGE:
                values = {k: 4 for k in CRITERIA_KEYS}  # like fixture jdg_07
            else:
                values = {}
                for k in CRITERIA_KEYS:
                    v = 3.0 + truth[p] + bias[j] + rng.gauss(0.0, 0.5)
                    values[k] = min(5, max(1, round(v)))
            reviews.append(E.ReviewInput(f"{j}__{p}", j, p, values))
        criteria = [E.Criterion(k, 1.0, 1, 5) for k in CRITERIA_KEYS]
        scored = E.score_reviews(reviews, criteria)
        estimates = {}
        estimates["raw"] = {
            p: (sum(r.score for r in scored if r.project_id == p)
                / sum(1 for r in scored if r.project_id == p))
            for p in projects
        }
        by_judge = defaultdict(list)
        for r in scored:
            by_judge[r.judge_id].append(r)
        z, skipped = {}, False
        for j, rs in by_judge.items():
            ss = [r.score for r in rs]
            mean = sum(ss) / len(ss)
            var = sum((x - mean) ** 2 for x in ss) / len(ss)
            if var <= 0:
                skipped = True  # zero-variance judge (always the constant one)
                continue
            sd = math.sqrt(var)
            for r in rs:
                z.setdefault(r.project_id, []).append((r.score - mean) / sd)
        z_skips += skipped
        estimates["zscore"] = {p: sum(v) / len(v) for p, v in z.items()}
        estimates["add0"] = dict(E.fit_additive(scored, 0.0).mu)
        estimates["add2"] = dict(E.fit_additive(scored, 2.0).mu)
        strengths = E.bradley_terry(E.derived_comparisons(scored), projects)
        floor = min([s for s in strengths.values() if s is not None] or [0.0])
        estimates["bt"] = {
            p: (strengths[p] if strengths[p] is not None else floor - 1.0)
            for p in projects
        }
        true_vals = [truth[p] for p in projects]
        true_top5 = top5(order_projects(truth, projects))
        true_best = order_projects(truth, projects)[0]
        for m, est in estimates.items():
            vals = [est[p] if est.get(p) is not None else float("-inf")
                    for p in projects]
            acc[m]["rho"] += spearman(vals, true_vals)
            ordered = order_projects(est, projects)
            acc[m]["top5"] += len(top5(ordered) & true_top5) / 5.0
            acc[m]["best"] += ordered[0] == true_best
        if rep < ADAPTIVE_REPS:
            choice = E.select_lambda(scored)
            ad_lams.append(choice.value)
            est_ad = dict(E.fit_additive(scored, choice.value).mu)
            vals = [est_ad[p] for p in projects]
            acc_ad["rho"] += spearman(vals, true_vals)
            ordered = order_projects(est_ad, projects)
            acc_ad["top5"] += len(top5(ordered) & true_top5) / 5.0
            acc_ad["best"] += ordered[0] == true_best
    out = {
        m: (v["rho"] / REPS, v["top5"] / REPS, v["best"] / REPS)
        for m, v in acc.items()
    }
    out["adaptive"] = (
        acc_ad["rho"] / ADAPTIVE_REPS,
        acc_ad["top5"] / ADAPTIVE_REPS,
        acc_ad["best"] / ADAPTIVE_REPS,
    )
    mean_lam = sum(ad_lams) / len(ad_lams) if ad_lams else float("nan")
    return out, z_skips / REPS, mean_lam


# ------------------------------------------------- calibration comparison

def anchor_design(budget):
    """Anchor pattern at the fixture review budget.

    Runs the portal assignment algorithm
    (``judging.assign.propose_assignments``) with ``anchors_per_track=1``
    and the default target, then deterministically drops non-anchor
    assignments from the most-covered projects until the total equals
    ``budget`` ("reducing other assignments to keep the budget"). No
    conflicts exist in the fixture, so every track judge takes the
    anchor. Returns ``(pairs, anchor_pairs, anchor_ids, n_unfilled,
    n_before_trim)`` as sorted lists/counts.
    """
    data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    judge_inputs = [
        A.JudgeInput(j["id"], frozenset(j["tracks"])) for j in data["judges"]
    ]
    project_inputs = [
        A.ProjectInput(p["id"], p["track"], p["team"])
        for p in data["projects"]
        if p["id"] != SUPERSEDED
    ]
    proposal = A.propose_assignments(
        judge_inputs,
        project_inputs,
        target=ANCHOR_TARGET,
        seed=ANCHOR_SEED,
        anchors_per_track=ANCHORS_PER_TRACK,
    )
    anchors = {
        (a.judge_id, a.project_id)
        for a in proposal.new_assignments
        if a.is_anchor
    }
    kept = {(a.judge_id, a.project_id) for a in proposal.new_assignments}
    non_anchor = sorted(kept - anchors)
    while len(kept) > budget and non_anchor:
        cover = Counter(p for _, p in kept)
        load = Counter(j for j, _ in kept)
        cands = sorted({p for _, p in non_anchor}, key=lambda p: (cover[p], p))
        doomed = cands[-1]
        jp = sorted(
            ((j, p) for j, p in non_anchor if p == doomed),
            key=lambda t: (load[t[0]], t[0]),
        )
        drop = jp[-1]
        kept.discard(drop)
        non_anchor.remove(drop)
    return (
        sorted(kept),
        sorted(anchors),
        list(proposal.anchor_project_ids),
        len(proposal.unfilled),
        len(proposal.new_assignments),
    )


def rank_recovery(design, projects, sigma_b, seed, reps):
    """Mean Spearman rho with truth for raw means and additive lam=2.

    Same generative model as :func:`simulate` (truth ~ N(0,1), judge
    offsets ~ N(0, sigma_b) on the 1-5 scale, noise ~ N(0, 0.5) per
    criterion, constant judge all 4s). Projects with no reviews in the
    design score worst (they would be unranked, ranked here as tied
    last so the correlation stays defined).
    """
    rng = random.Random(seed)
    judges = sorted({j for j, _ in design})
    acc = {"raw": 0.0, "add2": 0.0}
    for _ in range(reps):
        truth = {p: rng.gauss(0.0, 1.0) for p in projects}
        bias = {j: rng.gauss(0.0, sigma_b) for j in judges}
        reviews = []
        for j, p in design:
            if j == CONSTANT_JUDGE:
                values = {k: 4 for k in CRITERIA_KEYS}  # like fixture jdg_07
            else:
                values = {}
                for k in CRITERIA_KEYS:
                    v = 3.0 + truth[p] + bias[j] + rng.gauss(0.0, 0.5)
                    values[k] = min(5, max(1, round(v)))
            reviews.append(E.ReviewInput(f"{j}__{p}", j, p, values))
        criteria = [E.Criterion(k, 1.0, 1, 5) for k in CRITERIA_KEYS]
        scored = E.score_reviews(reviews, criteria)
        by_project: dict[str, list[float]] = defaultdict(list)
        for r in scored:
            by_project[r.project_id].append(r.score)
        raw = {
            p: (sum(by_project[p]) / len(by_project[p]) if p in by_project
                else float("-inf"))
            for p in projects
        }
        fitted = dict(E.fit_additive(scored, 2.0).mu)
        add = {p: fitted.get(p, float("-inf")) for p in projects}
        true_vals = [truth[p] for p in projects]
        acc["raw"] += spearman([raw[p] for p in projects], true_vals)
        acc["add2"] += spearman([add[p] for p in projects], true_vals)
    return {m: v / reps for m, v in acc.items()}


def organizer_spread(values_by_judge):
    """Sample SD of per-judge mean scores (organizers' 1-5 definition)."""
    import statistics

    means = [
        sum(v) / len(v) for v in values_by_judge.values() if v
    ]
    if len(means) < 2:
        return float("nan"), {}
    return statistics.stdev(means), {
        j: sum(v) / len(v) for j, v in values_by_judge.items() if v
    }


# ------------------------------------------------------------------- render

def f2(x):
    return f"{x:.2f}"


def arrow(raw_r, norm_r):
    d = ranknum(raw_r) - ranknum(norm_r)
    if d > 0:
        return f"\u25b2{d}"
    if d < 0:
        return f"\u25bc{-d}"
    return "="


def main():
    criteria, reviews, titles, names, n_excluded = load_fixture()
    projects = sorted({r.project_id for r in reviews})
    design = sorted({(r.judge_id, r.project_id) for r in reviews})

    fits = {lam: E.evaluate(reviews, criteria, lam=lam, target=TARGET) for lam in LAM_GRID}
    base = E.evaluate(reviews, criteria, lam="auto", target=TARGET)
    auto = base.lambda_choice
    assert auto is not None
    bt_order = order_projects(base.strengths, projects)

    lines = []
    add = lines.append
    grid_str = ", ".join(f"{g:g}" for g in E.DEFAULT_LAMBDA_GRID)
    add("# Normalization proof")
    add("")
    add("Method in one paragraph: every review is reduced to a 0-100 score by "
        "equal-weighted rescaling of the three 1-5 criteria "
        "(functionality, quality, innovation), then fitted to the additive "
        "model `score = project quality + judge offset` by block coordinate "
        "descent minimizing `sum (s - mu - b)^2 + lambda * sum b^2` "
        "(BUILD-SPEC 9). The normalized project score "
        "is `mu`; ranks below are competition ranks with ties shared at 2 dp. "
        "Lambda is not hand-picked: the locked policy selects it by seeded "
        "5-fold cross-validation over the grid "
        f"({grid_str}) at calculation time (ties go to the larger, more "
        "conservative value; BUILD-SPEC 19), and the chosen value is "
        f"recorded with the result. On this fixture the procedure selects "
        f"`lambda = {auto.value:g}` (see the CV table below). "
        "The portal results page runs this same code "
        "(`src/results/engine.py`), so these numbers match it exactly. "
        f"Source: `fixtures.json` ({len(reviews)} included reviews over "
        f"{len(projects)} projects after the duplicate exclusion).")
    add("")
    add("## Fixture results")
    add("")
    add(f"Duplicate handling: `{SUPERSEDED}` (titled "
        f"\"{titles.get(SUPERSEDED, '?')}\") was superseded by `{SUPERSEDED_BY}` "
        f"and excluded with its {n_excluded} reviews; `{SUPERSEDED_BY}` keeps its "
        "own 4 reviews. Judge `jdg_01` (Tomas Varga) reviewed only the "
        "superseded project, so they contribute 0 included reviews.")
    add("")
    add("| Norm rank | Raw rank | Move | Project | Title | n | Raw | Normalized |")
    add("|---|---|---|---|---|---:|---:|---:|")
    for p in sorted(projects, key=lambda p: (-base.normalized[p], titles[p])):
        mean, n = base.raw[p]
        add(f"| {base.rank_norm[p]} | {base.rank_raw[p]} | "
            f"{arrow(base.rank_raw[p], base.rank_norm[p])} | `{p}` | "
            f"{titles[p]} | {n} | {f2(mean)} | {f2(base.normalized[p])} |")
    add("")
    add(f"### Judge offsets (adaptive lambda = {auto.value:g})")
    add("")
    add("| Judge | Name | n | Mean given | Offset | Label | Spread |")
    add("|---|---|---:|---:|---:|---|---:|")
    for j, row in sorted(base.judges.items(), key=lambda kv: (kv[1].offset, kv[0])):
        mark = " \u2014 constant scorer" if j == CONSTANT_JUDGE else ""
        add(f"| `{j}` | {names.get(j, '?')}{mark} | {row.n} | {f2(row.mean)} | "
            f"{row.offset:+.2f} | {row.label} | {f2(row.spread)} |")
    add("")
    add("`jdg_07` (Iva Petrova) is the constant scorer: 3 reviews, every "
        "criterion a 4. `jdg_01` gave a single review to superseded `prj_07`, "
        "so they have no included reviews and no offset (a single-review "
        "offset would be pure shrinkage anyway).")
    add("")
    moved = [(p, ranknum(base.rank_raw[p]) - ranknum(base.rank_norm[p]))
             for p in projects]
    movers = sorted([m for m in moved if m[1] != 0], key=lambda m: -abs(m[1]))
    add(f"### Rank movements (raw \u2192 normalized): {len(movers)} of "
        f"{len(projects)} projects move")
    add("")
    for p, d in movers:
        direction = f"\u25b2{d}" if d > 0 else f"\u25bc{-d}"
        add(f"- {direction} `{p}` ({titles[p]}): "
            f"raw {base.rank_raw[p]} \u2192 normalized {base.rank_norm[p]}")
    add("")
    add(f"### Spread before/after: {f2(base.spread_before)} \u2192 "
        f"{f2(base.spread_after)}")
    add("")
    add("Sigma of per-judge mean scores moves from "
        f"{f2(base.spread_before)} raw to {f2(base.spread_after)} after offset "
        "removal: with the adaptive choice (near-maximal shrinkage) the "
        "fitted offsets are close to zero, so almost nothing is removed. "
        "Raw spread can reflect assigned project quality, judge scoring "
        "levels, and noise. These summaries do not identify how much "
        "comes from each source; nonzero spread alone does not establish bias.")
    add("")
    add("### Judge spread, organizers' definition")
    add("")
    fixture_data = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    all_by_judge: dict[str, list[float]] = defaultdict(list)
    for s in fixture_data["scores"]:
        all_by_judge[s["judge"]].append(
            sum(s["criteria"].values()) / len(s["criteria"])
        )
    inc_by_judge: dict[str, list[float]] = defaultdict(list)
    for s in fixture_data["scores"]:
        if s["project"] == SUPERSEDED:
            continue
        inc_by_judge[s["judge"]].append(
            sum(s["criteria"].values()) / len(s["criteria"])
        )
    sd_all, mean_all = organizer_spread(all_by_judge)
    sd_inc, mean_inc = organizer_spread(inc_by_judge)
    scored_inc = E.score_reviews(reviews, criteria)
    spread_lams = (auto.value, 0.0, 2.0)
    fits_15 = {lam: E.fit_additive(scored_inc, float(lam)) for lam in spread_lams}
    sd_adj = {}
    for lam in spread_lams:
        fit = fits_15[lam]
        adj = {
            j: mean_inc[j] - fit.offset.get(j, 0.0) / 25.0
            for j in mean_inc
        }
        sd_adj[float(lam)], _ = organizer_spread(
            {j: [v] for j, v in adj.items()}
        )
    add(f"Sample SD of per-judge mean scores (mean of the three 1\u20135 "
        f"criteria per review): {sd_all:.4f} over all "
        f"{sum(len(v) for v in all_by_judge.values())} reviews and "
        f"{len(mean_all)} judges "
        f"(this matches the homepage \u03c3 = 0.42 confirmed by the "
        f"organizers) \u2192 {sd_inc:.4f} after the duplicate policy "
        f"excludes `{SUPERSEDED}`'s reviews "
        f"({sum(len(v) for v in inc_by_judge.values())} reviews, "
        f"{len(mean_inc)} judges) \u2192 {sd_adj[float(auto.value)]:.4f} "
        f"after offset removal at the CV-chosen \u03bb={auto.value:g} "
        f"(\u03bb=0: {sd_adj[0.0]:.4f}; \u03bb=2: {sd_adj[2.0]:.4f}).")
    add("")
    add("The first drop comes from removing the single all-2s review on the "
        "superseded project, the second step removes the fitted judge levels "
        "at each stated shrinkage, and a smaller spread after that removal "
        "is not itself evidence that the ranking improved \u2014 the check "
        "for improvement is held-out prediction, the permutation test and "
        "the simulations, not the spread.")
    add("")
    add("### The judge who marks everything the same")
    add("")
    row = base.judges[CONSTANT_JUDGE]
    add(f"`{CONSTANT_JUDGE}` ({names.get(CONSTANT_JUDGE, '?')}) gave "
        f"{row.n} reviews with identical criterion values everywhere "
        f"(all 4s, score {f2(row.mean)}). The model absorbs that level into "
        f"their offset ({row.offset:+.2f}, labelled {row.label}) and their "
        "reviews add no ordering information: they only pull their three "
        "projects toward a common value. They are kept by default and shown "
        "with this explanation; an organizer may exclude them with a recorded "
        "reason before publication.")
    without = E.evaluate(
        [r for r in reviews if r.judge_id != CONSTANT_JUDGE],
        criteria, lam=base.lam, target=TARGET, projects=projects,
    )
    changed = [p for p in projects
               if p in without.rank_norm
               and without.rank_norm[p] != base.rank_norm[p]]
    if changed:
        add(f"Excluding `{CONSTANT_JUDGE}` changes the normalized rank of "
            f"{len(changed)} project(s): " +
            ", ".join(f"`{p}` ({titles[p]}: {base.rank_norm[p]} \u2192 "
                      f"{without.rank_norm[p]})" for p in sorted(changed)) + ".")
    else:
        add(f"Excluding `{CONSTANT_JUDGE}` changes no normalized rank: none of "
            "the rank order depends on their level-only reviews.")
    add("")
    add("### Outliers (|residual| > 2.5 x SD, projects with >= 3 reviews)")
    add("")
    if base.outliers:
        for o in base.outliers:
            direction = "above" if o.residual > 0 else "below"
            add(f"- `{o.judge_id}` ({names.get(o.judge_id, '?')}) scored "
                f"`{o.project_id}` ({titles[o.project_id]}) "
                f"{abs(o.residual):.1f} points {direction} consensus "
                f"(score {f2(o.score)}).")
    else:
        add("No review meets the outlier bar on the fixture data.")
    add("")
    add("Outliers are detection-only signals for organizers (possible "
        "collusion or undeclared conflict) and are never auto-excluded.")
    add("")
    add("### Adaptive lambda selection (seeded 5-fold CV)")
    add("")
    add(f"The predeclared procedure shuffles the {len(reviews)} included "
        f"reviews by sorted review id with seed `verdict`, holds out each "
        f"fifth in turn, refits on the rest, and predicts held-out scores "
        f"as `mu_p + b_j` ({auto.n} predicted, {auto.skipped} skipped where "
        f"the project or judge has no training review). The baseline "
        f"predicts the training mean of the held-out review's project "
        f"mates. Winner is the smallest CV RMSE; ties go to the larger "
        f"lambda. Selected: `lambda = {auto.value:g}`.")
    add("")
    add("| Predictor | CV RMSE |")
    add("|---|---:|")
    add(f"| project mean only | {auto.baseline_rmse:.2f} |")
    for lam in E.DEFAULT_LAMBDA_GRID:
        add(f"| additive \u03bb={lam:g} | {auto.cv_rmse[float(lam)]:.2f} |")
    add("")
    add("### Lambda sensitivity (vs lambda = 2)")
    add("")
    mu2 = fits[2].normalized
    add("| \u03bb | Top-5 (normalized) | Spearman \u03c1 vs \u03bb=2 |")
    add("|---:|---|---:|")
    for lam in LAM_GRID:
        top = order_projects(fits[lam].normalized, projects)[:5]
        rho = 1.0 if lam == 2 else spearman(
            [fits[lam].normalized[p] for p in projects],
            [mu2[p] for p in projects])
        add(f"| {lam} | " + ", ".join(f"`{p}`" for p in top) + f" | {rho:.4f} |")
    add("")
    add(f"Judge\u2013project graph components: {base.diagnostics.n_components} "
        "(cross-component comparisons would be flagged weakly supported). "
        f"Under-reviewed (< {TARGET}): " +
        (", ".join(f"`{p}`" for p in base.diagnostics.under_reviewed) or "none") +
        ". Single-review judges (included reviews): " +
        (", ".join(f"`{j}`" for j in base.diagnostics.single_review_judges) or "none") +
        ". Derived Bradley\u2013Terry cross-check top-5: " +
        ", ".join(f"`{p}`" for p in bt_order[:5]) + ".")
    add("")
    add("## Simulations")
    add("")
    add("Design: the fixture's exact judge\u2013project review pattern "
        f"({len(design)} pairs). Per replication: true project quality ~ N(0,1), "
        "judge offsets ~ N(0, \u03c3_b) with \u03c3_b in {0.0, 0.3, 0.6, 1.0} "
        "score points on the 1\u20135 scale (\u03c3_b = 0.0 is the fair-judge "
        "control: no systematic judge bias, so any gap to raw means is the "
        "cost of normalizing), noise ~ N(0, 0.5) per "
        "criterion, rounded and clipped to integer 1\u20135; one constant "
        f"judge (`{CONSTANT_JUDGE}`, all 4s) like the fixture. "
        f"{REPS} replications per \u03c3_b. Methods: raw mean, per-judge "
        "z-score (zero-variance judges skipped), additive \u03bb=0, additive "
        "\u03bb=2, derived Bradley\u2013Terry, plus adaptive (the same "
        "predeclared 5-fold CV rule over the default grid, applied inside "
        f"each replication; {ADAPTIVE_REPS} replications per \u03c3_b for "
        "this method only, to keep the total runtime under 3 minutes). "
        "Reported: mean Spearman "
        "\u03c1 with truth, top-5 recall, share of replications where the "
        "true best project ranks first.")
    add("")
    mean_lams: dict[float, float] = {}
    for sigma in SIGMAS:
        res, skip_rate, mean_lam = simulate(sigma, SEEDS[sigma], design, projects)
        mean_lams[sigma] = mean_lam
        add(f"### \u03c3_b = {sigma} (seed {SEEDS[sigma]})")
        add("")
        add("| Method | Mean \u03c1 | Top-5 recall | Best ranked first |")
        add("|---|---:|---:|---:|")
        for m in ("raw", "zscore", "add0", "add2", "bt"):
            rho, top5r, best = res[m]
            add(f"| {m} | {rho:.3f} | {top5r:.3f} | {best:.3f} |")
        rho, top5r, best = res["adaptive"]
        add(f"| adaptive ({ADAPTIVE_REPS} reps) | {rho:.3f} | {top5r:.3f} | {best:.3f} |")
        add("")
        add(f"Zero-variance judges skipped in {skip_rate:.1%} of replications "
            "(the forced constant judge is skipped every replication; natural "
            "zero-variance judges are rare).")
        add("")
    add("Reading the tables in plain language: with fair judges "
        "(\u03c3_b = 0.0) normalization costs essentially nothing \u2014 "
        "additive \u03bb=2 matches raw means on rank correlation \u2014 so "
        "shrinkage is cheap insurance. Once judges disagree "
        "(\u03c3_b \u2265 0.3), \u03bb=2 beats raw means on both average rank "
        "correlation and top-5 recovery in every biased setting, while the "
        "unshrunk \u03bb=0 fit overfits the sparse fixture pattern (it also "
        "predicts held-out fixture reviews worst in the leave-one-out check "
        "below). Per-judge z-scores throw away level information and must "
        "skip the constant judge in every replication, so they trail "
        "\u03bb=2 in every setting and cannot use the constant judge's "
        "reviews at all. The adaptive rows (20 replications each, so noisier "
        "than the 100-rep rows) match or slightly beat fixed \u03bb=2 on "
        "mean rank correlation and top-5 recall in every setting. Its mean "
        "chosen \u03bb falls as judge bias grows "
        f"({', '.join(f'{s}: {mean_lams[s]:.1f}' for s in SIGMAS)}), so the "
        "rule normalizes gently when judges agree and strongly when they do "
        "not. Its best-first shares trail fixed \u03bb=2 in three of four "
        "settings. These unequal simulation budgets do not establish whether "
        "that difference exceeds Monte Carlo variability; no uncertainty "
        "test for the difference is performed.")
    add("")
    add("### Designing for calibration")
    add("")
    anchor_pairs, anchor_only, anchor_ids, anchor_unfilled, anchor_before = (
        anchor_design(len(design))
    )
    est_fixture = E.estimability(design, seed=CAL_EST_SEED)
    est_anchor = E.estimability(anchor_pairs, seed=CAL_EST_SEED)
    uncovered = sorted(set(projects) - {p for _, p in anchor_pairs})
    add(f"Same review budget ({len(design)} reviews): the fixture's review "
        f"pattern vs an anchor pattern built by the portal assignment "
        f"algorithm (`judging.assign.propose_assignments` with "
        f"`anchors_per_track = {ANCHORS_PER_TRACK}`, target "
        f"{ANCHOR_TARGET}, seed `{ANCHOR_SEED}`). The anchor run proposes "
        f"{anchor_before} reviews across {len(anchor_ids)} anchor projects "
        f"({', '.join(f'`{p}`' for p in anchor_ids)}) with "
        f"{len(anchor_only)} anchor reviews; {anchor_unfilled} project(s) "
        f"come out under target because anchor load counts toward max_load, "
        f"exactly the infeasibility the algorithm reports. Non-anchor "
        f"reviews are then dropped from the most-covered projects until "
        f"the total is back to {len(design)}, so the comparison holds the "
        f"budget fixed. The anchor pattern covers {len({p for _, p in anchor_pairs})} "
        f"of {len(projects)} projects"
        + (f" (uncovered: {', '.join(f'`{p}`' for p in uncovered)})" if uncovered else " (full coverage)")
        + ".")
    add("")
    add("| Design | Reviews | Median SE | Power at 8 pts |")
    add("|---|---:|---:|---:|")
    add(f"| fixture | {len(design)} | {est_fixture.median_se:.2f} | "
        f"{est_fixture.power_at_8:.3f} |")
    add(f"| anchor (1/track) | {len(anchor_pairs)} | {est_anchor.median_se:.2f} | "
        f"{est_anchor.power_at_8:.3f} |")
    add("")
    add("Estimability uses `results.engine.estimability` with the defaults "
        "(additive model, lam = 2.0, noise 15.0, 200 reps, same seed for "
        "both designs): per-judge expected SE of the offset and the mean "
        "share of judges whose injected 8-point bias exceeds 2 SE.")
    add("")
    rec_fixture = rank_recovery(
        design, projects, CAL_RANK_SIGMA_B, CAL_RANK_SEED, CAL_RANK_REPS
    )
    rec_anchor = rank_recovery(
        anchor_pairs, projects, CAL_RANK_SIGMA_B, CAL_RANK_SEED, CAL_RANK_REPS
    )
    add(f"Rank recovery under judge bias \u03c3_b = {CAL_RANK_SIGMA_B} "
        f"({CAL_RANK_REPS} replications, seed {CAL_RANK_SEED}, same "
        f"generative model as above; projects with no reviews rank tied "
        f"last):")
    add("")
    add("| Design | Raw mean \u03c1 | Additive \u03bb=2 \u03c1 |")
    add("|---|---:|---:|")
    add(f"| fixture | {rec_fixture['raw']:.3f} | {rec_fixture['add2']:.3f} |")
    add(f"| anchor (1/track) | {rec_anchor['raw']:.3f} | {rec_anchor['add2']:.3f} |")
    add("")
    if est_anchor.power_at_8 >= est_fixture.power_at_8:
        add(f"Reading: at this budget the anchor pattern detects an 8-point "
            f"bias {est_anchor.power_at_8:.3f} of the time vs "
            f"{est_fixture.power_at_8:.3f} for the fixture pattern, with "
            f"median SE {est_anchor.median_se:.2f} vs "
            f"{est_fixture.median_se:.2f}.")
    else:
        add(f"Reading: at this budget the anchor pattern does not improve "
            f"average detectability ({est_anchor.power_at_8:.3f} vs "
            f"{est_fixture.power_at_8:.3f} for the fixture pattern; median "
            f"SE {est_anchor.median_se:.2f} vs "
            f"{est_fixture.median_se:.2f}, within sampling noise). The "
            f"anchor reviews concentrate on 8 projects while "
            f"{len(uncovered)} project(s) lose coverage"
            + (" entirely" if uncovered else "")
            + ", so rank recovery at \u03c3_b = 0.6 is lower on the anchor "
            f"pattern (additive \u03c1 {rec_anchor['add2']:.3f} vs "
            f"{rec_fixture['add2']:.3f}). Anchors buy shared comparisons "
            f"for the covered projects at the price of thinner coverage "
            f"elsewhere \u2014 under a fixed budget the net effect here is "
            f"nil to negative, which is itself the design-time lesson: "
            f"check the estimability meter before buying anchors.")
    add("")
    add("### Experimental review-budget scenarios: sensitivity to assumed noise")
    add("")
    budget_scored = E.score_reviews(reviews, criteria)
    residual_dispersion = E.pooled_residual_sd(budget_scored, lam=2.0)
    budgets = [E.review_budget_curve(
        BUDGET_JUDGES, BUDGET_PROJECTS,
        reviews_per_judge_grid=BUDGET_GRID, bias=BUDGET_BIAS,
        sigma_noise=multiplier * residual_dispersion, scored=budget_scored,
        reps=BUDGET_REPS, seed=BUDGET_SEED, lam=2.0,
    ) for multiplier in BUDGET_NOISE_MULTIPLIERS]
    budget = budgets[BUDGET_NOISE_MULTIPLIERS.index(1.0)]
    add(f"The fixture's in-sample residual dispersion sqrt(SSE/n) is "
        f"{residual_dispersion:.2f} at fixed \u03bb=2. This is descriptive, "
        "not a calibrated estimate of generating noise: the fitted project "
        "means and judge offsets consume degrees of freedom, and shrinkage "
        "affects residuals. We explicitly assume noise at "
        + ", ".join(f"{m:g}\u00d7" for m in BUDGET_NOISE_MULTIPLIERS)
        + " that dispersion to test sensitivity. These are illustrative "
        "scenarios, not an estimated confidence interval for noise.")
    add("")
    add(f"Balanced random designs with {BUDGET_JUDGES} judges and "
        f"{BUDGET_PROJECTS} projects (same id sets, track-agnostic; "
        f"`results.engine.review_budget_curve` with seed `{BUDGET_SEED}`, "
        f"{BUDGET_REPS} reps, fixed additive \u03bb=2.0). The adaptive "
        "live lambda procedure is not simulated. Noise is independent "
        "homoskedastic Gaussian and scores are not clipped. One positive "
        "judge offset is injected at a time; detection means the fitted "
        "offset exceeds +2 null-simulation SD. The threshold is not calibrated "
        "for multiple judges, and this does not model collusion or varying "
        "track eligibility. The first table shows only the explicit "
        f"1\u00d7 noise assumption (\u03c3={budget.sigma_noise:.2f}):")
    add("")
    add("| Reviews per judge | Median SE | Power at 8 pts | Power at 12 pts |")
    add("|---:|---:|---:|---:|")
    for brow in budget.rows:
        add(f"| {brow.reviews_per_judge} | {brow.median_se:.2f} | "
            f"{brow.power[8.0]:.3f} | {brow.power[12.0]:.3f} |")
    add("")
    add("Assumed-noise sensitivity (same designs and seeds):")
    add("")
    add("| Noise multiplier | Assumed noise SD | Detection at 4 reviews, +8 pts | Detection at 16 reviews, +8 pts | First tested budget at 0.8 share |")
    add("|---:|---:|---:|---:|---|")
    for multiplier, scenario in zip(BUDGET_NOISE_MULTIPLIERS, budgets):
        at4 = next(row for row in scenario.rows if row.reviews_per_judge == 4)
        at16 = next(row for row in scenario.rows if row.reviews_per_judge == 16)
        add(f"| {multiplier:g} | {scenario.sigma_noise:.2f} | "
            f"{at4.power[8.0]:.3f} | {at16.power[8.0]:.3f} | "
            f"{scenario.reviews_needed(0.8)} |")
    add("")
    at16_values = [scenario.rows[-1].power[8.0] for scenario in budgets]
    add(f"At 16 reviews per judge the conditional detection share ranges "
        f"from {min(at16_values):.3f} to {max(at16_values):.3f} across these "
        "assumptions. This range measures scenario sensitivity, not sampling "
        "uncertainty. Monte Carlo error and variation between assignment "
        "designs are not quantified here. The planner remains experimental; "
        "it does not promise an organizer a detection rate or required budget.")
    add("")
    add("### Leave-one-review-out cross-validation (predicting unseen reviews)")
    add("")
    loo = E.leave_one_out(E.score_reviews(reviews, criteria), LOO_LAMS)
    add(f"Each included review was held out once: the model was refit "
        f"without it and the held-out score predicted as `mu_p + b_j`; the "
        f"baseline predicts the mean of the held-out review's project mates. "
        f"{loo.mean_only.n} of {len(reviews)} reviews predicted, "
        f"{loo.skipped} skipped (single-review judges `jdg_12`/`jdg_23`: "
        f"holding out their only review leaves no data to estimate that "
        f"judge's offset). Grids cover the project-mean baseline and "
        f"additive \u03bb \u2208 {{0, 1, 2, 5, 10}}.")
    add("")
    add("| Predictor | RMSE | MAE |")
    add("|---|---:|---:|")
    add(f"| project mean only | {loo.mean_only.rmse:.2f} | {loo.mean_only.mae:.2f} |")
    for lam in LOO_LAMS:
        r = loo.additive[float(lam)]
        add(f"| additive \u03bb={lam} | {r.rmse:.2f} | {r.mae:.2f} |")
    add("")
    best_lam = min(LOO_LAMS, key=lambda lam: loo.additive[float(lam)].rmse)
    r_best = loo.additive[float(best_lam)]
    r_two = loo.additive[LAM_DEFAULT]
    r_zero = loo.additive[0.0]
    add(f"Smallest unseen-review RMSE is \u03bb={best_lam} ({r_best.rmse:.2f}); "
        f"\u03bb={LAM_DEFAULT:g} ({r_two.rmse:.2f}) is "
        f"{r_two.rmse - loo.mean_only.rmse:.2f} points worse than the project "
        f"mean ({loo.mean_only.rmse:.2f}), while \u03bb=0 "
        f"({r_zero.rmse:.2f}) is {r_zero.rmse - loo.mean_only.rmse:.2f} points "
        f"worse than ignoring judges entirely. In plain language: \u03bb=2 "
        f"predicts unseen fixture reviews worse than the project mean; "
        f"strong shrinkage (\u03bb\u2248{best_lam}) is best; the permutation "
        f"test finds no detectable judge effect in this sparse fixture; the "
        f"adaptive rule therefore normalizes gently here and strongly when "
        f"bias is present (simulations). The adaptive 5-fold choice on this "
        f"fixture is \u03bb={auto.value:g} (CV RMSE "
        f"{auto.cv_rmse[auto.value]:.4f} vs baseline {auto.baseline_rmse:.4f}: "
        f"the best of the grid and within 0.01 of the project mean), i.e. "
        f"near-maximal shrinkage, exactly as that reading prescribes.")
    add("")
    add("### Permutation test for judge effects")
    add("")
    perm = E.permutation_test(
        E.score_reviews(reviews, criteria), project_tracks(),
        n_perm=PERM_N, seed=PERM_SEED, lam=PERM_LAM)
    q = perm.quantiles
    add(f"Statistic: population variance of the fitted judge offsets "
        f"(\u03bb={PERM_LAM:g}). Judge labels were "
        f"shuffled {PERM_N:,} times within each track (seed {PERM_SEED}; "
        f"each review keeps its project and score, only the judge label "
        f"moves). Observed variance {perm.observed:.2f}; null quantiles "
        f"5% {q[0.05]:.2f}, 25% {q[0.25]:.2f}, 50% {q[0.5]:.2f}, 75% "
        f"{q[0.75]:.2f}, 95% {q[0.95]:.2f}, 99% {q[0.99]:.2f}; "
        f"p = {perm.p_value:.3f} (fraction of null draws at or above "
        f"observed).")
    add("")
    if perm.p_value < 0.05:
        add("The observed spread is unusual under this within-track "
            "relabeling null. This is evidence against that null, not proof "
            "of a particular cause or judge intent.")
    else:
        add("The test does not reject this relabeling null at the 5% level. "
            "Nondetection is not proof of no bias, non-identifiability, or "
            "non-estimable offsets. This p-value alone does not quantify "
            "the test's power or attribute fitted offsets to sampling noise. "
            "Predictive cross-validation and the explicitly assumed "
            "simulations answer different questions.")
    add("")
    add("### Agreement between the normalized and Bradley\u2013Terry rankings")
    add("")
    agr = E.rank_agreement(base.normalized, base.strengths)
    add(f"Spearman \u03c1 = {agr.rho:.4f}, Kendall \u03c4 = {agr.tau:.4f} "
        f"over the {agr.n} projects ranked by both methods "
        f"(score-level correlation, then rank positions). "
        f"{len(agr.movers)} project(s) differ by more than 5 places:")
    add("")
    if agr.movers:
        for m in agr.movers:
            add(f"- `{m.project_id}` ({titles[m.project_id]}): normalized "
                f"{base.rank_norm[m.project_id]} vs Bradley\u2013Terry "
                f"{base.rank_bt[m.project_id]}")
    else:
        add("None: every project agrees within 5 places.")
    add("")
    add("The Bradley\u2013Terry cross-check uses only within-judge orderings "
        "(derived pairwise comparisons), so judge levels cancel out of it "
        "entirely. Its agreement is a descriptive cross-check on the same "
        "reviews, not independent evidence that the offset model is correct.")
    add("")
    add(f"### Robustness of the fixture result (adaptive lambda = {auto.value:g})")
    add("")
    rb = base.robustness
    assert rb is not None and rb.winner is not None
    gap = base.normalized[rb.winner] - base.normalized[rb.top_k[1]]
    add(f"Winner `{rb.winner}` ({titles[rb.winner]}); top-{rb.k}: " +
        ", ".join(f"`{p}` ({titles[p]})" for p in rb.top_k) + ". "
        f"The winner leads the runner-up by {gap:.2f} normalized points. "
        "Every refit below reuses the already-chosen "
        f"`lambda = {rb.lam:g}` (no lambda re-selection: the certificate is "
        "conditional on that choice, not the complete adaptive procedure) "
        "and warm-starts from the full-data "
        "fit; iteration is in sorted-id order, so the certificate is "
        "deterministic.")
    add("")
    add(f"Leave-one-judge-out ({rb.n_judges} judges with included reviews): "
        f"1st place holds in {rb.judge_holds} of {rb.n_judges} removals; "
        f"the top-{rb.k} set holds in {rb.topk_holds} of {rb.n_judges}. "
        f"{len(rb.judges_flip)} removal(s) change the winner:")
    add("")
    if rb.judges_flip:
        for j in rb.judges_flip:
            new_top = ", ".join(f"`{p}`" for p in rb.judge_topk[j])
            first = ", ".join(f"`{p}` ({titles[p]})" for p in rb.judge_winners[j])
            add(f"- without `{j}` ({names.get(j, '?')}): first-place set "
                f"{first or 'empty'}; "
                f"new top-{rb.k}: {new_top}")
    else:
        add("None: every single-judge removal keeps the winner.")
    add("")
    add(f"Leave-one-review-out ({rb.n_reviews} included reviews): 1st place "
        f"holds in {rb.review_holds} of {rb.n_reviews} removals "
        f"({len(rb.reviews_flip)} flip it):")
    add("")
    if rb.reviews_flip:
        for rid in rb.reviews_flip:
            judge, _, proj = rid.partition("__")
            first = ", ".join(f"`{p}` ({titles[p]})" for p in rb.review_winners[rid])
            add(f"- without `{rid}` ({names.get(judge, '?')} on "
                f"`{proj}` ({titles.get(proj, '?')})): first-place set {first or 'empty'}")
    else:
        add("None: every single-review removal keeps the winner.")
    add("")
    add(rb.flip_summary)
    add(f"Search status `{rb.flip_status}`; {rb.flip_evaluations} subsets "
        f"tested, maximum subset size {rb.flip_cap}, maximum evaluations "
        f"{rb.flip_max_evaluations}. Only above-midpoint reviews of the "
        "unique winner can be lowered; creating a rounded first-place tie "
        "counts as a change. First-place sets use the official two-decimal "
        "tie rule and top-k sets include boundary ties.")
    add("")
    add(f"In plain language: {rb.summary}")
    add("")
    if rb.flip_margin == 1:
        frag = ("moving one of its above-midpoint reviews to the midpoint "
                "is enough")
    elif rb.flip_margin is None:
        frag = "the tested midpoint subsets do not establish a flip count"
    else:
        frag = (f"moving {rb.flip_margin} of its reviews to the midpoint "
                "is enough")
    add(f"Reading: with a {gap:.2f}-point lead the fixture winner is fragile "
        f"\u2014 {len(rb.judges_flip)} judges and {len(rb.reviews_flip)} "
        f"single reviews can each flip it, and {frag}. That is the honest "
        "consequence of a near-tie at the top, not a flaw in the fit: the "
        "certificate reuses the published lambda and shows exactly where "
        "the result could break.")
    add("")
    add("## Properties")
    add("")
    add("Shift invariance: adding +1 to every criterion value of one judge "
        f"(`{CONSTANT_JUDGE}`, chosen because 4+1 needs no clipping) moves the "
        "raw ranking but leaves the additive (\u03bb=0) ranking exactly "
        "unchanged \u2014 the shift is absorbed by that judge's offset:")
    shifted = []
    for r in reviews:
        if r.judge_id == CONSTANT_JUDGE:
            shifted.append(E.ReviewInput(
                r.review_id, r.judge_id, r.project_id,
                {k: v + 1 for k, v in r.values.items()}))
        else:
            shifted.append(r)
    plain = E.evaluate(reviews, criteria, lam=0.0, target=TARGET)
    moved_fit = E.evaluate(shifted, criteria, lam=0.0, target=TARGET)
    raw_moves = sum(1 for p in projects
                    if E.rank({q: plain.raw[q][0] for q in projects})[p]
                    != E.rank({q: moved_fit.raw[q][0] for q in projects})[p])
    add(f"- raw ranking positions changed: {raw_moves} of {len(projects)};")
    add(f"- additive (\u03bb=0) rankings identical: "
        f"{moved_fit.rank_norm == plain.rank_norm}.")
    add("")
    add("Constant-judge case: a judge with zero variance breaks per-judge "
        "z-scores (division by zero) and their scores depend on which projects "
        "they happened to receive; the additive model instead absorbs their "
        f"level into the offset ({base.judges[CONSTANT_JUDGE].offset:+.2f}) and "
        "their reviews contribute no ordering information. That is why the "
        "portal uses offsets, not z-scores (see JUDGING.md).")
    add("")
    add("## Method lineage")
    add("")
    add("The implemented additive fit is penalized least squares with "
        "project levels and judge offsets: the Platt-Burges objective NIPS "
        "(now NeurIPS) minimised to calibrate reviewer scores from 2006 to "
        "2012 (score = quality + reviewer bias + noise, ridge penalty on the "
        "biases), as described by Ge, Welling and Ghahramani, 'A Bayesian "
        "Model for Calibrating Reviewer Scores'. A Gaussian random-offset model "
        "gives lambda a residual-to-judge variance-ratio interpretation, "
        "but this implementation selects lambda by cross-validation, not "
        "variance estimation. It is not a Rasch model. The Bradley-Terry "
        "cross-check uses numerical MM updates with a virtual opponent, "
        "subject to convergence tolerance and an iteration cap. Neither "
        "model establishes robustness to strategic or correlated judging.")
    add("")
    add("## Limitations")
    add("")
    add("- Offset-only model: linear level habits are removed, but scale "
        "habits (harsh on weak projects, generous on strong ones) and "
        "nonlinear mappings are not modelled.")
    add("- Few reviews per judge: single-review offsets are almost pure "
        "shrinkage toward zero; their projects are ranked mostly by raw means.")
    add("- Clipping at the scale ends (1 and 5) destroys level information a "
        "shift would otherwise preserve.")
    add("- Correlated or strategic bias (vote-trading, team-targeted "
        "collusion) is not addressed by normalization; see the outlier list "
        "and THREAT-MODEL.md.")
    add("")
    add("## Reproducibility")
    add("")
    add("Regenerate with `.venv\\Scripts\\python.exe "
        "scripts/normalization_proof.py` (standard library only; reads "
        "`fixtures.json`, imports `src/results/engine.py`). Simulation seeds: "
        + ", ".join(f"\u03c3_b={s} \u2192 {SEEDS[s]}" for s in SIGMAS) + ". "
        f"Adaptive rule: grid \u03bb \u2208 "
        f"{{{', '.join(f'{g:g}' for g in E.DEFAULT_LAMBDA_GRID)}}}, 5 folds, "
        f"seed `verdict`; adaptive simulation rows use {ADAPTIVE_REPS} "
        f"replications per \u03c3_b (other rows {REPS}). "
        f"Leave-one-out grid: \u03bb \u2208 {{{', '.join(str(l) for l in LOO_LAMS)}}}. "
        f"Permutation test: {PERM_N:,} within-track shuffles, seed {PERM_SEED}, "
        f"\u03bb={PERM_LAM:g}. Calibration section: anchor pattern "
        f"(anchors_per_track={ANCHORS_PER_TRACK}, target={ANCHOR_TARGET}, "
        f"seed `{ANCHOR_SEED}`, trimmed to {len(reviews)} reviews), "
        f"estimability seed `{CAL_EST_SEED}` (200 reps), rank recovery "
        f"\u03c3_b={CAL_RANK_SIGMA_B} seed {CAL_RANK_SEED} "
        f"({CAL_RANK_REPS} reps). Budget planner: "
        f"{BUDGET_JUDGES} judges / {BUDGET_PROJECTS} projects, grid "
        f"{{{', '.join(str(k) for k in BUDGET_GRID)}}}, bias {BUDGET_BIAS:g}, "
        f"noise explicitly assumed at {BUDGET_NOISE_MULTIPLIERS} times "
        f"the in-sample residual dispersion, {BUDGET_REPS} reps, seed "
        f"`{BUDGET_SEED}`. No timestamps are written, so regenerating "
        f"twice gives identical bytes.")
    add("")
    text = "\n".join(lines)
    OUT_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT_PATH} ({len(text)} chars)")


if __name__ == "__main__":
    main()
