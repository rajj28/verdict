# F2A: Per-project rank uncertainty and "statistically tied" labels (engine only)

Read `AGENTS.md` first. This packet is pure Python in `src/results/engine.py`
(standard library only, no Django, no numpy). A later packet wires it into the
pages; do not touch services, views, templates or the API.

## Why

With about three reviews per project, neighbouring ranks are often not
distinguishable. Organizers should see, per project, how firm its rank is
("rank 2-6 in 90% of re-runs; in the top 3 in 64%") and whether first vs second
is clear or statistically tied, before they hand out prizes. This must be exact,
deterministic and honestly labelled.

## Method (implement exactly this)

`rank_uncertainty(scored, lam, *, replicates=200, seed=20260929, level=0.90, top_k=3, tie_threshold=0.95) -> Uncertainty`

Inputs: `scored` = the `ScoredReview` list the official fit uses (after rubric
weighting); `lam` = the official lambda (reuse it, never re-select).

1. `P` = number of distinct projects, `n` = number of reviews. If `P < 2` or
   `n <= P`: return `available=False` with a plain `reason`
   (e.g. "Too few reviews to estimate noise: 40 reviews for 41 projects.").
2. `fit = fit_additive(scored, lam)`. Fitted value per review
   `f_r = fit.mu[p] + fit.offset[j]`. `SSE = sum (s_r - f_r)^2`,
   `df = n - P`, `sigma = sqrt(SSE / df)`. (Degrees-of-freedom corrected; the
   planner's `pooled_residual_sd` divides by n and is not used here.)
3. `rng = random.Random(seed)`. For each replicate `b` in `range(replicates)`:
   iterate the reviews sorted by `review_id`; synthetic score
   `y = f_r + rng.gauss(0.0, sigma)`; build `ScoredReview` copies with `y`;
   refit `fit_additive(copies, lam, init=fit)` (warm start). Replicate rank of
   project `p` = 1 + number of projects whose replicate `mu` is strictly greater
   (competition ranking).
4. Official order: projects sorted by `(-round(fit.mu[p], 2), p)` (the same key
   `_rank_sets` uses).
5. Per project (`ProjectUncertainty`):
   - `rank_low`, `rank_high`: nearest-rank interval of its replicate ranks at
     `level`: sort the `B` values ascending, take index
     `floor(((1 - level) / 2) * B)` and index `ceil(((1 + level) / 2) * B) - 1`
     (B=200, level=0.9 -> indices 10 and 189). Put this in a small helper
     `_nearest_rank_interval(values, level)` and test it directly.
   - `score_low`, `score_high`: same rule on its replicate `mu` values.
   - `p_first`: share of replicates with rank 1. `p_top`: share with rank <= `top_k`.
   - `p_above_next`: for the next project `q` in the official order, share of
     replicates with `mu_p > mu_q` plus half the share with `mu_p == mu_q`;
     `None` for the last project.
6. `tied_pairs`: adjacent official-order pairs with `p_above_next < tie_threshold`.
   0.95 is chosen because "the order held in fewer than 95% of re-runs" is the
   one-sided form of "the two-sided 90% interval of the difference includes
   zero"; say so in the docstring.
7. `groups`: every project in exactly one group; a group is a maximal run of
   consecutive official-order projects joined by tied pairs (singletons allowed).
8. `summary`: one computed sentence about first place, e.g.
   "1st place is clear: it stayed ahead of 2nd in 97% of 200 re-runs." or
   "1st and 2nd are statistically tied: the order held in 71% of 200 re-runs."
   (use the actual numbers; handle 1 project, and ties at the top).
9. `assumption` (fixed text, adjust wording only for clarity): "Parametric
   re-runs of the additive model: each review is project level + judge offset +
   independent normal noise, with the noise SD estimated from the residuals
   (SSE / (reviews - projects)). Rubric bounds, rounding and correlated judging
   are not modelled, so intervals are approximate and describe the model, not
   the truth."

Dataclasses (frozen): `ProjectUncertainty(project_id, rank_low, rank_high,
score_low, score_high, p_first, p_top, p_above_next)` and `Uncertainty(available,
replicates, seed, level, top_k, tie_threshold, sigma, df, projects: dict,
order: tuple, tied_pairs: tuple, groups: tuple, summary, assumption, reason="")`.
Round nothing inside the engine except where the official order already does.

Do not call it from `evaluate()`; the integration packet decides where it runs
(it costs about one refit per replicate).

## Determinism and speed

- Same inputs -> identical dataclass (compare with `==`), independent of dict
  insertion order and `PYTHONHASHSEED` (sort ids everywhere).
- On the organizers' fixture (`fixtures.json`, scored the way `tests/test_engine.py`
  already scores it, at lambda 100), 200 replicates must finish in under 3
  seconds on a laptop. Report the measured time in your summary.

## Tests: add to `tests/test_engine.py` (pure unittest, no Django)

It runs as `python -m unittest tests.test_engine`. Add a
`RankUncertaintyTests` class covering:
1. Determinism: two calls equal; a different seed changes at least one `p_*`.
2. Well separated projects (three projects with true levels 2, 5, 8, four
   judges each, tiny noise): intervals are exactly [1,1], [2,2], [3,3]; every
   `p_above_next` is 1.0; no tied pairs; summary says clear.
3. Two projects with identical data plus a third far below: the pair's
   `p_above_next` is between 0.3 and 0.7 and the pair is tied.
4. A perfectly additive dataset (sigma == 0): degenerate intervals, `p_above_next`
   is 1.0 for strict orders and 0.5 for exact equals.
5. `n <= P` -> `available=False` with a reason.
6. `_nearest_rank_interval` indices for B=200/level 0.9 and a tiny case.
7. Fixture: available; groups partition the projects; every `p_*` in [0, 1];
   `rank_low <= rank_high`; the official rank position lies inside its interval
   for at least 90% of projects; runtime under 10 seconds (generous CI bound).

Also write `docs/UNCERTAINTY.md` (30-60 lines): the method above in plain
words, what "statistically tied" means, the assumptions and limits, how it
differs from the robustness certificate (judge/review removal) and the fixture
numbers you measured (winner's `p_first`, whether 1st vs 2nd is tied, how many
tied adjacent pairs, sigma, runtime). Numbers must come from running the code.

## Scope

Touch only `src/results/engine.py` (add; do not change existing behaviour),
`tests/test_engine.py` (add a class; do not edit existing tests),
`docs/UNCERTAINTY.md` (new). Other workers are editing `src/results/services.py`,
`src/results/consequences.py`, templates, `src/core/**` and `JUDGING.md`: do not
touch them. Never git commit.

## Done means

`.venv\Scripts\python.exe -m unittest tests.test_engine` passes (all old tests
too). Finish with SUMMARY / FILES / TESTS / GAPS as `AGENTS.md` says.
