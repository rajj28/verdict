# F5: "Click and play" guided tour with a private sandbox per visitor

Read `AGENTS.md`, `docs/SHOWCASE.md`, `src/core/showcase.py` and
`docs/REAL-WORLD-JUDGING.md` first. PostgreSQL only; use the `DATABASE_URL` you
are given.

## Why

Judges are senior engineers with forty portals to look at. After
`docker compose up` (or on a hosted copy) they should see one full event
lifecycle in five minutes by clicking, with no passwords and no reading. Every
click must be a real action through the real API on real (synthetic) data, and
two visitors must never step on each other. This is the "click and play" demo.

## Behaviour

Only when `settings.DEMO_MODE` is on. With `DEMO_MODE` off, every tour URL returns
404 and the home page shows no tour.

1. **Entry.** The home page shows a prominent "Take the 5-minute tour" card: one
   sentence ("One full event, real actions, synthetic data, about five
   minutes"), a Start button, and the note that it runs in a private sandbox.
2. **Sandbox.** `POST /api/v1/tour/start` creates a private copy of the
   calibration showcase for this browser session: a new event with slug
   `tour-<8 random chars>` imported through `interop.importer.import_fixture`
   from `core.showcase.build()` (use a distinct event id per sandbox), plus
   three sandbox-only accounts (organizer, judge, participant) with random
   emails under `@tour.verdict.local` and no usable password. Adjust the copy so
   the tour has something to do:
   - submissions already closed (a participant edit is refused on server time:
     this demonstrates the deadline);
   - judging open, with two of the sandbox judge's reviews still to do (remove
     those two scores from the imported data and keep them as assignments);
   - results unpublished.
   The sandbox judge takes the place of one showcase judge, so the planted
   truth still applies. Do not edit `src/core/showcase.py`,
   `src/core/bootstrap.py` or `src/core/calibration_views.py`; put sandbox code
   in `src/core/tour.py` (or a small `tour` package) and call their public
   functions. If the calibration page needs to know about sandboxes, add a
   tiny documented hook rather than rewriting it.
3. **Role switching.** `POST /api/v1/tour/role` with `{"role": "organizer" |
   "judge" | "participant"}` logs the browser in as that sandbox account (Django
   session login). It works only for the sandbox tied to this session, never for
   anyone else's. The sandbox accounts can reach only their sandbox event.
4. **Steps.** About ten steps, defined server-side in one place (a list of
   dicts: id, title, one or two plain sentences, the role it needs, the page URL
   template, a CSS selector to highlight, an optional "try this" hint).
   Suggested order, adjust to what the pages really offer:
   1. Welcome: what you will see, synthetic data, everything real.
   2. Participant: open your project; try to save an edit after the deadline and
      read the server-time refusal.
   3. Public gallery: your project is listed; emails never shown.
   4. Judge: your queue; score the two open reviews with the keyboard.
   5. Judge privacy: try another judge's review and see it refused.
   6. Organizer: progress view, then close judging.
   7. Organizer: raw versus normalized ranking; the calibration check finds the
      planted harsh and generous judges.
   8. Organizer: the Certainty column; disqualify the leader and read the
      consequence preview before deciding.
   9. Publish (with the preview), open the public results, press Verify.
   10. Certificate and its verification; the end card: what to try next
       (`python run.py .dogfood.toml`, `scripts/verify_tiers.py`,
       `docs/TIER-EVIDENCE.md`), plus Reset.
5. **Tour panel.** `src/static/js/tour.js` plus a small stylesheet. The step data
   is rendered as a JSON data island (`<script type="application/json"
   id="tour-steps">`, which CSP allows because it is not executed). A compact
   panel pinned bottom-right: step n of N, title, text, Back / Next, "Switch to
   <role>" when the next page needs another role (calls the role endpoint, then
   navigates), highlight of the target element (outline and scroll into view),
   Exit, and Reset sandbox (`POST /api/v1/tour/reset`: deletes this session's
   sandbox and creates a fresh one). The current step lives in the session
   (server) and in localStorage (convenience; wrap in try/catch). Keyboard
   usable, `aria-live` for step changes, respects `prefers-reduced-motion`,
   works at 360 px wide. Never covers the element it highlights (move to the
   other corner if needed).
6. **Limits and cleanup.**
   - At most one sandbox per session.
   - At most 60 live sandboxes in total; above that, start refuses with 429 and
     a friendly message.
   - Rate limit start/reset per client IP using the project's existing rate-limit
     helper.
   - `python manage.py prune_tour_sandboxes [--older-than-hours 6]` deletes
     expired sandboxes with their sandbox accounts and nothing else. Start also
     prunes expired ones lazily.
   - Sandboxes never appear in public event lists, the gallery of other events,
     exports of other events, or the fixture/demo/showcase data.
7. **API.** The tour endpoints are drf-spectacular annotated like the others
   (`manage.py spectacular --validate --fail-on-warn --file NUL` stays clean) and
   go through `api-forms.js` conventions or `fetch` with the CSRF header.

## Tests: `tests/test_tour.py`

- `DEMO_MODE` off: every tour route 404, home page has no tour card.
- Start creates exactly one event, three sandbox accounts and their roles. A
  second start in the same session returns the same sandbox. Two sessions get two
  independent sandboxes; actions in one never change the other.
- The sandbox has submissions closed, judging open, exactly two open reviews for
  the sandbox judge, and nothing published.
- Role switch logs in only as this session's sandbox accounts. Another session
  cannot switch into them (403/404). Sandbox accounts cannot read or write any
  other event (sample a few API calls).
- Reset replaces the sandbox. Prune removes expired sandboxes and their accounts
  and leaves every other event and user untouched.
- Cap and rate limit return 429 with the envelope.
- The step JSON renders on the pages and every step URL resolves (200 for the
  right role).
- The full happy path: the judge scores both reviews, the organizer closes
  judging, previews the disqualify consequence, publishes, and Verify says
  identical.
- Query-count bounds on start and on a page with the panel.

## Docs: `docs/TOUR.md`

What the tour shows, how sandboxes are isolated and cleaned up, limits, and how
to turn it off (`DEMO_MODE=0`). Include one line for the README (the
orchestrator places it).

## Scope

New: `src/core/tour.py` (or `src/tour/`), `src/core/management/commands/prune_tour_sandboxes.py`,
`src/static/js/tour.js`, a tour stylesheet under `src/static/css/`, templates or
includes for the panel and the home-page card, `tests/test_tour.py`,
`docs/TOUR.md`. Edit only where needed to mount it: URLconfs, the base template
(include the panel when a tour is active), the home template, and settings (a
setting for the cap). Do not edit `src/core/showcase.py`,
`src/core/bootstrap.py`, `src/results/**`, `src/judging/**` business rules,
`docs/API.md`, `docs/openapi.yaml` or `tests/test_api_first.py`; other workers
are editing those. Never git commit.

## Done means

`manage.py test tests.test_tour tests.test_showcase tests.test_bootstrap` passes;
strict schema validation clean; `manage.py check` and `makemigrations --check`
clean. Finish with SUMMARY / FILES / TESTS / GAPS as `AGENTS.md` says.
