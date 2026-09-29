# Judging

How VERDICT assigns reviews, scores them, normalizes judge habits, checks its
own fragility, and publishes. Every fixture number below is output of
`.venv\Scripts\python.exe scripts/normalization_proof.py` (stdlib only; reads
`fixtures.json`, imports `src/results/engine.py`) and is tabulated in
`docs/NORMALIZATION-PROOF.md`. Engine unit coverage is `tests/test_engine.py`;
robustness contracts are `tests/test_robustness_contract.py`.

## Assignment

`propose_assignments(judges, projects, target, max_load, seed, anchors_per_track)`
in `src/judging/assign.py` fills each project's review target (fixture: 3)
most-constrained-first: the project with the fewest eligible judges is served
first, ties broken by load, overlap, then seeded randomness. Eligibility is
track membership plus declared conflicts (the `Conflict` model in
`src/judging/models.py`, unique per judge/team). The algorithm reports unfilled slots with reasons
instead of inventing judges: on the fixture pattern, an anchor run with
`anchors_per_track=1` leaves 4 projects under target because anchor load
counts toward `max_load`, and 2 projects lose coverage entirely when the
budget is held fixed. "Unfilled by this planner" is the honest output; it is
not a proof that no feasible assignment exists. Pacing and rebalance math
lives in `src/judging/forecast.py`; the first submitted review locks the
event's scoring settings (`scoring_locked_at`), further rubric edits return
409. Each judge's queue follows a per-judge deterministic order — sha256 of the
event, judge-role and project public ids, to-do before submitted — so the same
projects are not judged first or last by everyone. This spreads
serial-position effects across projects instead of piling them onto the same
teams; the scoring model does not correct order effects.

## Rubric and review score

The fixture rubric has three criteria (functionality, quality, innovation),
each 1–5, equal weight. `review_score` in `src/results/engine.py` rescales to
0–100:

```text
score = 100 * sum(w * (v - min) / (max - min)) / sum(w)
```

Weights are positive and `max > min` by DB constraint
(`criterion_weight_positive`, `criterion_max_above_min`), so the divisor is
never zero. The raw project score is the mean of its review scores. A missing
review is never a zero: projects with no reviews are unranked, and
publication is blocked until every ranked project meets the target or an
organizer acknowledges the shortfall (`publish` in `src/results/services.py`).

## Normalization

Each review score is fitted to the additive model `score = project quality +
judge offset` by block coordinate descent minimizing
`sum (s - mu - b)^2 + lambda * sum b^2` (canonical order: judge, project,
review id; tolerance 1e-10, cap 10,000 iterations; `lam == 0` recentres
`sum n_j b_j = 0`). The normalized project score is `mu`. Ranks are
competition ranks sharing ties at 2 dp.

Lambda is not hand-picked. `select_lambda` runs seeded 5-fold CV over the
grid `(0.5, 1, 2, 5, 10, 20, 50, 100)`: reviews are put in canonical order
(judge, project, review id), shuffled with seed `verdict`, split into fifths,
each held-out score predicted as `mu_p + b_j` (119 predicted, 2 skipped where
a single-review judge has no training data), smallest RMSE wins, ties go to
the larger lambda. On the fixture (121 included reviews over 40 projects after
excluding superseded `prj_07` and its 5 reviews) the procedure selects
**lambda = 100**, CV RMSE 19.52 against a project-mean baseline of 19.52 —
i.e. near-maximal shrinkage, normalizing gently because the data does not
support strong judge effects.

Fixture effect, same command: 19 of 40 projects move rank raw → normalized
(largest: `prj_28` 24 → 28); the top changes from tied `prj_34`/`prj_11` at
83.33 raw to `prj_34` 83.35 above `prj_11` 83.26 normalized.

The implemented fit is penalized least squares with additive judge offsets: the
Platt–Burges objective NIPS (now NeurIPS) minimised to calibrate reviewer scores
from 2006 to 2012 (`score = quality + reviewer bias + noise`, with a ridge
penalty on the biases), as described by Ge, Welling and Ghahramani ("A Bayesian
Model for Calibrating Reviewer Scores"), who moved NIPS 2013–2014 to a Bayesian
variant. It has a Gaussian random-offset interpretation when lambda is the
residual-to-judge variance ratio, but VERDICT chooses lambda by predeclared
cross-validation, not by variance estimation. It is not a Rasch model.
Linear-bias models break under strategic or correlated miscalibration (Wang and
Shah, 2019) — hence the outlier list and the stated offset-only limit, not a
wider claim. The Bradley–Terry cross-check uses iterative MM updates (Hunter,
2004) with a virtual opponent; it is a regularized numerical fit subject to a
convergence tolerance and an iteration cap, not an exact symbolic solution.

## The organizers' sigma 0.42 chain

The competition homepage showed judge spread σ = 0.42, defined (per organizer
`souvlakee`, Discord 2026-09-27) as the sample SD of per-judge mean scores on
the 1–5 scale over all of `fixtures.json`. `scripts/normalization_proof.py`
prints the full chain so each step is auditable:

```text
0.4198  all 126 reviews, 30 judges (matches the homepage 0.42)
0.3292  after the duplicate policy excludes prj_07 (121 reviews, 29 judges)
0.3201  after offset removal at the CV-chosen lambda = 100
        (lambda = 0: 0.6145; lambda = 2: 0.1908, for context)
```

The first drop follows removal of all five reviews of the superseded project,
including the only included review from `jdg_01` (all 2s). The second removes
fitted judge levels at the stated shrinkage. A smaller
spread after removal is **not** evidence the ranking improved: lambda = 2
shrinks spread the most yet predicts unseen reviews worse than plain project
means (LOO RMSE 19.86 vs 19.28; best is lambda = 10 at 19.17). Improvement is
checked by held-out prediction, the permutation test, and simulations with
known truth — not by the spread.

## The judge who marks everything the same

`jdg_07` (Iva Petrova) gave 3 reviews with every criterion a 4 (score 75.00).
Per-judge z-scores break on this input (division by zero) and would have to
discard the reviews; the additive model absorbs the level into the offset
(+0.30, labelled typical) and the reviews contribute no ordering information.
That is why the portal uses offsets, not z-scores
(`docs/NORMALIZATION-PROOF.md`, Properties; shift-invariance check: adding +1
to one judge's criteria moves 17 of 40 raw ranks and leaves the additive
ranking exactly unchanged). The reviews are kept by default and shown with
this explanation; excluding `jdg_07` moves 18 projects and is an organizer
decision with a recorded exclusion reason, never automatic. After publication,
a correction needs a new publication version to change official results.

## Permutation test: what it does and does not show

Statistic: population variance of fitted offsets at lambda = 2. Judge labels
are shuffled 2,000 times within each track (seed 97531); each review keeps
its project and score. Observed variance 24.40 sits inside the null
(quantiles 5% 14.73, 50% 22.69, 95% 32.12), p = 0.381. The test does not
reject the relabeling null at 5%.

Non-detection is not proof of no bias, non-identifiability, or non-estimable
offsets. The p-value alone quantifies neither power nor the source of the
fitted spread. Predictive cross-validation (above) and the simulations below
answer the adjacent questions: can the model predict unseen reviews, and does
it recover known truth when bias is injected.

Simulations (same command, fixture review pattern, 100 replications per
setting, adaptive rows 20): with fair judges additive lambda = 2 matches raw
means (mean ρ 0.959 vs 0.958), so shrinkage is cheap insurance; with biased
judges (σ_b 0.3/0.6/1.0) lambda = 2 beats raw on rank correlation and top-5
recall every time, while unshrunk lambda = 0 overfits and z-scores trail in
every setting (they skip the constant judge in 100% of replications). The
adaptive rule's mean chosen lambda falls as bias grows (44.9 → 1.3 → 0.6 →
0.5): gentle when judges agree, strong when they do not.

## Robustness analysis

`robustness()` in `src/results/engine.py` runs three refit batteries at the
already-published lambda (fixed, no re-selection — the certificate is
conditional on that choice) and the results page shows them. Fixture winner
`prj_34` (Iron Switch) leads by 0.09 normalized points:

- Leave-one-judge-out: refit once per judge excluding all their reviews.
  1st place holds in 24 of 29 removals; the top-3 set holds in 22 of 29.
  Five removals flip the winner (without `jdg_04` → `prj_37`; `jdg_15` or
  `jdg_25` → `prj_11`; `jdg_24` → `prj_18`; `jdg_29` → `prj_10`).
- Leave-one-review-out: one refit per review id. Holds in 115 of 121; six
  single reviews each flip it (listed in the proof, e.g. `jdg_15__prj_34`).
- Midpoint flip: only for a unique winner; subsets of the winner's
  above-midpoint reviews (score 50 = every criterion at midpoint) are lowered
  to 50 in increasing size order, ties counting as change, until the first
  size that flips or caps hit (max subset size 5, max 256 refits). Here 1
  review (`jdg_15__prj_34`) suffices — the exact minimum for this named
  perturbation.

Limits: statuses `size_capped` / `search_capped` mean the search gave up, not
that the result is stable; pairwise methods report `available: false`. With a
0.09-point lead the fixture winner is fragile, and the certificate says so —
that is the consequence of a near-tie, not a flaw in the fit.

## Readiness and budget planner

`estimability()` and `review_budget_curve()` (same engine file) answer the
planning question: given a review budget, how precisely could judge offsets
be estimated. Assumptions, stated on every display: balanced track-agnostic
design, fixed lambda = 2.0, independent homoskedastic Gaussian noise that is
**assumed** at 0.75×/1×/1.5×/2× the in-sample residual dispersion (10.92),
one injected positive offset at a time, detection = fitted offset above +2
null SD, uncalibrated for multiple judges. The 1× scenario needs 16 reviews
per judge for 0.8 detection share at +8 points; at 2× noise the same budget
detects 0.286. The range across assumptions measures scenario sensitivity,
not sampling error, and the adaptive live-lambda procedure is not simulated.
The planner is organizer-only, opt-in, bounded off page refresh, and never a
judge accusation. Calibration finding from the same command: at a fixed 121
reviews the anchor pattern (1/track) does not improve detectability (power
0.127 vs 0.134) and recovers ranks worse (ρ 0.824 vs 0.930) — check the meter
before buying anchors.

## Pairwise mode

Choose Pairwise or Both in event settings, assign at least two eligible
projects to each judge, and open judging. The judge console links to
`/judge/{slug}/pairwise`. Both cards include the assigned projects' private
evidence. Judges can only compare their own submitted, in-track assignments
without declared conflicts.

Each judge compares a pair once. Selection prioritizes projects with the
fewest event-wide comparisons; ties use a deterministic hash of the event,
judge, completed-pair count, and candidate pair. A judge finishes when every
assigned project has reached the configured comparison target (default
three), or all distinct pairs are exhausted. Three comparisons per project
per judge is a coverage guideline, not a precision guarantee. "Too close" is
an abstention: it counts as a seen pair and creates no win, half-win, or
ranking edge.

The latest choice can be retracted for 30 seconds, strictly before the
boundary and while judging remains open. Retraction preserves the original
record and creates an audit entry; the pair becomes available again. Event
row locks and a database constraint prevent duplicate active verdicts,
including reversed pairs. The first comparison locks the event's scoring
settings.

Live outcomes use the existing Bradley–Terry MM estimator: each project has
a positive strength, and the fitted log strengths determine the order. A
virtual opponent contributes one win and one loss for each observed project
so unbeaten projects remain finite. Log strengths are not percentages or
win-confidence estimates. Equal strengths after rounding to two decimals
share a rank; titles only control display order within a shared rank.

Results distinguish live comparisons from the separate rubric-derived
pairwise cross-check. Selecting Pairwise as the official ranking uses only
live outcomes, including an explicitly empty set; it never substitutes
derived rubric outcomes. Projects without decisive comparisons remain
unranked. Disconnected observed comparison groups are shown separately and
cannot support an overall official order: pairwise publication is blocked
until additional comparisons connect them. The virtual opponent stabilizes
the estimator; it does not supply missing evidence between disconnected
groups.

Publications snapshot the exact active comparisons, including abstentions,
and verification recomputes that snapshot and checks its digest against live
data. Organizer export `pairwise.csv` includes original and retracted
judgments. Pairwise choices sidestep differences in numeric scoring
baselines, but they do not remove differences in taste, strategic judgments,
intransitive preferences, or sparse coverage. No winner probability or
calibrated confidence is claimed.

A separate bounded completion analysis exists in `src/results/closure.py`
(`tests/test_closure_engine.py`): given a
declared pending roster it searches deterministic completions for a
first-place change and returns only `counterexample_found`, `unknown`, or
`unsupported` — never a stability certificate. Caps: 100 projects, 500
reviews, 24 pending slots, 16 criteria. Labelled one-slot example from the
same proof command: adding `jdg_20 → prj_10` at all 5s (an eligible,
not-yet-scored pair, not an established assignment) moves Still Beacon
78.95 → 86.13 and the lead. The scenario replays through the real engine; it
does not show any real judge owes that review.

## Consequence preview

Before publishing, disqualifying a project, or excluding/re-including a review,
organizers see the changed ranks, awards, and winner in a read-only preview.
Publish compares the latest official rows and awards with the current preview.
Disqualification and review actions compare the current preview with the
hypothetical result after that one action.
The preview uses the same result engine, eligible-project set, review inputs,
and award allocator as normal preview and publication; it is exact, not an
estimate.
Every response includes the live input digest used as its basis.
When submitted with the write, the digest is checked again while the event is
locked; a mismatch returns `stale_preview` without changing any data.
The organizer must review refreshed consequences and confirm again.

## Rank uncertainty

Normalized rankings include seeded parametric re-runs that estimate rank ranges,
first-place/top-three shares and adjacent pairs whose order is not firm.
The public results page shows rank intervals and marks statistically tied
projects; the organizer preview also shows the model assumptions and limits.
This is sensitivity to estimated review noise, not the removal-based
robustness certificate above.
Uncertainty is derived from the exact scoring inputs and is **not stored** in
the publication snapshot or digest. See [docs/UNCERTAINTY.md](docs/UNCERTAINTY.md)
for the method, definition of a tie, assumptions and fixture measurements.

## Publication and verification

`publish` (`src/results/services.py`) locks the event, requires judging and
voting closed, and refuses with 409 on open windows, unranked projects
(unless acknowledged), missing rubric, or disconnected pairwise groups. It
stores method, params (including chosen lambda), canonical inputs, rows,
awards and digests. `verify_publication` recomputes the engine and prize
allocation from stored inputs and the stored public project snapshot. It
checks rows, awards, the stored digest and policy metadata, independently
of whether current inputs still match that digest — verdict `identical` or `differs` with an
explicit reason. Malformed stored inputs fail explicitly instead of raising
(`tests/test_adversarial_t2.py`). Verify from a shell with
`python manage.py verify_publication <pub_id>` or
`python scripts/verify_record.py record.json verdict-keys.json` for signed
judge records. Prizes (`allocate` in `src/results/prizes.py`) go only to ranked
rows in position order, skip on 2-dp ties, and record unawarded reasons.

## Limitations

- Offset-only: scale habits, clipping at 1/5, and single-review offsets
  (near-pure shrinkage) are not corrected; few reviews per judge means mostly
  raw means.
- Correlated or strategic bias (vote-trading, team-targeted collusion) is not
  addressed by normalization; outliers (fixture: `jdg_04` on `prj_37`, 35.3
  below consensus) are detection-only signals, never auto-exclusions.
- Derived Bradley–Terry agreement (fixture ρ = 0.8509, τ = 0.6684, 15
  projects differ by >5) is a cross-check on the same reviews, not
  independent evidence the offset model is right.
- Historical replay uses the stored roster; the separate unchanged check
  reads live data. Rewriting data and digest together (DB admin) is outside
  what the digest detects. Migration-backfilled snapshots are labelled as such.
