# Adversarial T2: private judge notes and malformed stored publications

Date: 2026-09-27. Scope: `src/results/services.py`, `tests/test_adversarial_t2.py`.
Database: isolated PostgreSQL `127.0.0.1:55433`, Django test DB `test_verdict_hardt2`,
env `DATABASE_URL=postgresql://postgres:***@127.0.0.1:55433/verdict_hardt2`,
`SECRET_KEY=adversarial-test-only`, `DATA_DIR=.data-hardt2`. The agent Bash sandbox
blocks localhost TCP (`server closed the connection unexpectedly`), so the runs below
used sandbox-disabled Bash against that designated test server only.

## Target 1: private notes stay private

Contract: `judge/review.html` collects `Review.comment` as "private to you and the
organizers, never shown to the team". `results.services.project_feedback` returned
those comments to the owning team once feedback was released.

Fix: `project_feedback` no longer reads `Review.comment`. The `comments` key stays in
the payload, always `[]`, to keep the API shape. Score, rank, `n_reviews` and
per-criterion averages are unchanged. Authorized reads are unchanged: the authoring
judge via `GET /api/v1/events/{slug}/judge/reviews/{prj}`, organizers of the same event
via `exports/reviews.csv` and the manage pages. Team-facing feedback would need a
distinct, separately consented field and workflow (out of scope, not added).

Tests (`PrivateJudgeNotesTests`: published, feedback released, marker `T2-PRIVATE-NOTE-9c41e7`):
owning team gets 200 without the marker and `comments == []`; owning team keeps the
score summary (rank and official score equal the stored row, 2 reviews, averages 4.5);
the service returns no comments for either project; a peer participant gets 403; an
organizer of an unrelated event gets 403 on feedback and `reviews.csv`; the authoring
judge and the own-event organizer still read the marker; public results HTML and API
are 200 without the marker.

RED before the fix, `manage.py test tests.test_adversarial_t2 --noinput -v2`:

```
FAIL: test_feedback_service_never_returns_review_comments (...) (project='prj_rt1')
AssertionError: Lists differ: ['Excellent', 'T2-PRIVATE-NOTE-9c41e7 demo crashed twice'] != []
FAIL: test_feedback_service_never_returns_review_comments (...) (project='prj_rt2')
AssertionError: Lists differ: ['Interesting approach'] != []
FAIL: test_owning_team_feedback_omits_private_note (...)
AssertionError: 'T2-PRIVATE-NOTE-9c41e7' unexpectedly found in '{"pub_id":"pub_qsxz4bc7os","feedback_released":true,
"official_score":87.4999999997278,"rank":"1","n_reviews":2,"per_criterion":{"quality":4.5,"innovation":4.5},
"comments":["Excellent","T2-PRIVATE-NOTE-9c41e7 demo crashed twice"]}'
Ran 7 tests in 0.820s
FAILED (failures=3)
```

GREEN after the fix, `manage.py test tests.test_adversarial_t2 tests.test_results tests.test_decision_room --noinput -v1`:
`Ran 60 tests in 8.004s` / `OK`.

No existing assertion relied on exposing notes (`test_results` only checks that comments
carry no `jdg_` id), so no existing test was changed.

## Target 2: malformed stored publications fail explicitly

`verify_publication` read the persisted canonical inputs without shape checks, so the
organizer verify API raised (HTTP 500) on malformed JSON. RED (target 1 already fixed),
same `-v2` command, `Ran 10 tests`, `FAILED (errors=18)`; the valid snapshot passed:

| Stored field tampered | Unhandled exception |
|---|---|
| params null / list | AttributeError: no attribute get |
| params.lam "not-a-number" / [2.0] | ValueError could not convert / TypeError float() argument |
| params.lam -1 | ValueError: lam must be non-negative |
| params.method null | ValueError: method must be one of (normalized, raw, pairwise) |
| params.criteria [null] / weight "heavy" | TypeError not subscriptable / ValueError could not convert |
| included null / item list / criteria null | TypeError not iterable / list indices / not subscriptable |
| included value "five" / 99 | TypeError str vs int / ValueError value 99 outside [1, 5] |
| comparisons [null], projects [null] | TypeError not subscriptable / TypeError str vs NoneType |
| prizes [null] / places "two" | TypeError not subscriptable / ValueError invalid literal for int() |
| rows[0] null | AttributeError: no attribute get |

Fix (bounded, genuine recomputation kept): a type-only shape check of the stored inputs
and rows (`_malformed_inputs`), plus a `ValueError` catch around only the engine re-run
and the prize re-allocation, which is how the engine rejects out-of-domain values. Both
return the existing failure verdict `differs` with detail `Stored publication cannot be
recomputed: <reason>.`, `rows_match`/`awards_match`/`digest_match` false and
`live_digest` null. A valid snapshot still recomputes and verifies `identical`.

GREEN, `manage.py test tests.test_adversarial_t2 tests.test_results tests.test_decision_room --noinput -v1`:
`Ran 63 tests in 8.952s` / `OK`.

## Limitations

- Full suite and T1 tests were not run at the time (another worker was active). Superseded:
  the full gate passed later, see `docs/ADVERSARIAL-INTEGRATION-20260927.md`.
- No new verdict value: an unrecomputable publication reports `differs`; a distinct
  `unverifiable` verdict would need a UI review.
- Fixed in the integration review (`docs/ADVERSARIAL-INTEGRATION-20260927.md`): stored inputs
  are now re-hashed against `input_digest` (`stored_inputs_match`) next to recomputation, stored
  rows are checked against the live non-draft roster, and giant numbers, a missing criterion
  score, a nameless prize and a non-int prize position now fail explicitly.
- Pre-existing: publications without `params.criteria` recompute with the live rubric.
- Organizers calling the feedback API now also see `comments: []`; they read notes via
  `reviews.csv` and the manage pages. No participant HTML renders feedback today.
