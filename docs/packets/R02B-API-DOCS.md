# R02B: API First, finished (docs/API.md, parity test, committed schema)

Read `AGENTS.md` first. PostgreSQL only; use the `DATABASE_URL` you are given.

## Why

The API First bonus reads: "Every UI action available through a documented API
with an OpenAPI spec." The schema now validates strictly (`manage.py spectacular
--validate --fail-on-warn` = 0 errors, 0 warnings). What is missing is the proof
that *every* UI action maps to a documented operation, and the human
documentation. A judge must be able to verify the claim in two minutes.

## Deliverables

### 1. `docs/openapi.yaml`

Generated, never hand-edited: `.venv\Scripts\python.exe manage.py spectacular
--file docs/openapi.yaml --validate --fail-on-warn`. Commit-ready (LF endings).

### 2. `tests/test_api_first.py`

1. Strict schema: generate the schema in-process (drf-spectacular's generator,
   the same settings `manage.py spectacular` uses) and assert zero errors and zero
   warnings.
2. Drift: `docs/openapi.yaml` equals the freshly generated schema (compare
   parsed data, not bytes; the failure message says to run the command above).
   YAML parsing: drf-spectacular already depends on PyYAML, use it.
3. UI parity: scan every template under `src/templates/` for
   `data-api-url="..."` (with its `data-api-method`, default POST as
   `api-forms.js` does) and every file under `src/static/js/` for `/api/v1/`
   URL literals and their HTTP methods. Normalise template variables and JS
   concatenations to path parameters (`{{ event.slug }}` -> `{slug}`, etc.).
   Assert every (method, path) pair matches an operation in the schema. Keep an
   explicit, commented allow-list only for pairs that are not API calls (there
   should be none or almost none), and make the test print the full unmatched
   list on failure.
4. Completeness: every operation has an `operationId`, a summary or
   description, at least one 2xx response with a schema, and its documented
   error responses use the shared error envelope component.
5. Docs coverage: every `operationId` in the schema appears in `docs/API.md`
   (so the prose cannot silently fall behind the code).

### 3. `docs/API.md`

- How to explore: `/api/schema` (OpenAPI 3), `/api/docs` (Swagger UI, served
  offline via drf-spectacular-sidecar), `docs/openapi.yaml`.
- Authentication: browser session + CSRF header for the UI; bearer tokens
  (`Authorization: Bearer <token>`) for scripts, how an organizer creates and
  revokes one, the demo tokens in `.dogfood.toml` (DEMO_MODE only).
- Conventions: `public_id`/`slug` only, never integer ids; the error envelope
  `{"error": {"code", "message", "fields"}}` with the status codes the code
  actually returns (400/401/403/404/409/429); pagination format; time and
  window rules (server time, half-open windows); rate limits; webhooks
  (events, signature header, retries) if implemented.
- A table: every operation grouped by area (accounts, events, teams, projects,
  judging, pairwise, community voting, results, exports, webhooks,
  certificates, audit), columns: operationId, method + path, who may call it,
  the UI control that uses it (template or JS file), one-line purpose.
- A full lifecycle with `curl` (bash) using the demo tokens and the demo
  events: create an event, open submissions, register, create a team, submit a
  project, assign judges, score, close judging, preview results, publish,
  verify the publication, fetch a certificate, export CSV. Every command must
  be one you ran against a real server (use Django's `LiveServerTestCase` or a
  throwaway `manage.py runserver` in the background that you stop afterwards)
  and show the real status codes; do not invent responses.
- A short "How we keep this true" section naming `tests/test_api_first.py`.

Another worker is adding one endpoint right now
(`POST /api/v1/events/<slug>/results/consequences`) and new data-api controls
in `src/templates/manage/results.html` and `src/static/js/consequences.js`. If
those exist when you run, include them; if they do not, do not wait for them.

## Scope

Touch only `docs/openapi.yaml`, `docs/API.md`, `tests/test_api_first.py`. If a
UI action truly has no API operation, do not add code: list it under GAPS. Do
not edit `src/**` (other workers are editing it). Never git commit.

## Done means

`manage.py test tests.test_api_first` passes. Finish with SUMMARY / FILES /
TESTS / GAPS as `AGENTS.md` says.
