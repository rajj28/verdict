# Packet P3-PW: pairwise judging mode (Bradley–Terry), complete or not at all

Requires T2 merged (engine BT from P2-EN, judging services from P2-JA). Read BUILD-SPEC 9 (Bradley–Terry), 17 (bonus plan) and AGENTS.md.

## Do
1. Event `judging_mode` pairwise or both enables `/judge/{slug}/pairwise`. Pairs are drawn only from the judge's own active assignments (same isolation as rubric reviews), inside the judging window.
2. Pair selection (`src/judging/pairs.py`, pure function, seeded): prefer the pair this judge has not compared yet whose projects have the fewest total comparisons event-wide (information first), never repeat a pair for the same judge until every pair of their assignments has been seen, deterministic tie-break with a seed of (event slug, judge id, count). Stopping policy: a judge is done when each assigned project has ≥ `pairwise_min_comparisons` comparisons by that judge (default 3) or all pairs are exhausted; the console shows progress toward that.
3. API: `GET /events/{slug}/judge/pairs/next` → {left, right, progress} or {done: true}; `POST /events/{slug}/judge/comparisons` {left, right, winner|null} (null = "too close to call", recorded, not a win); duplicate submission of the same pair by the same judge → 409. Organizer: comparisons export `pairwise.csv`.
4. Console page: two project cards side by side (title, summary, links, images, private answers since assigned), buttons "Left is better" (key ←/A), "Right is better" (→/D), "Too close" (↓/S), progress bar, undo last within 30 s (deletes the comparison, audited).
5. Results: when comparisons exist, the results page shows the BT ranking (live pairwise) next to rubric results; if `ranking_method = pairwise` it is official. Projects with no comparisons are `unranked_no_reviews`. Components of the comparison graph reported; disconnected components flagged as not comparable.
6. JUDGING.md section "Pairwise mode": why pairwise sidesteps calibration, the MM estimator with the virtual-opponent prior, pair selection and stopping, what "too close" means, limitations (intransitive preferences, judges with different taste, needs enough comparisons: guidance ≈ 3 per project per judge).

## Tests (`tests/test_pairwise.py`)
Isolation (unassigned project in a pair → 403; other judge's comparisons invisible), window closed → 403, duplicate pair 409, selection never repeats early and is deterministic, stopping policy, BT results ordering on a scripted set of comparisons, undo window, results integration and unranked handling.

## Files you own
src/judging/pairs.py, pairwise parts of src/judging/{services,api,api_urls,views,urls}.py (coordinate: add new functions, do not rewrite existing ones), src/templates/judge/pairwise.html, src/static/js/pairwise.js, results integration in src/results/services.py (additive), tests/test_pairwise.py, JUDGING.md pairwise section.
