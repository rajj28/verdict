# Packet P3-OPS: API-first artifacts, CI, lint, load test, outbox email

Requires T1+T2 merged. Read BUILD-SPEC 16, 17 and AGENTS.md.

## Do
1. API First bonus:
   - Every API view has drf-spectacular annotations (request/response serializers, error envelope responses 400/401/403/404/409/429, tags per area, operation summaries). `python manage.py spectacular --validate --fail-on-warn --file docs/openapi.yaml` passes and the file is committed.
   - `docs/API.md`: auth (session vs `Authorization: Bearer`, creating a token at /me/tokens), error envelope and codes table, pagination, a table mapping **every UI action** (page + button) to its endpoint, and curl examples for the lifecycle (create event → register → team → submit → assign → review → publish → export).
   - `tests/test_api_first.py`: parse every template for `data-api-url` values and assert each path pattern exists in the generated schema; assert the schema lists every URL under `/api/v1/`.
2. `pyproject.toml` with ruff lint (E, F, I, B, UP, DJ, S with sensible ignores) + ruff format; run `ruff check --fix` and `ruff format` over src/ and tests/ (behaviour-preserving only; tests must stay green).
3. `.github/workflows/ci.yml`: Python 3.12, `pip install -r requirements.txt ruff`, `ruff check`, `ruff format --check`, `python manage.py test tests`, then `docker compose up -d --wait`, `python run.py .dogfood.toml | tee report.txt`, fail unless the report shows 7 PASS; upload report as artifact.
4. `scripts/loadtest.py` (stdlib only): args base URL, users (50), duration (60 s), tokens from the demo banner; mix gallery pages, project detail, judge queue, review draft saves; prints p50/p95/p99 per route and errors. Run it against docker once and put the numbers (with machine specs) in README "Operations".
5. Outbox: `core.mail.OutboxBackend` storing messages in an `OutboxMessage` model (to, subject, body, created_at, related event); settings use SMTP when `EMAIL_HOST` is set, else Outbox. Judge invites, team invites (optional "email this link"), password reset links and "results published" notices are sent through Django's `send_mail`. Page `/manage/{slug}/outbox` (organizer: messages for their event) and `/admin-panel/outbox` (admin: all).

## Files you own
pyproject.toml, .github/**, scripts/**, docs/openapi.yaml, docs/API.md, src/core/mail.py (+ model/migration in core), outbox templates/views, tests/test_api_first.py, tests/test_outbox.py, schema annotations in */api.py (annotations only; no behaviour changes).
