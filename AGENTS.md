# Instructions for AI coding agents working on VERDICT

You are a worker on VERDICT, a hackathon submission/judging portal (Django 5.2 + DRF + PostgreSQL, server-rendered templates + Bootstrap, no build step). An orchestrator plans, reviews and commits. You implement one task packet at a time.

## Read first
1. `docs/process/BUILD-SPEC.md`: the authoritative spec (data model, permissions, API contract, engine math). Follow it exactly. If it is ambiguous, choose the simplest reading and say so in your final summary.
2. Your packet file in `docs/process/packets/`. Touch only the files and directories it lists. If you must change anything else, keep it minimal and list it in your summary.

## Environment
- Windows host. Use the repo virtualenv: `.venv\Scripts\python.exe` (create it with `python -m venv .venv` and `.venv\Scripts\python.exe -m pip install -r requirements.txt` if missing).
- Application code lives in `src/` (Django apps, `src/templates/`, `src/static/`); `manage.py` is at the repo root and adds `src/` to `sys.path`. Tests live in the top-level `tests/` package, one `test_<area>.py` module per area; only edit the test modules your packet names.
- Run tests with `.venv\Scripts\python.exe manage.py test tests -v 1` (or one module: `... test tests.test_import`) after setting `DATABASE_URL` to an isolated PostgreSQL database. VERDICT only supports PostgreSQL, everywhere, including tests — there is no SQLite fallback, so a missing/invalid `DATABASE_URL` fails fast at startup. Django's test runner creates and drops its own `test_<name>` database on top of whatever you point at, so never point it at a shared/production database. If Postgres isn't reachable from the host (e.g. the Compose `db` service has no published port), run inside the container instead: `docker compose exec web python manage.py test tests`. Do not start Docker unless your packet says so.
- Write files with LF line endings. No shell scripts. Container entrypoints are Python.

## Rules (from BUILD-SPEC section 2, do not break them)
- All permission, ownership and deadline checks live in `<app>/services.py` (writes) and `<app>/policy.py` (read scoping). Views and API classes stay thin and call them. Never rely on the template hiding something.
- HTML views only read. Every write goes through the JSON API (`/api/v1/...`); forms use `static/js/api-forms.js` (`data-api-method`, `data-api-url`, `data-success`).
- Querysets in views/API/exports always come from `policy.visible_*()` helpers scoped to the caller and event.
- Time comes from `core.clock.now()`. Windows are half-open `[open, close)`.
- Guarded writes: `transaction.atomic()` + `select_for_update()`, re-check rules after locking. Call `audit.services.record(...)` for every state change.
- API errors use the envelope `{"error": {"code", "message", "fields"}}` via `core.errors`. Status codes: 400/401/403/404/409/429 as in the spec.
- Expose `public_id`/`slug`, never integer PKs. Emails are never shown publicly.
- No CDN links, no web fonts, no inline `<script>` blocks or `on*=` attributes (CSP), no runtime network calls.
- No new dependencies. No edits to `run.py` or `fixtures.json`.
- No N+1 queries: list views, exports and dashboards use `select_related`/`prefetch_related`; add `assertNumQueries` tests for list endpoints you build.
- Every rule you implement gets a test (allowed case and denied case). Tests must pass before you finish.
- Do not run `git commit`, `git push`, `git reset`, or delete files outside your packet scope. The orchestrator commits.

## Code style
Idiomatic Django: fat services, thin views, explicit querysets, `TextChoices` for enums, `UniqueConstraint`/`CheckConstraint` in `Meta.constraints`, type hints on service functions, short docstrings that say why. Keep functions small. No dead code, no commented-out code, no TODO placeholders pretending to be features.

## Finish with exactly this summary
```
SUMMARY: <2-4 lines of what now works>
FILES: <created/modified paths>
TESTS: <exact command> -> <final result line, e.g. "Ran 42 tests ... OK">
GAPS: <anything not done, assumptions made, spec ambiguities>
```
