# VERDICT

A self-hosted hackathon submission and judging portal: teams submit projects,
judges score them under assignment and conflict rules, organizers publish
ranked awards with a verifiable record. Django 5.2 + DRF + PostgreSQL 16,
server-rendered templates, no build step, no required network at runtime.

The scoring core (`src/results/engine.py`, standard library only) fits an
additive judge-offset model with cross-validated shrinkage, and every number
below names the command that produces it.

## Quick start

```powershell
docker compose up --build --wait
# open http://localhost:8080 (port from VERDICT_PORT in .env.example)
python run.py .dogfood.toml
```

`docker compose up` starts `db` (postgres:16-alpine), `web` (gunicorn via
`python manage.py runportal`, which waits for the DB, migrates, then seeds),
and `webhook-worker` (`python manage.py deliver_webhooks --workers 2`).
See `docker-compose.yml` and `Dockerfile`. First boot prints demo credentials
from `src/core/bootstrap.py`; the seed is idempotent (`python manage.py
bootstrap` re-runs safely and re-activates demo tokens).

`run.py` is the organizer acceptance checker (stdlib only, `python run.py
.dogfood.toml`), not the app entrypoint. It checks 7 conditions against
`.dogfood.toml` routes and prints `claimed ... verified ...`.

## Demo logins

Seeded by `src/core/bootstrap.py` into two events: the closed fixture event
`sample-hack-2026` (imported from `fixtures.json`: 41 projects, 126 scores,
30 judges — count with
`python -c "import json;d=json.load(open('fixtures.json'));print(len(d['projects']),len(d['scores']),len(d['judges']))"`)
and the open practice event `demo-hack`. Password for every demo account is
`verdict-demo`; bearer tokens live in `.dogfood.toml` (`[auth]`).

| Role | Email | Sees |
|---|---|---|
| Organizer | `organizer@verdict.local` | Manage pages, Command Center, publish |
| Judge A | `diego.herrera@example.org` | Own queue at `/judge`, own scores only |
| Judge B | `jonas.vogel@example.org` | Same, cannot see Judge A's scores |
| Participant | `priya1@example.org` | Own team submission, public gallery |

Demo shortcuts exist only while `DEMO_MODE=1` (`POST /api/v1/auth/demo-login`,
`src/accounts/api.py`); setting `DEMO_MODE=0` retires demo passwords and
tokens (`_retire_demo_credentials` in `src/core/bootstrap.py`).

## Reviewer's map

| Concern | Start here | Confirm with |
|---|---|---|
| Security boundaries | `THREAT-MODEL.md`, `docs/BUILD-SPEC.md` sec 3 | `python scripts/attack.py .dogfood.toml` → `attack-report.txt` (28/28 + 14/14 direct) |
| DevOps / boot / health | `docker-compose.yml`, `Dockerfile`, `src/core/management/commands/runportal.py` | `docker compose up --build --wait`, `GET /healthz` |
| Data / constraints | `DATA-MODEL.md`, one `models.py` per app under `src/` | `.venv\Scripts\python.exe scripts/gate.py` (migrations check; last recorded full gate 2026-09-28: 987 Django + 57 engine tests, see `docs/ADVERSARIAL-INTEGRATION-20260927.md`) |
| Judging maths | `JUDGING.md`, `docs/NORMALIZATION-PROOF.md`, `src/results/engine.py` | `.venv\Scripts\python.exe scripts/normalization_proof.py` regenerates the proof |
| UX per role | `src/templates/projects/gallery.html`, `src/templates/judge/queue.html`, `src/templates/judge/review.html`, `src/templates/manage/command_center.html`, `src/templates/manage/decision_room.html` | Log in as each demo role above |
| Race / privacy fixes | `docs/ADVERSARIAL-T1-RACES.md`, `docs/ADVERSARIAL-T2-PRIVACY.md` | `manage.py test tests.test_adversarial_t1 tests.test_adversarial_t2` |
| Tier status | `docs/TIER-EVALUATION-20260927.md`, `.dogfood.toml` | `python scripts/verify_tiers.py .dogfood.toml` → `tier-evidence-report.txt` (25/25) |
| API surface | `/api/schema`, `/api/docs` (drf-spectacular) | `python run.py .dogfood.toml` → `acceptance-report.txt` (7/7) |

## Tiers claimed and how to verify

`.dogfood.toml` claims `["T1", "T2"]` — nothing higher.

- T1/T2 by machine: `python run.py .dogfood.toml` (7/7 expected) and
  `python scripts/verify_tiers.py .dogfood.toml` (25/25 expected). Both exit
  0 even with failures, so read the printed totals, not the exit code.
- T3 (community voting) by hand: as organizer open `demo-hack` voting
  (`src/templates/manage/voting.html`), cast ballots in each access mode via
  `src/templates/community/ballot.html`, confirm tallies stay hidden until
  `voting_close_at` (`src/templates/community/results.html`) and that a second
  ballot from the same identity returns 409. Open-link device/email gates
  deter casual repeats; they do not establish one-human-one-vote.
- T4 (API First) by hand: fetch `/api/schema` and map each button in
  `src/static/js/api-forms.js` (`data-api-url`) to an operation; several UI
  actions lack typed schema metadata, so a complete claim is not made.
- Do not claim T3/T4 or any bonus until the manual matrix in
  `docs/TIER-EVALUATION-20260927.md` is signed off on the submitted build.

## Operations

Env vars are listed in `.env.example`; Compose injects them in
`docker-compose.yml`. `DATABASE_URL` must be PostgreSQL
(`src/verdict/settings.py` rejects anything else, including SQLite, and fails
fast when unset). `SECRET_KEY` unset means a key is generated into
`/data/secret_key` (the `appdata` volume); losing it invalidates sessions and
HMAC certificate links (`src/interop/certificates.py`). `WEBHOOKS_ALLOW_PRIVATE`
stays `0` in production. Blank `EMAIL_HOST` (or `DEMO_MODE=1`) keeps mail in
the organizer outbox (`src/templates/core/outbox.html`) instead of SMTP.

Production mode: `DEMO_MODE=0`, `ALLOWED_HOSTS` set, `ADMIN_EMAIL` /
`ADMIN_PASSWORD` set, `SECRET_KEY` set from a vault. No backup command ships:
back up Postgres (`pgdata` volume, e.g. `pg_dump`) plus the `appdata` volume
(media, secret key, Ed25519 keys in `DATA_DIR/keys` via
`src/interop/signing.py`). Portable exports (`/api/v1/exports/event.json`,
per-kind CSVs in `src/interop/exports.py`) are not backups: they exclude
credentials, keys, and audit hashes. Restoring means a fresh Compose stack, a
DB restore, and re-running `python manage.py verify_publication <pub_id>`
(`src/results/management/commands/verify_publication.py`).

## Honest limitations

- Publication verification reads the live project roster, so adding, deleting
  or re-drafting a project after publish reports `differs`; there is no
  historical roster snapshot (see `docs/ADVERSARIAL-INTEGRATION-20260927.md`).
- Normalization removes linear judge level habits only; scale habits,
  strategic bias and collusion are not modelled (`JUDGING.md`, Limitations).
- The review-budget planner is an explicitly labelled experiment with assumed
  noise, not a promise of detection rates.
- OpenAPI strict validation has known failures (306 errors at last count in
  `docs/TIER-EVALUATION-20260927.md`); T4 is partial.
- Performance evidence is a 3-request smoke sample, not a benchmark (same doc).

## License and third-party licenses

`LICENSE`: MIT, (c) 2026 VERDICT contributors. Pinned runtime deps in
`requirements.txt` (9): Django 5.2.17, djangorestframework 3.16.1,
drf-spectacular 0.30.0, drf-spectacular-sidecar 2026.9.1, psycopg[binary]
3.3.6, gunicorn 23.0.0, whitenoise 6.12.0, Pillow 12.3.0, cryptography 46.0.5.
Vendored assets under `src/static/vendor/` (Bootstrap, MIT) and
`src/static/fonts/` (Geist + Instrument Serif, see `src/static/fonts/LICENSE-Geist.txt`
them). No CDN, web font, or runtime network call: API docs ship via
drf-spectacular-sidecar and the CSP in `src/core/middleware.py` allows
`script-src 'self'` only.
