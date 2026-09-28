# Rank uncertainty ("statistically tied" labels)

With about three reviews per project, neighbouring ranks are often
not distinguishable. `rank_uncertainty` (in `src/results/engine.py`)
quantifies, per project, how firm its rank is: "rank 2-6 in 90% of
re-runs; in the top 3 in 64%". It is exact, deterministic, and
honestly labelled. A later packet wires it into the pages; the engine
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
95% of re-runs. The 95% cut is the one-sided form of "the two-sided
90% interval of the score difference includes zero". Tied pairs join
into groups: maximal runs of consecutive projects (singletons
allowed), so every project sits in exactly one group.

## Assumptions and limits

Parametric re-runs of the additive model: each review is project
level + judge offset + independent normal noise, with the noise SD
estimated from the residuals (SSE / (reviews - projects)). Rubric
bounds, rounding and correlated judging are not modelled, so
intervals are approximate and describe the model, not the truth.

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
Runtime 0.22 s for 200 refits (budget: under 3 s on a laptop).
