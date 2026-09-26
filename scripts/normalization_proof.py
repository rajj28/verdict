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

FIXTURES_PATH = REPO / "fixtures.json"
OUT_PATH = REPO / "docs" / "NORMALIZATION-PROOF.md"

LAM_DEFAULT = 2.0
LAM_GRID = (0, 1, 2, 5)
TARGET = 3  # default event.reviews_per_project (BUILD-SPEC 4)
SUPERSEDED = "prj_07"
SUPERSEDED_BY = "prj_41"
CRITERIA_KEYS = ("functionality", "quality", "innovation")
CONSTANT_JUDGE = "jdg_07"  # the fixture's all-4s scorer

REPS = 500
SIGMAS = (0.3, 0.6, 1.0)
SEEDS = {0.3: 1101, 0.6: 2202, 1.0: 3303}


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


# ------------------------------------------------------------------ stats

def _ranks(xs):
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


def spearman(a, b):
    """Spearman rho via Pearson on average ranks (ties handled)."""
    ra, rb = _ranks(list(a)), _ranks(list(b))
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra)
    vb = sum((x - mb) ** 2 for x in rb)
    if va <= 0 or vb <= 0:
        return float("nan")
    return cov / math.sqrt(va * vb)


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
    """One sigma setting: mean rho, top-5 recall, best-first rate per method."""
    rng = random.Random(seed)
    judges = sorted({j for j, _ in design})
    acc = {
        m: {"rho": 0.0, "top5": 0.0, "best": 0.0}
        for m in ("raw", "zscore", "add0", "add2", "bt")
    }
    z_skips = 0
    for _ in range(REPS):
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
    out = {
        m: (v["rho"] / REPS, v["top5"] / REPS, v["best"] / REPS)
        for m, v in acc.items()
    }
    return out, z_skips / REPS


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
    base = fits[LAM_DEFAULT]
    bt_order = order_projects(base.strengths, projects)

    lines = []
    add = lines.append
    add("# Normalization proof")
    add("")
    add("Method in one paragraph: every review is reduced to a 0-100 score by "
        "equal-weighted rescaling of the three 1-5 criteria "
        "(functionality, quality, innovation), then fitted to the additive "
        "model `score = project quality + judge offset` by block coordinate "
        "descent minimizing `sum (s - mu - b)^2 + lambda * sum b^2` with "
        f"`lambda = {LAM_DEFAULT}` (BUILD-SPEC 9). The normalized project score "
        "is `mu`; ranks below are competition ranks with ties shared at 2 dp. "
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
    add("### Judge offsets (lambda = 2)")
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
    add("Sigma of per-judge mean scores drops from "
        f"{f2(base.spread_before)} raw to {f2(base.spread_after)} after offset "
        "removal: most of the disagreement between judges' average marks is "
        "level, not ordering.")
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
        criteria, lam=LAM_DEFAULT, target=TARGET, projects=projects,
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
        "judge offsets ~ N(0, \u03c3_b) with \u03c3_b in {0.3, 0.6, 1.0} "
        "score points on the 1\u20135 scale, noise ~ N(0, 0.5) per "
        "criterion, rounded and clipped to integer 1\u20135; one constant "
        f"judge (`{CONSTANT_JUDGE}`, all 4s) like the fixture. "
        f"{REPS} replications per \u03c3_b. Methods: raw mean, per-judge "
        "z-score (zero-variance judges skipped), additive \u03bb=0, additive "
        "\u03bb=2, derived Bradley\u2013Terry. Reported: mean Spearman "
        "\u03c1 with truth, top-5 recall, share of replications where the "
        "true best project ranks first.")
    add("")
    for sigma in SIGMAS:
        res, skip_rate = simulate(sigma, SEEDS[sigma], design, projects)
        add(f"### \u03c3_b = {sigma} (seed {SEEDS[sigma]})")
        add("")
        add("| Method | Mean \u03c1 | Top-5 recall | Best ranked first |")
        add("|---|---:|---:|---:|")
        for m in ("raw", "zscore", "add0", "add2", "bt"):
            rho, top5r, best = res[m]
            add(f"| {m} | {rho:.3f} | {top5r:.3f} | {best:.3f} |")
        add("")
        add(f"Zero-variance judges skipped in {skip_rate:.1%} of replications "
            "(the forced constant judge is skipped every replication; natural "
            "zero-variance judges are rare).")
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
        + ", ".join(f"\u03c3_b={s} \u2192 {SEEDS[s]}" for s in SIGMAS) + ".")
    add("")
    text = "\n".join(lines)
    OUT_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT_PATH} ({len(text)} chars)")


if __name__ == "__main__":
    main()
