# Packet P0a: foundation (settings, all models, auth tokens, fixture import, bootstrap)

Goal: a Django project whose database holds the full fixture event plus demo accounts, with every model from BUILD-SPEC section 4 migrated. No HTML pages or API endpoints yet (P0b does those).

Read BUILD-SPEC sections 1, 2, 4, 8 and AGENTS.md before writing code.

## Do
1. `requirements.txt` pinned: Django 5.2.x, djangorestframework, drf-spectacular, drf-spectacular-sidecar, psycopg[binary], gunicorn, whitenoise, Pillow. Pick current versions that install together on Python 3.11 (host) and 3.12 (container). Create `.venv` and install.
2. Project package at `src/verdict/` (settings, urls, wsgi, asgi) and a root `manage.py` that inserts `src/` into `sys.path` (BUILD-SPEC section 1 layout). `BASE_DIR = src/`, `REPO_DIR = BASE_DIR.parent`. Settings from environment: `SECRET_KEY` (env; else read/create `DATA_DIR/secret_key`; `DATA_DIR` env default `REPO_DIR/.data`), `DEBUG` (default False), `ALLOWED_HOSTS` (default localhost,127.0.0.1,0.0.0.0,web + env extra), `DATABASE_URL` (parse postgres://user:pass@host:port/name with urllib.parse; unset → SQLite `DATA_DIR/dev.sqlite3`), `DEMO_MODE` (env "1"/"0", default "0"; compose sets 1), `FIXTURES_PATH` (default `REPO_DIR/fixtures.json`), `MEDIA_ROOT = DATA_DIR/media`, `TIME_ZONE="UTC"`, `USE_TZ=True`, `AUTH_USER_MODEL="accounts.User"`. Do NOT enable `django.contrib.admin`. Sessions: HttpOnly, SameSite=Lax, 7-day age. When running tests use `MD5PasswordHasher`.
3. Apps under `src/` (each with models.py, services.py, policy.py, api.py, views.py, urls.py, api_urls.py; stubs are fine where empty): `core accounts events teams projects judging results audit interop community`. Create `src/templates/` and `src/static/` (empty) and the `tests/` package with `__init__.py`. `verdict/urls.py` includes every app's `urls.py` at "" and every `api_urls.py` under `api/v1/`.
4. `core/clock.py` (`now()`), `core/ids.py` (`new_public_id(prefix)`: prefix + "_" + 10 random lowercase base32 chars), `core/errors.py` (`ApiError(code, message, status=400, fields=None)` + DRF exception handler producing `{"error": {"code","message","fields"}}` for ApiError, ValidationError (code "invalid", fields = DRF detail), NotAuthenticated/AuthenticationFailed (401 "not_authenticated"), PermissionDenied (403 "forbidden"), NotFound (404 "not_found"), Throttled (429 "throttled")), `core/csvutil.py` (writer that prefixes cells starting with = + - @ tab or CR with a single quote).
5. Every model in BUILD-SPEC section 4 with the listed fields, TextChoices, constraints and indexes. Include `community` app with no models yet. Run `makemigrations` and `migrate` cleanly on SQLite.
6. `accounts`: `User` with custom manager (`create_user(email, password=None, **extra)`, `create_superuser` sets is_admin), `ApiToken` + `accounts/tokens.py`: `issue_token(user, name, plaintext=None, is_demo=False) -> (token_obj, plaintext)` (plaintext `vd_` + 40 url-safe chars unless given), `authenticate_token(plaintext) -> User|None` (sha256 lookup, reject revoked/inactive, update last_used_at at most once per minute). DRF authentication class `accounts.auth.BearerTokenAuthentication` reading `Authorization: Bearer <token>`.
7. `audit/services.py`: `record(actor, action, *, event=None, target=None, summary, data=None, request=None)`; target may be any model with `public_id`/`slug`; computes `actor_label` and `ip_hash`.
8. `interop/importer.py`: `import_fixture(data: dict, *, slug=None, actor=None) -> ImportReport` implementing BUILD-SPEC section 8 step 1 exactly (users, roles with public_id = fixture judge id `jdg_NN`, tracks `trk_NN`, teams `tm_NN`, projects `prj_NN`, revisions, duplicate → superseded, assignments + submitted reviews + criterion scores, `scoring_locked_at`, rubric functionality/quality/innovation weight 1 scale 1-5, audit `import.completed` with report data). All in one transaction. Share one precomputed password hash for all seeded users (DEMO_MODE: hash of `verdict-demo`; otherwise unusable password). Must be idempotent at the caller level: bootstrap skips if an Event with that source_id exists.
9. `core/management/commands/bootstrap.py`: BUILD-SPEC section 8 steps 1-5 (import, admin + organizer accounts, organizer EventRole on both events, demo event `demo-hack` with 3 tracks / 2 prizes / 2 questions / 4 weighted criteria, deterministic demo tokens when DEMO_MODE, banner). Safe to run repeatedly.

## Tests (must pass)
Modules: `tests/test_import.py`, `tests/test_bootstrap.py`, `tests/test_tokens.py`, `tests/test_core.py`.
- import: after import: 41 projects, exactly 1 superseded (`prj_07` superseded_by `prj_41`), 30 judge roles, 126 submitted reviews, 378 criterion scores, 40 teams, scoring_locked_at set, report lists jdg_07 as constant scorer and the projects with fewer than 3 reviews.
- bootstrap: bootstrap twice → identical counts; demo tokens authenticate to the right users when DEMO_MODE on and do not exist when off.
- tokens: token issue/authenticate/revoke; wrong token → None.
- core: public id format; csv escaping.

## Parallel worker notice
P2-EN runs at the same time and owns `src/results/engine.py`, `scripts/normalization_proof.py`, `tests/test_engine.py`, `docs/NORMALIZATION-PROOF.md`. Do not create or modify those files (create the rest of the `results` app normally).

## Files you own
Root files (manage.py, requirements.txt, .gitignore), everything under `src/`, and the four test modules above. Not run.py, fixtures.json, AGENTS.md or docs/. No templates or static files yet beyond empty dirs.

## Hardening items (BUILD-SPEC 16) in this packet
- Extra model fields now, so later packets need no migrations: `ProjectRevision.digest` (sha256 hex), `CustomQuestion.is_active` (default True), `ResultPublication.inputs` (JSON canonical input set), `accounts.PasswordResetToken` (user FK CASCADE, token_hash unique, expires_at, used_at nullable, created_by FK nullable).
- Import report stores the fixture sha256 and byte size; revisions created by the importer get their digest.
- Bootstrap re-activates demo tokens on every boot when DEMO_MODE (clear revoked_at, ensure is_active user).
