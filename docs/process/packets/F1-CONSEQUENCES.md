# F1: Consequence preview before publish, disqualify, exclude, re-include

Read `AGENTS.md` first. PostgreSQL only; use the `DATABASE_URL` you are given.

## Why

An organizer about to publish results, disqualify a project, exclude a review or
put a review back should see exactly what that changes before it happens:
"This changes 4 ranks and 1 award: Best overall moves from Paper Trails to Tide
Table." The action then executes only if the data is still what the preview
showed. No hackathon tool does this; it is a headline feature, so build it
completely (service, API, UI, tests, docs), not a stub.

## Contract

### 1. Service: `src/results/consequences.py` (new)

- Refactor `results.services.preview(event)` so its computation runs from
  explicit inputs: `preview(event)` must call a helper such as
  `_preview_from(event, inc, exc, eligible_project_ids)` and return exactly what
  it returns today (same keys, same values, same `input_digest`). Existing tests
  must stay green without edits.
- `what_if(event, *, disqualify=(), exclude_reviews=(), include_reviews=()) -> dict`
  returns the preview dict for a hypothetical change applied in memory only:
  a disqualified project leaves the eligible set exactly as a real
  disqualification would (same code path the real status uses); an excluded
  review moves from included to excluded; a re-included review moves back. No
  writes, no row locks, no audit entries. `what_if(event)` with no change must
  equal `preview(event)`.
- `consequences(event, action, target=None) -> dict`, actions:
  - `disqualify` (target: project public_id), `exclude_review` and
    `include_review` (target: review public_id): compare `preview(event)`
    (before) with `what_if(...)` (after).
  - `publish` (no target): compare the official (latest) publication's stored
    rows and awards (before) with the current preview (after). With no
    publication yet, report the first publication plainly
    ("First publication: 38 ranked projects, 3 awards.").
- Returned keys: `action`, `target`, `basis_digest` (the live input digest right
  now), `rank_changes` (list of `{project, title, before, after}` for every
  project whose official rank string changes, including ranked -> ineligible or
  unranked), `award_changes` (list of `{prize, prize_name, before, after}` where
  `before`/`after` are lists of `{project, title}`), `winner_before`,
  `winner_after`, `n_rank_changes`, `n_award_changes`, and `sentence`: one plain
  sentence computed from the data, e.g. "Disqualifying Paper Trails changes 4
  ranks and 1 award: Best overall moves from Paper Trails to Tide Table." or
  "Excluding this review changes no rank and no award."
- Validation: unknown/foreign targets -> 404 via `ApiError`; a target that is
  not applicable (already disqualified project, already excluded review, review
  not excluded for `include_review`) -> 409 with the same codes/messages the real
  action would give.

### 2. API

`POST /api/v1/events/<slug>/results/consequences`, body
`{"action": "...", "target": "..."}` -> 200 with the dict above. Organizers of
the event only (anonymous 401, other roles 403, events the caller cannot see
404). Read-only: no writes, no audit. Annotate with drf-spectacular request and
response serializers: `manage.py spectacular --validate --fail-on-warn --file
NUL` must still report zero errors and zero warnings.

### 3. Revalidation on execution

The publish endpoint, the project disqualify endpoint and the review exclusion
endpoint (POST exclude, DELETE put back) accept an optional `expected_digest`
in the JSON body. When present: lock the Event row (`select_for_update`) at the
start of the service call, recompute the live input digest inside that
transaction, and if it differs raise 409 with code `stale_preview` and message
"The results changed since you previewed this action. Review the new
consequences and confirm again." Nothing is written in that case. When absent,
behaviour is unchanged (run.py and every existing test must keep passing).
Import across apps lazily inside functions if needed to avoid import cycles.

### 4. UI (organizer results page)

`src/templates/manage/results.html` plus a new CSP-safe
`src/static/js/consequences.js` (no inline scripts, no `on*=` attributes; every
write still goes through the API as `api-forms.js` does):

- Publish: pressing "Publish results" first fetches the `publish` consequences
  and shows them in a confirm dialog (headline sentence, rank-change table capped
  at 10 rows with "and N more", award changes). Confirm submits the publish with
  `expected_digest`. On 409 `stale_preview`, show the message and reload the
  consequences in the same dialog.
- Rankings table: add a "Disqualify" action per ranked project (reason
  required) -> consequences dialog -> confirm calls the existing disqualify API
  with `reason` and `expected_digest`.
- Outlier reviews table: add an "Exclude" action (reason required) with the same
  flow against the existing exclusion API. The existing "Put back" button for
  excluded reviews gets the same preview-then-confirm flow.
- Accessible: focus moves into the dialog and back, Escape closes, loading and
  error states are announced (`aria-live`), usable by keyboard alone. Match the
  page's existing classes and tone; no new CSS framework.

### 5. Tests: `tests/test_consequences.py`

- `what_if` with no change equals `preview` (rows, awards, digest).
- Disqualifying the leader: `winner_after` is the previous second place, award
  changes listed, `sentence` names both; and nothing was written (project
  statuses, review exclusions, audit rows and publications unchanged).
- Excluding a deciding review changes the expected ranks; re-including restores.
- Publish consequences: no change since the official publication -> 0 ranks and
  0 awards; after an exclusion -> non-zero; no publication -> first-publication
  sentence.
- Permissions: anonymous 401, judge and participant 403, organizer of another
  event 403 or 404.
- Stale preview: take consequences, make another results-affecting change, then
  execute with the old `expected_digest` -> 409 `stale_preview` and no write;
  with the fresh digest -> success. Cover publish, disqualify, exclude, put back.
- No `expected_digest` -> old behaviour.
- `assertNumQueries` bound on the consequences endpoint (state the bound).

### 6. Docs

Add a "Consequence preview" section (8-15 lines) to `JUDGING.md`: what is
compared for each action, the digest check, and that the preview is exact (same
code path as preview/publish), not an estimate.

## Scope

Touch only: `src/results/consequences.py` (new), `src/results/services.py`
(preview refactor, publish `expected_digest`), `src/results/api.py`,
`src/results/api_urls.py`, `src/results/views.py` (template context only),
`src/projects/services.py` and `src/projects/api.py` (disqualify
`expected_digest`), `src/judging/services.py` and `src/judging/api.py`
(exclusion `expected_digest`), `src/templates/manage/results.html`,
`src/static/js/consequences.js` (new), `tests/test_consequences.py` (new),
`JUDGING.md`.

Other workers are editing `src/results/engine.py`, `src/core/**`,
`src/templates/manage/overview.html`, `scripts/**`, `docs/**` (except this
packet): do not touch those. Never git commit.

## Done means

- `manage.py test tests.test_consequences tests.test_results tests.test_publication_policy tests.test_adversarial_t2 tests.test_judging tests.test_projects` passes
  (use whichever of these modules exist).
- `manage.py spectacular --validate --fail-on-warn --file NUL` -> no errors, no warnings.
- `manage.py check` and `manage.py makemigrations --check --dry-run` clean.

Finish with SUMMARY / FILES / TESTS / GAPS exactly as `AGENTS.md` says.
