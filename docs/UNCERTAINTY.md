# Rank uncertainty ("statistically tied" labels)

With about three reviews per project, neighbouring ranks are often
not distinguishable. `rank_uncertainty` (in `src/results/engine.py`)
quantifies, per project, how firm its rank is: "rank 2-6 in 90% of
re-runs; in the top 3 in 64%" (illustrative, not fixture numbers). These are
approximate Monte Carlo model summaries, reproducible with a fixed seed.
A later packet wires them into the pages; the engine
itself changes no ranking.

## Method

1. Fit the additive model once at the official lambda (never
   re-selected) and estimate review noise as `sigma =
   sqrt(SSE / (reviews - projects))` from the residuals.
2. Draw 200 synthetic review sets: fitted value plus independent
   normal noise with SD sigma (seeded, in review-id order), and refit
   each one warm-started from the official fit.
3. Rank the projects in every re-run (competition ranks). Each
   project's rank interval is the central 90% of its 200 replicate
   ranks (sorted values at indices 10 and 189); the score interval is
   the same rule on replicate scores. `p_first` / `p_top` are the
   shares of re-runs ranked 1st / in the top 3.
4. `p_above_next` is the share of re-runs a project beats its
   official-order neighbour (ties count half).

## What "statistically tied" means

An adjacent pair is tied when its official order holds in fewer than
95% of re-runs. This is a descriptive stability cutoff, inspired by a
one-sided 5% tail; it is not an exact equivalence to a finite-sample 90%
interval containing zero (discrete draws, ties and shrinkage matter).
It does not establish equal project quality or a calibrated hypothesis test. Tied pairs join
into groups: maximal runs of consecutive projects (singletons
allowed), so every project sits in exactly one group. Chaining adjacent pairs
does not imply every pair in a group is indistinguishable.

## Assumptions and limits

Parametric re-runs of the additive model: each review is project
level + judge offset + independent normal noise, with the noise SD
set to `sqrt(SSE / (reviews - projects))`. This divisor subtracts project
means only, not the effective fitted judge-offset parameters: it is a
heuristic, not an unbiased noise estimator or a coverage guarantee. Lambda,
review assignments and the fitted model are held fixed; uncertainty in CV
selection is omitted. Each refit applies shrinkage again. Rubric
bounds, rounding and correlated judging are not modelled, so
intervals are approximate and describe the model, not the truth.
Replicate ranks compare unrounded scores, whereas official ties use two
decimal places. Exact ties count half for adjacent order shares; several
projects may simultaneously count as first, so first-place shares need not
sum to one. Disconnected designs do not support data-only cross-component
comparisons; a simulated overall order inherits the shrinkage assumptions.

## How it differs from the robustness certificate

Robustness (`robustness`) removes real judges/reviews and asks what
breaks: sensitivity to the data we have. Uncertainty re-simulates
noisy reviews and asks how firm each rank is: sensitivity to the
noise we estimate. A winner can be robust (no removal flips it) yet
uncertain (neighbouring ranks overlap), or the reverse.

## Fixture numbers (measured)

On `fixtures.json` (121 reviews over 40 projects, lambda 100, 200
re-runs): noise SD sigma = 15.43 (df 81); winner prj_34 first in 22%
of re-runs with rank interval 1-16; 1st vs 2nd is statistically tied
(order held 46%); all 39 adjacent pairs tied, i.e. one group of 40.
Reproduce with `.venv/Scripts/python.exe scripts/uncertainty_evidence.py`.
The exact adjacent order share is 0.465; the summary rounds it to 46%.
The command prints the seed (20260929), inputs, output and elapsed time;
this audit measured 0.227 s on Windows/Python 3.11.9. Runtime is host-dependent.
