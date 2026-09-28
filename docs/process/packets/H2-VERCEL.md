# H2: Fast, free hosted copy on Vercel (Hobby) + Neon Postgres

Read `AGENTS.md`, `src/verdict/settings.py`, `src/interop/signing.py`,
`src/core/management/commands/runportal.py`, `docs/TOUR.md` first. Replaces
`H1-HOSTED.md` (Hugging Face Docker Spaces became paid in July 2026).

## Why

Judges should be able to click a URL and try VERDICT without installing anything,
and it must be fast. Vercel's Hobby plan (free, no card) runs Python serverless
functions with no whole-app sleep; Neon (free, no card) provides PostgreSQL. The
official product stays `docker compose up` with the network off: this is a
**separate copy** for hosting, and nothing here may change the official
Dockerfile, compose file, `.dogfood.toml` or any default behaviour.

## Research first (use webfetch)

Read Vercel's current Python runtime documentation (how a WSGI/Django app is
exposed, `vercel.json` format, Python version, function max duration and bundle
size on Hobby, static files, build command support) and Neon's connection
guidance for serverless Django (pooled vs direct URL, `sslmode`, `CONN_MAX_AGE`).
Follow what the docs say today; note the URLs you used in `docs/HOSTING-VERCEL.md`.

## Deliverables

1. `deploy/vercel/` with everything Vercel needs (entry module exposing the Django
   WSGI app with `src/` on `sys.path`, `vercel.json`, any build hook for
   `collectstatic`), plus `deploy/vercel/make_copy.py` (standard library): builds a
   ready-to-deploy folder (default `../verdict-vercel`) from `git archive HEAD` with
   the Vercel files placed where Vercel expects them. It never deploys and prints
   the next manual steps.
2. Hosted-mode switches, **environment only, defaults unchanged**, each tested:
   - `VERDICT_HOSTED=1` turns on the hosted profile: writable data dir under
     `/tmp`, `CONN_MAX_AGE=0`, secure proxy header, secure cookies,
     `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` from `HOSTED_ORIGIN` (default
     `.vercel.app`), outbound webhooks refused with a clear 403 message, image
     uploads refused with a clear 403 message.
   - `SECRET_KEY` must come from the environment in hosted mode (fail fast
     otherwise).
   - Ed25519 signing key from an env var (for example `VERDICT_SIGNING_KEY`, base64
     private key) when set, so every serverless instance signs with the same key.
     Add a tiny management command that prints a fresh key for the operator.
   Add the smallest code changes needed in settings, signing, webhook and upload
   services; everything off unless the env var is set.
3. One-time database setup documented and scripted: the operator runs
   `migrate` and `bootstrap` (DEMO_MODE=1) against the Neon URL, either locally or
   through a documented build step. Seeding must be idempotent.
4. Tour housekeeping on serverless: sandboxes prune lazily on start (already);
   optionally a Vercel Cron route protected by `CRON_SECRET` that calls the prune
   service. Document the Hobby cron limits.
5. `docs/HOSTING-VERCEL.md`: create Neon project (region close to Vercel's
   `iad1`), create Vercel project with the CLI (`npx vercel login`, `npx vercel
   deploy --prod` from the copy folder), set env vars with `npx vercel env add`
   (the operator types secrets; never commit them), run the one-time setup, what is
   disabled in hosted mode and why, limits (function duration, bundle size, Neon
   free storage and autosuspend), and how to take it down.

## Tests

- `tests/test_hosted_settings.py`: each switch off by default; on with
  `VERDICT_HOSTED=1` (use subprocess or settings reload helpers as the codebase
  already does for env-driven settings); hosted mode without `SECRET_KEY` fails
  fast; signing key from env is used and stable; webhook creation and image upload
  refused with 403 in hosted mode and unchanged otherwise.
- `tests/test_vercel_copy.py` (plain unittest): `make_copy.py` in a temp dir
  produces the expected layout, no `.git`, no logs or local data, and the entry
  module imports the WSGI application.
- Do not deploy anything and do not run Docker.

## Scope

New files under `deploy/vercel/`, `docs/HOSTING-VERCEL.md`, the two test modules,
and minimal, env-gated changes in `src/verdict/settings.py`,
`src/interop/signing.py` (+ a key-print command), the webhook creation service and
the image upload service. Do not touch the official `Dockerfile`,
`docker-compose.yml`, `.dogfood.toml`, `run.py`, `src/core/tour.py`,
`src/core/calibration_views.py`, `docs/API.md`, `docs/openapi.yaml` or
`tests/test_api_first.py` (another worker is editing those). Never git commit.

## Done means

`manage.py test tests.test_hosted_settings tests.test_t4 tests.test_projects` and
`python -m unittest tests.test_vercel_copy` pass; `manage.py check` clean. Finish
with SUMMARY / FILES / TESTS / GAPS as `AGENTS.md` says.
