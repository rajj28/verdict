# Packet P1-EV: events, tracks, prizes, custom questions, registration, organizer setup pages

Read AGENTS.md and BUILD-SPEC sections 2, 3, 4 (events.*), 5, 6 (Events), 12. Templates extend `base.html` and use the UI kit classes (P1-UI builds them in parallel; use the class names exactly as the spec lists them) and `api-forms.js` data attributes for every write.

## Do
1. `src/events/services.py`: `create_event(actor, data)` (admin or is_host; creator gets organizer EventRole; auto slug from name, unique), `update_event(actor, event, data)` (organizer/admin; validates windows per BUILD-SPEC 4; after `scoring_locked_at`: submission window changes → 409 `scoring_locked`; ranking_method / shrinkage_lambda changes → 409), `close_judging(actor, event)`, track/prize/question create/update/delete (organizer; deleting a track used by a project → 409 `track_in_use`), `register_participant(actor, event)` (window open, else 403 `window_closed`; existing other role → 409 `role_conflict`; idempotent for participants), `add_organizer(actor, event, email)` (existing user; role conflict → 409). Audit each with a human summary that includes old → new values for window changes.
2. `src/events/policy.py`: extend with `visible_events(user)` (all events), `can_manage(user, event)`.
3. `src/events/api.py` + `api_urls.py`: endpoints from BUILD-SPEC 6 Events. Serializers expose slug/public_id only, ISO UTC datetimes, computed `phase` (upcoming | submissions_open | judging | results_published) and `submission_window_open`.
4. Pages (`src/events/views.py`, `urls.py`, `src/templates/events/**`):
   - `/events/` list with phase badges and countdowns.
   - `/events/{slug}/` public event page: name, tagline, description, timeline (open/close times as `<time data-countdown>`), tracks, prizes, links to gallery (filtered to event) and results (if published); role-aware call to action: Register (visitor → login), Create/Join team + Edit submission (participant, while open), Judge console (judge), Manage (organizer).
   - `/events/new` create form (host/admin; others see 403 page).
   - `/manage/{slug}/` organizer overview: phase checklist (event configured, tracks, rubric, judges invited, assignments, judging progress, results published) with done/pending states from real data, stat tiles (participants, teams, submitted projects, drafts, judges, reviews submitted), data issues panel (superseded duplicates, under-reviewed projects, constant scorers, from the import report and live queries), quick links to every manage page.
   - `/manage/{slug}/settings`: edit name/tagline/description/windows/max team size/reviews per project/gallery_public/judging_mode/ranking_method/shrinkage_lambda (locked fields disabled with an explanation once scoring is locked), "Close judging now" button.
   - `/manage/{slug}/setup`: tracks, prizes, custom questions editors (add/edit/delete inline, reorder by position number).
   All manage pages: 403 for non-organizers (enforced in the view via policy, not just hidden links).

## Tests
`tests/test_events.py`: create event as host ok / as plain user 403 / anonymous 401; slug uniqueness; window validation errors (close before open, judging before submission close); organizer-only update (participant 403, other event's organizer 403); scoring-locked changes → 409 on the fixture event; register: open → 201, closed fixture event → 403 window_closed, judge registering → 409 role_conflict; track delete in use → 409; manage pages 403 for judge/participant; audit rows written.

## Files you own
src/events/** (except models.py unless a migration is truly needed), src/templates/events/**, src/templates/manage/overview.html, settings.html, setup.html, tests/test_events.py.

## Hardening items (BUILD-SPEC 16) in this packet
- `POST /events/{slug}/close-submissions` (organizer; while open, before scoring lock; sets close + judging_open to now; audited) and a "Close submissions now" button on settings/overview.
- Co-organizers: list/add/remove; last organizer cannot be removed (409 `last_organizer`).
- Question with answers cannot be deleted (409 `question_in_use`); `is_active` toggle instead (inactive questions hidden from new submissions, answers kept).
- `prize.track` must belong to the same event (400 `cross_event`).
Add tests for each in tests/test_events.py.
