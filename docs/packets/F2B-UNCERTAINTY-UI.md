# F2B: Rank uncertainty in the product (preview, publish, results pages, API)

Read `AGENTS.md`, `docs/UNCERTAINTY.md` and `engine.rank_uncertainty` in
`src/results/engine.py` first. PostgreSQL only; use the `DATABASE_URL` you are
given.

## Why

`engine.rank_uncertainty` exists and is tested (seeded parametric re-runs of the
additive model: 90% rank intervals, `p_first`, `p_top`, `p_above_next`, tied
pairs, groups, a summary sentence, an assumption text). On the organizers'
fixture the likely winner is first in only 22% of re-runs and every adjacent
pair is statistically tied: organizers need to see this before they hand out
prizes, and the public page should state it honestly. Competitors show
confidence intervals; we make it decision-grade: tied labels at the award
boundaries, a warning in the publish dialog, and the same numbers in the API.

## Contract

1. Preview (`results.services.preview`): add an `uncertainty` key computed with
   `engine.rank_uncertainty(scored, lam)` where `scored` and `lam` are exactly
   what the official fit used. `top_k` = the number of overall-ranked award
   places the event's prize configuration hands out (fall back to 3). Only for
   the normalized method; for raw and pairwise return
   `{"available": false, "reason": "Rank intervals are computed for the normalized ranking only."}`.
   Serialise it plainly: `available`, `replicates`, `seed`, `level`, `top_k`,
   `sigma`, `df`, `summary`, `assumption`, `reason`, `tied_pairs`, `groups`.
   Each ranked row gains `rank_low`, `rank_high`, `p_first`, `p_top`,
   `p_above_next` and `tied_with_next` (bool). Round probabilities to 3 decimals
   and scores to 2 only at serialisation. The computation must add no database
   queries.
2. The input digest, the publication snapshot, stored rows and Verify stay
   exactly as they are (uncertainty is derived, never stored or hashed). Every
   existing test must pass unchanged.
3. Published results (public page and public results API): compute the same
   uncertainty from the publication's stored inputs (the ones Verify
   reproduces), so it is the same for every viewer and does not move when live
   data changes. Cache it in Django's cache keyed by publication id and digest.
4. Organizer results page (`src/templates/manage/results.html`): a "Certainty"
   column in the rankings table ("ranks 2-6, top 3 in 64%"), a tie marker
   between adjacent tied rows, and a callout above the table with the summary
   sentence, the assumption in a details/summary element, and a link to
   `docs/UNCERTAINTY.md` on GitHub-relative path text (no external link).
   Match existing classes, CSP-safe, accessible (no colour-only meaning).
5. Public results page (find the template the public results route renders):
   rank ranges and a "statistically tied" label on tied adjacent rows, plus one
   plain sentence for the public: "Ranks are estimates from a few reviews each.
   Projects marked as tied could swap places if different judges had reviewed
   them." Do not show sigma or seeds to the public.
6. Publish consequences (`src/results/consequences.py` and
   `src/static/js/consequences.js`): the `publish` consequences gain
   `certainty` (the summary sentence) and `top_tied` (bool). When `top_tied`,
   the publish dialog shows a clearly styled warning line above Confirm:
   "1st and 2nd are statistically tied (order held in 46% of 200 re-runs).
   Consider a tie-break review or a shared award." (numbers computed). It does
   not block publishing.
7. API: add the new fields to the preview and public results serializers with
   drf-spectacular types; `manage.py spectacular --validate --fail-on-warn
   --file NUL` must stay at zero errors and zero warnings.
8. Docs: a short "Rank uncertainty" section in `JUDGING.md` (6-12 lines) that
   links `docs/UNCERTAINTY.md` and says it is derived, not stored, and why.

## Tests: `tests/test_uncertainty_integration.py`

- Preview has uncertainty for a normalized event; unavailable with the reason
  for raw and pairwise.
- Row fields present for ranked rows, absent or null for unranked/ineligible;
  `rank_low <= rank_high`; probabilities in [0, 1].
- Digest, snapshot and Verify unchanged by this feature (publish, then Verify
  is identical; the stored snapshot has no uncertainty keys).
- Public results uncertainty comes from stored inputs: publish, then exclude a
  review live; the public page's uncertainty is unchanged, the preview's
  changes.
- Publish consequences carry `certainty`/`top_tied`; `top_tied` true on a
  constructed near-tie and false on a clear winner.
- Organizer page and public page render the new column/labels (assert text).
- Query count of the preview endpoint unchanged versus before this feature
  (state the bound), and the fixture preview completes in under 3 seconds.

## Scope

Touch only: `src/results/services.py`, `src/results/consequences.py`,
`src/results/api.py`, `src/results/views.py`,
`src/templates/manage/results.html`, the public results template,
`src/static/js/consequences.js`, `tests/test_uncertainty_integration.py`
(new), `JUDGING.md`. Do not edit `src/results/engine.py`. Other workers are
editing `src/core/**`, `src/templates/manage/overview.html`,
`src/templates/manage/calibration.html`, `scripts/**` and `docs/**`: do not
touch those. Never git commit.

## Done means

`manage.py test tests.test_uncertainty_integration tests.test_consequences
tests.test_results tests.test_publication_policy tests.test_adversarial_t2`
passes; `python -m unittest tests.test_engine` passes; spectacular strict
validation clean; `manage.py check` clean. Finish with SUMMARY / FILES / TESTS /
GAPS as `AGENTS.md` says.
