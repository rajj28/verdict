# F3: Calibration showcase event with planted judge bias (seed + in-app check)

Read `AGENTS.md` first. PostgreSQL only; use the `DATABASE_URL` you are given.

## Why

Normalization claims are easy to make and hard to see. On the organizers'
fixture nobody knows the true order, so nobody can check it. This packet adds a
third seeded event, "Calibration showcase" (slug `showcase`), generated from a
known truth: synthetic projects with known quality and judges with planted
habits (two harsh, two generous, the rest neutral). Judging is closed and
nothing is published, so an organizer can open it, see that VERDICT recovers
the planted habits and the true order better than raw averages, and rehearse
the whole publish flow without touching the real events.

## Contract

### 1. Generator: `src/core/showcase.py` (new, pure, deterministic)

- Fixed constants and `random.Random(SHOWCASE_SEED)`; no dependence on dict
  order or hash seeds. `build() -> dict` returns a fixtures-shaped dict with the
  same keys and shapes as `fixtures.json` (`event`, `tracks`, `judges`, `teams`,
  `projects`, `scores`), event id `evt_showcase`, name
  "Calibration showcase (synthetic)", and the same three criteria as the
  fixture (`functionality`, `quality`, `innovation`, integers 1-5).
- `truth() -> dict` returns the planted values the page compares against:
  `{"judge_offsets": {judge_email: float}, "quality": {project_title: float},
  "design": "<one sentence describing the routing rule>"}`.
- Suggested design (you may tune the constants, never by trying seeds until a
  result looks good; pick a rule, fix the seed, report what comes out):
  24 projects with true quality spread evenly from 1.8 to 4.4 in a fixed
  shuffled order; 12 judges, 2 harsh (offset -1.0), 2 generous (+1.0), 8 neutral
  (offset drawn from N(0, 0.15)); every project gets 3 reviews, every judge 6;
  harsh judges are routed mostly to strong projects and generous judges mostly
  to middling ones (this is the failure mode normalization exists for; state
  that the routing is deliberate). Per criterion score =
  clamp(round(quality + offset + N(0, 0.5)), 1, 5). The design must be connected
  (check with `results.engine.components`).
- Synthetic, neutral names and emails (`showcase.judge01@example.org`,
  `showcase.member01@example.org`, ...). Judge names must not reveal the planted
  habit. Titles/summaries short and plausible; mark the event as synthetic in
  its name and tagline.

### 2. Seeding

- `python manage.py seed_showcase` imports `build()` through the existing
  `interop.importer.import_fixture(data, slug="showcase", actor=<admin>)` path
  (so every data-model rule applies), then makes the seeded organizer an
  organizer of it the way the bootstrap does for the demo event. Idempotent: a
  second run is a no-op that says so. `--reset` deletes and re-imports only the
  `showcase` event (refuse any other slug).
- `core.bootstrap.bootstrap()` seeds it when `settings.DEMO_MODE` is on and it
  does not exist yet, after the fixture and demo events. It must not change the
  fixture event (`sample-hack-2026`) or the demo event: same counts and data
  before and after. It must not seed anything when `DEMO_MODE` is off.
- Judging closed, results unpublished, feedback unreleased.

### 3. Calibration check page (organizer only)

`GET /manage/showcase/calibration` (view + template
`src/templates/manage/calibration.html`); 404 for any other event (the truth is
only known for the showcase); normal organizer permission checks via the
existing policy helpers (anonymous -> login, other roles -> 403/404 as the
other manage pages do). Read-only. Computed live from the event's current
included reviews and the event's lambda, using `results.services` /
`results.engine` functions (never re-implement the fit). Show:

1. Judge habits: for every judge, planted offset vs estimated offset (the
   official fit), labelled harsh/generous/neutral by the planted value, sorted
   by planted offset; plus the correlation between planted and estimated.
2. Order recovery: Kendall tau and Spearman rho between true quality and
   (a) raw means, (b) normalized scores; number of projects whose rank is 3+
   places from their true rank under raw vs normalized.
3. Top 3: true top 3 vs raw top 3 vs normalized top 3.
4. One computed verdict sentence, e.g. "Normalization recovered the four planted
   habits within 0.3 points and moved the true winner from 5th (raw) to 1st."
   Never hard-code numbers or outcomes.
5. An honesty note: synthetic data, deliberate routing, the lambda used; a
   recovery here shows the method works when its assumptions hold, not that
   every real event is fixed by it. Link to `docs/NORMALIZATION-PROOF.md`.

Match the look of the other manage pages (existing classes, no inline
scripts/styles, CSP-safe). Add a link to the page from
`src/templates/manage/overview.html`, shown only for the showcase event. No N+1
queries (`assertNumQueries` bound).

### 4. Tests: `tests/test_showcase.py`

- `build()`/`truth()` deterministic (equal across two calls); design connected;
  every project has 3 reviews and every judge 6.
- `seed_showcase` twice -> same counts; `--reset` restores after a change;
  fixture and demo events untouched (compare counts of projects, reviews,
  scores, roles per event before and after).
- Bootstrap seeds it with DEMO_MODE on and not with DEMO_MODE off.
- Page: organizer 200 with the four sections; judge/participant denied;
  anonymous redirected to login; 404 for other events.
- Recovery on the generated data: normalized Kendall tau > raw Kendall tau, and
  the estimated offsets of all four planted judges have the planted sign.
- Query-count bound on the page.

### 5. Docs: `docs/SHOWCASE.md`

What the event is, the exact generator rule and seed, the numbers the page shows
on a fresh seed (from running it), and a 6-step organizer rehearsal on it
(preview -> inspect the calibration page -> exclude or disqualify with the
consequence preview -> publish -> Verify -> certificates), plus how to reset it.

## Scope

Touch only: `src/core/showcase.py` (new), `src/core/management/commands/seed_showcase.py`
(new), `src/core/bootstrap.py` (the showcase call), the calibration view (put it
in `src/results/views.py`? no: another worker edits that file; add
`src/core/calibration_views.py` or similar and wire the URL in the core or
project URLconf you find), `src/templates/manage/calibration.html` (new),
`src/templates/manage/overview.html` (the link), `tests/test_showcase.py`
(new), `docs/SHOWCASE.md` (new). Other workers are editing
`src/results/services.py`, `src/results/views.py`, `src/results/api*.py`,
`src/results/engine.py`, `src/templates/manage/results.html`,
`src/projects/**`, `src/judging/**`, `JUDGING.md`: do not touch those. No new
migrations unless unavoidable (say why). Never git commit.

## Done means

`manage.py test tests.test_showcase tests.test_bootstrap` passes (use the
bootstrap test module name that exists), `manage.py check` and
`manage.py makemigrations --check --dry-run` are clean. Finish with SUMMARY /
FILES / TESTS / GAPS as `AGENTS.md` says.
