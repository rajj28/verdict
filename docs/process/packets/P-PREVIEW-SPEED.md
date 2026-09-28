# P-PREVIEW-SPEED: the consequence dialog must not sit on "Loading" for 6 seconds

Read `AGENTS.md` first. PostgreSQL only. Test DB: `DATABASE_URL=postgres://postgres:postgres@127.0.0.1:55433/verdict_w2`.

## Evidence (orchestrator, measured on the showcase event)
- Clicking Disqualify on /manage/showcase/results: `POST /api/v1/events/showcase/results/consequences` answers after **5.7 s**.
- `results/consequences.py::consequences()` calls `services.preview(event)` for "before" and `what_if(...)` for "after".
  Each runs the full engine; profile of one preview: `rank_uncertainty` ~2/3 of the time (200 re-runs of
  `fit_additive`), `robustness` most of the rest.
- The "before" preview is the same computation the results page (and the previous preview) already did.

## Change
1. Memoize the engine output of `services.preview` / `_preview_from` in-process, keyed by everything that determines
   the output: the input digest (`input_digest`) PLUS any other inputs `_preview_from` reads (rubric version,
   policy/params, prizes/award rules, project status overrides, engine version). Read `_preview_from` carefully and
   put every input it reads into the key, or do not cache that part. Small LRU (e.g. 16 entries). Returned objects
   must be deep-copied (or treated immutably) so callers cannot mutate the cache.
2. Do NOT change any numeric result, the seed, the order of computation, or what Verify replays. Publication and
   Verify paths should keep computing fresh (do not route Verify through the cache).
3. If after (1) the dialog is still > 2.5 s on the showcase, look at whether the consequences response needs full
   `robustness` for the "after" state (it uses `_certainty_fields` = uncertainty only). If robustness is not used by the
   consequences response, skip it there via an explicit parameter. Keep uncertainty.

## Tests (red before green where possible)
- A test that the cached preview equals a fresh uncached preview field-for-field (showcase-like fixture).
- A test that changing a review value, a prize, or a project status yields a different (non-cached) result.
- A test that Verify of a publication still recomputes (no cache hit path).
- Run: `python manage.py test tests.test_astra_final tests.test_consequences* tests.test_results* -v 1` (whatever exists)
  and then the full suite `python manage.py test` — report counts.

## Done means
Timings before/after for the showcase consequences call (use the Django test client or a small script against the
test DB loaded with `python manage.py seed_showcase` or the equivalent command that exists — check `manage.py help`).
Never git commit. Finish with SUMMARY / FILES / TESTS (with counts) / TIMINGS / GAPS.
