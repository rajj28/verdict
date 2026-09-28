# H1: Hosted demo copy (separate from the official build)

Read `AGENTS.md`, `docker-compose.yml`, `Dockerfile`, `src/verdict/settings.py`,
`src/core/management/commands/runportal.py` and `docs/TOUR.md` (if present) first.

## Why

Judges should be able to click a link and try VERDICT without installing
anything. The official product stays `docker compose up` with the network off;
this packet adds a **separate** single-container build for free Docker hosts
(Hugging Face Spaces first, any Docker host second). It must not change the
official `Dockerfile`, `docker-compose.yml`, `.dogfood.toml` or runtime defaults.

## Deliverables (all under `deploy/hosted/`)

1. `Dockerfile`: based on `python:3.12-slim`; installs the Debian PostgreSQL
   server package; installs Python dependencies from `vendor/wheels` with
   `pip --no-index` (same wheels as the official build); copies the app; runs as
   a non-root user with uid 1000 (Hugging Face requirement); exposes one port
   from `PORT` (default 7860).
2. `start.py` (standard library only, the container entrypoint):
   - initialises a PostgreSQL cluster in a writable directory on first start and
     starts it on localhost only;
   - creates the database and user;
   - runs `migrate` and `bootstrap` with `DEMO_MODE=1`;
   - starts gunicorn on `0.0.0.0:$PORT`;
   - every `RESET_HOURS` (default 6), runs the tour sandbox prune command if it
     exists;
   - shuts PostgreSQL down cleanly on SIGTERM.
   Data is intentionally ephemeral: a restarted Space is a fresh demo.
3. Hosted-mode settings through environment only (no edits to defaults):
   `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` for `*.hf.space` (or `HOSTED_ORIGIN`),
   secure cookies behind the proxy (`SECURE_PROXY_SSL_HEADER`), outbound webhooks
   off, image uploads off or capped hard, email to the outbox only. If the settings
   module lacks an env switch for one of these, add the smallest env-driven switch
   with a default that keeps today's behaviour, and test it.
4. `README.md` for the Space: Hugging Face front matter (`sdk: docker`,
   `app_port: 7860`, title, emoji, short description), what the demo is, that data
   resets, a link back to the source repository placeholder `<REPO_URL>`, and that
   the official way to run VERDICT is `docker compose up`.
5. `make_space.py` (standard library): builds a ready-to-push folder
   (`../verdict-space` by default) from `git archive HEAD` plus the files in
   `deploy/hosted/` placed where Hugging Face expects them (`Dockerfile` and
   `README.md` at the root). It never pushes anything and prints the next manual
   steps. Exclude `.git`, local data, logs and anything in `.dockerignore`.
6. `deploy/hosted/HOSTING.md`: how to create the Space (private first), push the
   folder, set it public when the repository is public, what is disabled in
   hosted mode and why, how resets work, and how to run the same container
   locally (`docker build -f deploy/hosted/Dockerfile -t verdict-hosted .` then
   `docker run -p 7860:7860 verdict-hosted`).

## Tests

- `tests/test_hosted_settings.py`: the env switches you added (defaults unchanged;
  hosted values applied).
- `tests/test_make_space.py` (plain unittest): `make_space.py` in a temp directory
  produces the expected layout, no `.git`, no logs, and the Dockerfile at the root.
- Do not run Docker. The orchestrator builds and runs the image.

## Scope

New files under `deploy/hosted/`, the two test modules, and at most a minimal,
tested env switch in `src/verdict/settings.py` (and wherever an upload or webhook
toggle must be read). Do not touch the official `Dockerfile`,
`docker-compose.yml`, `.dogfood.toml`, `run.py`, `src/core/tour*`,
`src/core/showcase.py`, `src/results/**` or docs outside `deploy/hosted/`. Never
git commit.

## Done means

`manage.py test tests.test_hosted_settings` and `python -m unittest
tests.test_make_space` pass; `manage.py check` clean. Finish with SUMMARY / FILES
/ TESTS / GAPS as `AGENTS.md` says.
