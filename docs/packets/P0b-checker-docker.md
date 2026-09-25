# Packet P0b: acceptance path + Docker (goal: run.py 7/7 PASS from `docker compose up`)

P0a is done: models, importer, bootstrap, bearer tokens exist. Read BUILD-SPEC sections 2, 3, 5, 6, 7, 8, 13 and AGENTS.md.

## Do
1. DRF settings: authentication = [SessionAuthentication, accounts.auth.BearerTokenAuthentication]; default permission IsAuthenticatedOrReadOnly is NOT enough: every view declares its own; exception handler `core.errors`; throttles anon 120/min user 1200/min; pagination page_size 50 max 100. drf-spectacular: `/api/schema/` and `/api/docs/` (Swagger UI served from drf-spectacular-sidecar, no CDN).
2. `src/events/policy.py`: `role_of(user, event) -> str|None` ("admin" for is_admin users, else EventRole.role), `is_organizer(user, event)` (organizer role or admin), `judge_role(user, event)`, `submission_window_open(event, at=None)`, `judging_window_open(event, at=None)`.
3. `src/projects/services.py` `create_project(actor, event, data)`: order of checks: authenticated → event exists → **submission window open, else ApiError("window_closed", "Submissions for {name} closed at {iso}.", 403)** → caller is a participant with a team in the event (403 "not_a_participant") → team has no active project (409 "team_has_project") → validate title/summary lengths (400) → create draft, audit. API `POST /api/v1/events/{slug}/projects` returns 201 with the project (public_id, title, summary, status). `GET` on the same URL lists submitted projects (public).
4. `src/judging/policy.py` + API:
   - `GET /api/v1/judge/scores`: 401 anonymous; 403 `not_a_judge` if the user has no judge EventRole anywhere; if `?judge=` is present and differs from every judge public_id the caller holds → 403 `forbidden`; else 200 `{"judge": {...}, "reviews": [ {event, project{public_id,title}, status, criteria{key:value}, weighted_score, comment, submitted_at} ]}` for the caller's own reviews only.
   - `GET /api/v1/events/{slug}/judges/{jdg_id}/scores`: 200 for that judge's own user and for organizers/admins of the event; 403 for everyone else authenticated (including other judges); 401 anonymous; 404 unknown judge id (only after the permission check passes for organizers).
5. `src/interop/exports.py` + API `GET /api/v1/events/{slug}/exports/{kind}.csv` for kinds `reviews` and `results` (organizer/admin only; 403 otherwise; 404 unknown kind). results.csv columns: rank,project_id,title,team,track,reviews,raw_mean (0-100 weighted, BUILD-SPEC 9), status. Excludes superseded/withdrawn/disqualified/draft projects. reviews.csv: review_id,judge_id,judge_name,project_id,project_title,track,status, one column per criterion key, weighted_score, comment. Use `core.csvutil`. Content-Type `text/csv; charset=utf-8`, Content-Disposition attachment.
6. Minimal HTML gallery `GET /projects` (anonymous OK): server-rendered list of submitted, non-superseded projects in events with gallery_public, sorted by title, 48 per page, `?q=` search on title/summary, `?track=` filter; each item shows title, summary, team name, track, event. Temporary minimal `templates/base.html` with blocks `title`, `content`, `page_actions`, `extra_js` (P1 restyles it). `/healthz` returns `{"ok": true}` after a DB query.
7. `src/core/middleware.py` security headers + CSP from BUILD-SPEC 13; whitenoise middleware; media serving view for `/media/`.
8. `src/core/management/commands/runportal.py`: wait for DB (retry up to 60 s), migrate, bootstrap, then `os.execvp("gunicorn", ["gunicorn","--pythonpath","src","-b","0.0.0.0:8080","-w","3","--access-logfile","-","verdict.wsgi"])`.
9. `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.gitignore` (.venv, .data, __pycache__, *.sqlite3, media), `.gitattributes` (`* text=auto eol=lf`) per BUILD-SPEC 13. Compose file must work with plain `docker compose up`.
10. `.dogfood.toml` exactly as BUILD-SPEC section 7.

## Tests (must pass)
`tests/test_acceptance_parity.py`: with the fixture imported and DEMO_MODE on, use the Django test client with the demo bearer headers to replay run.py's seven checks (gallery 200 anonymous; a fixture title in the body; participant POST → 4xx and specifically 403 window_closed; judge_a own scores 200 with non-empty reviews; judge_b → judge_a url 403; participant → judge scores 403; organizer results.csv 200 with a comma in line 1). Plus: judge_b `?judge=jdg_24` → 403; anonymous → 401; organizer → judge A scores 200; open demo event POST by a participant without a team → 403 not_a_participant.

## Then verify for real
`docker compose up --build -d`, wait for `/healthz`, then `python run.py .dogfood.toml` from the repo root. Paste the full output in your summary. Fix until 7/7 PASS. Leave the stack running.

## Files you own
src/verdict/settings.py, src/verdict/urls.py, src/core/*, src/events/policy.py, src/projects/{services,policy,api,api_urls,views,urls}.py, src/judging/{policy,api,api_urls}.py, src/interop/{exports,api,api_urls}.py, src/templates/base.html, src/templates/projects/gallery.html, Dockerfile, docker-compose.yml, .dockerignore, .gitignore, .gitattributes, .dogfood.toml, tests listed above.

## Hardening items (BUILD-SPEC 16) in this packet
- API routes without trailing slashes; `/projects` exact; no `format_suffix_patterns`; exports return `HttpResponse`. Parity tests use `follow=False` and assert exact codes (a redirect on the submit route would silently turn run.py's POST into a GET).
- DRF auth order: BearerTokenAuthentication first with `authenticate_header` → anonymous API = 401 + `WWW-Authenticate: Bearer realm="api"`.
- Media view with authorization (public projects public; otherwise team, organizers, assigned judges; else 404).
- gunicorn access log format without query strings/referrers: `%(h)s %(t)s "%(m)s %(U)s" %(s)s %(b)s %(L)s`.
- DEMO_MODE → ALLOWED_HOSTS ["*"] unless set; compose port `"${VERDICT_PORT:-8080}:8080"`; `.env.example`.

- Compose services: `restart: unless-stopped`, healthchecks on db and web, named volumes only.
