# F5B: Tour sandbox = a full private copy; API First with no allow-lists

Read `AGENTS.md`, `docs/TOUR.md`, `src/core/tour.py`, `src/core/showcase.py`,
`src/core/calibration_views.py` and `tests/test_api_first.py` first. PostgreSQL
only; use the `DATABASE_URL` you are given.

## Why (review findings on F5)

1. **Isolation gap.** `_fresh_fixture()` gives every sandbox the same team-member
   accounts (`tour.memberNN@example.org`). `import_fixture` gives imported users the
   shared demo password, so one account has participant roles in every visitor's
   sandbox. `role_user()` picks accounts with `.first()` over roles, which is
   ordering-dependent.
2. **Thin sandbox.** The sandbox keeps only one judge's six reviews, so the tour's
   "compare raw and normalized / planted habits" step cannot be shown in it, and the
   sandbox calibration page is a placeholder.
3. **API First gaps.** `tests/test_api_first.py` carries temporary allow-lists:
   `KNOWN_GENERATOR_ERROR_FRAGMENTS` and `TOUR_INFLIGHT_OPERATION_IDS` (tour ops
   without summaries and documented errors), and `ENVELOPE_EXCEPTIONS` (four
   older ops whose errors do not reference the shared ErrorEnvelope component).

## Contract

1. A sandbox is a **full private copy** of the calibration showcase:
   - all 12 judges, all 24 projects and teams, and all 72 reviews;
   - every account in it (the organizer, the 12 judges, every team member and the
     tour participant) is created for that sandbox only, with a unique
     `@tour.verdict.local` address and an unusable password;
   - no account appears in two sandboxes or in any non-sandbox event.
   Keep the planted truth usable: the calibration page maps judges and projects
   through their fixture ids (for example the role's or project's source/public
   id carried from the fixture), not through emails. The tour judge is one of the
   12 judges (for example `jdg_sc01`), with two of its six reviews reopened as
   drafts. Everything else stays as F5 built it: submissions closed, judging
   open, nothing published.
2. **Role switching** logs in only the sandbox's designated accounts: its
   organizer, its tour judge and its tour participant. Look them up explicitly
   (stored mapping or exact addresses), never by `.first()` over roles.
3. **Calibration in the sandbox.** `/manage/<sandbox>/calibration` renders the real
   calibration sections (judge habits, order recovery, top 3, verdict sentence,
   honesty note) for the sandbox's live data. Remove the placeholder
   `tour_calibration.html` if the real page replaces it. The main showcase page
   stays as it is.
4. **Prune and reset** delete every sandbox-owned account with its event, and
   nothing else.
5. **Performance.** Creating a sandbox must take under 3 seconds on this laptop.
   Measure it in a test with a generous bound and report the real number.
6. **API First, no exceptions.**
   - Annotate the tour operations with a summary, typed request and response
     serializers, and 4xx responses that reference the shared ErrorEnvelope
     component.
   - Fix the four `ENVELOPE_EXCEPTIONS` operations the same way.
   - Delete all three allow-lists from `tests/test_api_first.py`.
   - Regenerate `docs/openapi.yaml` with `manage.py spectacular --file
     docs/openapi.yaml --validate --fail-on-warn`.
   - Add the tour operations to the operations table in `docs/API.md` (the docs
     coverage test requires every operationId there).

## Tests

- Extend `tests/test_tour.py`:
  - two sandboxes share no user ids;
  - no sandbox user has a usable password;
  - a sandbox has 12 judges, 24 projects, 70 submitted reviews and 2 drafts for the
    tour judge;
  - role switching yields exactly the designated accounts;
  - the sandbox calibration page shows the four planted habits with the right sign;
  - the full happy path: score the 2 drafts, close judging, preview a disqualify
    consequence, publish, and Verify says identical;
  - prune removes every sandbox account and leaves every other user and event
    unchanged;
  - the creation-time bound.
- `tests/test_api_first.py` passes with the allow-lists deleted.

## Scope

`src/core/tour.py`, `src/core/calibration_views.py` (only the minimal hook to map
by fixture ids for sandboxes), tour templates, `tests/test_tour.py`,
`tests/test_api_first.py` (delete the allow-lists only), `docs/openapi.yaml`,
`docs/API.md` (tour rows only), `docs/TOUR.md`, and the four views behind
`ENVELOPE_EXCEPTIONS` (annotations only). Do not change business rules elsewhere.
Never git commit.

## Done means

`manage.py test tests.test_tour tests.test_showcase tests.test_api_first` passes;
`manage.py spectacular --validate --fail-on-warn --file NUL` is clean;
`manage.py check` is clean. Finish with SUMMARY / FILES / TESTS / GAPS.
