# Integration review fix: publication verifier holes, then full gate

Date: 2026-09-27. Scope: `src/results/services.py` (`verify_publication` and helpers),
`tests/test_adversarial_t2.py`. Isolated PostgreSQL `127.0.0.1:55433`, Django test DB
`test_verdict_reviewfix`, env `DATABASE_URL=postgresql://postgres:***@127.0.0.1:55433/verdict_reviewfix`,
`SECRET_KEY=adversarial-test-only`, `DATA_DIR=.data-reviewfix`. Sandbox-disabled Bash was used only
for loopback to that server. Genuine engine and prize recomputation is kept; the digest
checks run alongside it, never instead of it.

## Changes

1. Stored-input digest. New flag `stored_inputs_match = _digest(pub.inputs) == pub.input_digest`.
   `identical` now needs rows, awards (unless absent), stored-input digest and live digest.
   Detail part: `stored inputs no longer hash to the stored digest`. This is tamper evidence
   relative to the retained digest only; rewriting inputs and `input_digest` together (DB admin)
   is not detected by it.
2. Row set. Expected rows are built by `_build_rows` (the function publish uses) from the
   engine re-run and the live non-draft project roster, not from `pub.rows`. Both sides are
   projected to `project_id, rank, normalized, status` (+ `live_strength` for live pairwise),
   sorted by project id with duplicates kept, and compared. Removed, repeated, invented or
   draft rows give `rows_match: false`.
3. Guards (`Stored publication cannot be recomputed: ...`): stored ints and floats must satisfy
   `abs(x) <= 10**12` (rejects giant JSON ints before any float conversion); prizes need a text
   `name` and an int, absent or null `position`; every included review needs a value for every
   criterion (checked before the engine, no KeyError catch); recomputed lambda, normalized
   scores, raw means and live strengths must be finite. The exception boundary is unchanged:
   only `ValueError` around the engine re-run and prize re-allocation. `_unverifiable` also
   returns `stored_inputs_match: false`. `_verify_awards` reuses the rows' project snapshot.

## RED before the services change

`manage.py test tests.test_adversarial_t2 --noinput -v1` -> `Ran 15 tests in 2.078s`,
`FAILED (failures=8, errors=5)`:

| Case | Observed |
|---|---|
| judge `jdg_rt1` renamed consistently, digest untouched | FAIL `None is not False` after `rows_match` and `digest_match` passed true |
| rows: winner removed, all removed, duplicated, invented unranked, draft added | FAIL `'identical' != 'differs'` (5 subtests) |
| rehashed: review missing `innovation` | ERROR `KeyError: "missing value for criterion 'innovation'"` |
| rehashed: prize without `name` | ERROR `KeyError: 'name'` |
| rehashed: `lam`, weight, `max_score` = `10**400` | ERROR `OverflowError: int too large to convert to float` (3) |
| rehashed: prize `position` `"1st"` | FAIL `True is not false` (rows matched, only awards differed) |
| rehashed: weights 1, -1, 5e-324 (one score inf) | FAIL detail `Recomputed rows differ from stored rows; live data digest 9d8cc8a6a81f… differs ...` |

Controls passing in the same red run: unchanged rows with an unreviewed submitted and a
withdrawn project verify `identical`; top-level `inputs` null/list and `rows` null/object
already failed explicitly; the 10 earlier T2 tests.

## GREEN

Only change to a new test before green: the rename test compares the detail lowercased,
because `"; ".join(parts).capitalize()` starts it with `Stored`. No existing assertion changed.

`manage.py test tests.test_adversarial_t2 tests.test_results tests.test_pairwise tests.test_prizes tests.test_decision_room --noinput -v1`
-> `Ran 134 tests in 18.876s` / `OK` (output also shows a logged
`core.errors.ApiError: Only organizers of this event can manage results.`; the run is OK).

## Full gate

`.venv/Scripts/python.exe scripts/gate.py` with the same env, no `--docker`:

```
check            PASS 0 issues (1.5s)
migrations       PASS 0 changes (1.5s)
django-tests     PASS Ran 986 tests failures=0 errors=0 (256.6s)
engine-pure      PASS Ran 57 tests failures=0 errors=0 (1.5s)
exit=0
```

`git diff --check`: no output, exit 0 (tracked files). `tests/test_adversarial_t2.py` is
untracked; `git diff --no-index --check /dev/null tests/test_adversarial_t2.py` printed no
whitespace problem (its exit 1 only reports that the file differs from `/dev/null`).

## Known boundaries

- Live roster dependency: rows and awards read today's projects, so adding, deleting,
  drafting or changing the status of a project after publication reports `differs`. There is
  no historical-roster independence; that would need a roster snapshot in the publication.
- Not verified: stored row display order and the unprojected row fields (title, team, track,
  `n_reviews`, `raw_mean`, `rank_raw`/`rank_norm`/`rank_bt`, `n_comparisons`, `status_reason`).
  `raw_mean` sums in input order, which differs between publish and verify, so exact equality
  was not added.
- The `10**12` bound is a policy above the model limits (`DecimalField(6, 3)` weights,
  `PositiveSmallIntegerField` scores and positions). Not an exhaustive parser: `excluded`,
  `lambda_cv`, prize `track_name`/`eligibility_note` and comparison ids are not shape-checked
  beyond what recomputation reads. `OverflowError` inside the engine is not caught; no path was
  observed once stored numbers are bounded.
- `manage.py verify_publication` prints `rows_match`/`digest_match` but not the new flag (file
  not owned); its verdict and detail include the stored-input check.
- Pre-existing: publications without `params.criteria` recompute with the live rubric.

## Follow-up 2026-09-28: null prize position, final gate, evaluated-image smoke

Same env as above except DB `verdict_finalsmoke` (test DB `test_verdict_finalsmoke`) and
`DATA_DIR=.data-finalsmoke`.

Null prize position (root review case). Hypothesis: `_is_optional_int` accepts an explicit
`"position": null`, `_verify_awards` passes it into `PrizeSpec`, and an allocator sort against
another prize at position 0 raises `TypeError`. New regression
`MalformedStoredPublicationTests.test_rehashed_null_prize_position_verifies_like_omitted`: two
real prizes (positions 0 and 1) are allocated at publish (2 stored awards), then the stored
positions become `null` and `0` and `input_digest` is rehashed. It passed on its first run
against the unchanged `services.py` (`MalformedStoredPublicationTests`: `Ran 7 tests`, `OK`),
so the hypothesis did not reproduce and `src/results/services.py` was not changed:
`prizes.allocate` walks the stored list in stored order and never sorts by or reads
`position`. Locked-in behavior: an explicit null position verifies like an omitted one
(default 0). Rows, awards and the stored-input digest match; the verdict is `differs` only
through the live digest, because live prizes hold integer positions. Omitted and integer
positions are unaffected and `"1st"` still fails explicitly. `PrizeSpec.position` can hold
`None` on this path; nothing reads it.

Full gate, `.venv/Scripts/python.exe scripts/gate.py`, no `--docker`:

```
check            PASS 0 issues (1.4s)
migrations       PASS 0 changes (1.5s)
django-tests     PASS Ran 987 tests failures=0 errors=0 (248.4s)
engine-pure      PASS Ran 57 tests failures=0 errors=0 (1.5s)
exit=0
```

Evaluated-image smoke, from `verdict/`: `docker compose --env-file ../planning/EVAL-DEMO.env -p
verdict-eval-20260927 -f docker-compose.yml -f ../planning/compose-evaluation.override.yml -f
../planning/compose-host-preview.override.yml up -d --build --wait` -> exit 0. `web-1` and
`webhook-worker-1` were recreated; `db-1` kept container `7fa422759ef5` and its volume; all
reported healthy. `verdict-eval-20260927-web-1` runs image
`sha256:03c26d6df597ca58322944fd8e6667ca2e939350f0cc482461afbb8f6a180567` (tag
`verdict-eval:20260927`, created 2026-09-27T18:53:19Z UTC; before: `sha256:40b67aa270c1…`),
health `healthy`. Checkers, each via the same prefix plus `exec -T web python`:

| Command | Exit | Printed result |
|---|---|---|
| `run.py .dogfood.toml` | 0 | 7 PASS, 0 FAIL; `claimed T1 T2, verified T1 T2` |
| `scripts/verify_tiers.py .dogfood.toml` | 0 | 25 PASS, 0 FAIL; `summary 25/25 checks passed` |
| `scripts/attack.py .dogfood.toml` | 0 | `summary 28/28 attacks refused`; `direct HTTP checks 14/14 passed`; no FAIL line |

Original `verdict-web-1` (`62ab56df737e`) and `verdict-db-1` (`e3dceccc0aa3`) kept the same
container ids, images and start time (2026-09-27T17:22:15Z) before and after; no `down`, `rm`
or `prune` was run. The checkers created their own disposable probe data; no fixture or app row
was edited by hand.

Limits: the build reused the Docker layer cache (3 `CACHED` steps) and the host-preview
override adds a second, internet-capable network, so this is not a fresh offline or cold-build
validation. Stdout was inspected in the terminal only (no copies saved under
`docs/evaluation-20260927-hardening/`); the root `acceptance-report.txt` and tier claims are
unchanged.
