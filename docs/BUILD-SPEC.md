# VERDICT build spec (authoritative for implementation)

Product: **VERDICT**, a self-hostable hackathon portal whose results you can defend.
Pitch (for .dogfood.toml): "A one-command hackathon portal where deadlines, judge isolation and score normalization are enforced in the backend and explained to organizers."

This file is the contract every worker implements. If code and this file disagree, raise it; do not improvise.
Scoring priorities (from dogfoodhack.com): Tier completion 40% (acceptance suite), Judging integrity 25%, Adoptability/operability 20%, Code quality 15%. Correctness beats breadth.

---

## 1. Stack, versions, layout

- Python 3.12 (container), Django 5.2.x LTS, djangorestframework 3.16+, drf-spectacular 0.30 + drf-spectacular-sidecar (offline Swagger UI), psycopg[binary] 3.x, gunicorn, whitenoise, Pillow. Pin exact versions in `requirements.txt` at kickoff.
- PostgreSQL 16 everywhere: Docker, local dev and tests. `DATABASE_URL` (postgres:// or postgresql://) is required; Django fails fast at startup if it is missing or uses any other scheme, including sqlite://. There is no SQLite fallback.
- Frontend: Django templates + vendored Bootstrap 5.3 (css + bundle js, MIT, committed under `static/vendor/bootstrap/` with its LICENSE) + our `static/css/app.css` + small vanilla JS files. **No build step, no CDN, no web fonts, no inline `<script>` blocks or `on*=` handlers** (CSP forbids them).
- Tests: Django test runner (`python manage.py test`) against an isolated PostgreSQL test database reachable via `DATABASE_URL`; the runner creates/drops its own `test_<name>` database. Tests use MD5 password hasher for speed.

Layout follows the organizers' suggested repo shape (context.txt section 7: `src/` = all code written during the window, `tests/` = our own suite beyond the acceptance one). `manage.py` stays at the root and inserts `src/` into `sys.path`; gunicorn runs with `--pythonpath src`. Settings: `BASE_DIR = src/`, `REPO_DIR = BASE_DIR.parent`, `FIXTURES_PATH` default `REPO_DIR/fixtures.json`, `DATA_DIR` default `REPO_DIR/.data`.

```
repo/
  manage.py  requirements.txt  Dockerfile  docker-compose.yml  .dockerignore  .gitignore  .gitattributes
  LICENSE (MIT)  README.md  ARCHITECTURE.md  DATA-MODEL.md  JUDGING.md
  .dogfood.toml  acceptance-report.txt  run.py (organizer file, unchanged)  fixtures.json (unchanged)
  docs/               THREAT-MODEL.md API.md NORMALIZATION-PROOF.md BUILD-SPEC.md packets/
  tests/              our suite: test_<area>.py modules (package with __init__.py); run `python manage.py test tests`
src/ (all application code):
  verdict/            settings.py urls.py wsgi.py asgi.py
  core/               clock.py ids.py errors.py (DRF exception handler) csvutil.py middleware.py (security headers, CSP)
                      templatetags/ management/commands/runportal.py bootstrap.py
  accounts/           User, ApiToken, auth (session + bearer), login throttle, demo accounts
  events/             Event, Track, Prize, CustomQuestion, EventRole
  teams/              Team, TeamMember, TeamInvite
  projects/           Project, ProjectRevision, ProjectImage, Answer, gallery
  judging/            Rubric, Criterion, JudgeInvite, Conflict, AssignmentBatch, Assignment, Review, CriterionScore, Comparison, ReviewExclusion
  results/            engine.py (pure functions, no Django imports), ResultPublication, results views, normalization proof command
  community/          (T3) VotingConfig, Ballot, BallotItem, Comment
  audit/              AuditEvent, record(), audit UI
  interop/            fixture import, JSON export/import, CSV exports, (T4) webhooks, signed records
  templates/  static/
```
Each app (under `src/`) has: `models.py`, `services.py` (all writes + business rules), `policy.py` (all read scoping + permission predicates), `api.py` (DRF serializers + views, thin), `views.py` (HTML views, thin), `urls.py` (HTML), `api_urls.py` (API). Templates in `src/templates/`, static files in `src/static/`. Tests live in the top-level `tests/` package, one module per area (`tests/test_import.py`, `tests/test_permissions.py`, ...), so parallel workers own separate test files.

## 2. Engineering rules (non-negotiable)

1. **Backend enforcement.** Every permission and lifecycle rule lives in `services.py`/`policy.py` and is enforced for HTML and API alike. Hiding a button is never the control.
2. **One write path.** UI forms submit to the JSON API through `static/js/api-forms.js`. HTML views only read. (This makes "every UI action has an API" true by construction.)
3. **Read scoping.** Lists, details, counts, search and exports all go through `policy.visible_*()` querysets. Never `Model.objects.all()` in a view.
4. **Time.** Always `core.clock.now()` (wraps `timezone.now()`); tests patch it. Windows are half-open: `open_at <= now < close_at`; `open_at = null` means no lower bound; `close_at = null` means no upper bound.
5. **Atomicity.** Guarded writes run in `transaction.atomic()` with `select_for_update()` on the row being changed; re-check permission + window after taking the lock.
6. **Audit.** Every state change calls `audit.record(actor, action, event, target, summary, data)` inside the same transaction.
7. **Errors.** API errors use one envelope: `{"error": {"code": "window_closed", "message": "Submissions for Sample Hack 2026 closed at 2026-03-01T18:00:00Z.", "fields": {...}}}`. 400 validation, 401 not authenticated, 403 not permitted (role, ownership, window), 404 missing, 409 state conflict (e.g., rubric locked), 429 throttled.
8. **IDs.** Every exposed object has a `public_id` (prefix + 10 lowercase base32 chars, e.g. `prj_k3m9x2qa7d`), unique per event for event-scoped models (UniqueConstraint(event, public_id)). Imported records keep the fixture id as `public_id` (e.g. `prj_01`) and also store `source_id`. Never expose integer PKs. Events use a unique `slug`.
9. **Untrusted content.** Plain text only (rendered escaped, `linebreaksbr`). URLs must be http/https; rendered with `rel="noopener noreferrer nofollow ugc"`. Images: jpeg/png/webp/gif, ≤5 MB, verified with Pillow, random filenames. No SVG uploads.
10. **Privacy.** Emails are never shown publicly. Public pages show display names only.
11. **No new dependencies** beyond section 1 without orchestrator approval.
12. **Idempotent boot.** `docker compose up` repeatedly must never duplicate or reset data.

## 3. Roles and permission matrix

Roles: **visitor** (anonymous), **participant**, **judge**, **organizer** (event-scoped via `EventRole`), **admin** (platform, `User.is_admin`). A user has **at most one role per event** (DB UniqueConstraint(event, user) on EventRole). This makes separation of duties structural: a judge can never also compete in or run the event they judge. `User.is_host` lets a user create events (admin grants it; admins can always create). Creating an event makes the creator its organizer. Admin has organizer powers on every event and manages users.

| Capability | Visitor | Participant | Judge | Organizer (own event) | Admin |
|---|---|---|---|---|---|
| Browse gallery, event pages, published results | yes | yes | yes | yes | yes |
| Register for event, create/join team (window open) | no | yes | no (role conflict) | no | no |
| Create/edit/submit own team's project (window open) | no | own team only | no | view all; disqualify | same as organizer |
| See drafts | no | own team only | no | yes | yes |
| See private custom answers | no | own team | assigned projects only | yes | yes |
| Read reviews/scores | no | no | **own only** | all in event | all |
| Write reviews | no | no | own active assignments, judging window open, project track in judge's tracks | no | no |
| See assignments | no | no | own only | all | all |
| Progress dashboard, results preview, exports, audit | no | no | no | yes | yes |
| Configure event, rubric, judges, assignments, publish | no | no | no | yes | yes |

Judges see only their own assignments, reviews and progress. Judge-only endpoints (assignment list, project review view with private answers, review read/write) are restricted to projects assigned to that judge, which are always in that judge's tracks. The public gallery stays public for everyone (documented in THREAT-MODEL.md).

## 4. Data model (field level)

All models: `created_at` (auto_now_add), `updated_at` (auto_now) unless noted. FK on_delete PROTECT unless noted.

**accounts.User** (custom, `AUTH_USER_MODEL`, email login, no username): `public_id` (usr_, global unique), `email` (unique, lowercased), `display_name`, `is_active`, `is_admin` (bool), `is_host` (bool), `date_joined`. Use `AbstractBaseUser` + `PermissionsMixin` is NOT needed; keep `is_staff=False` always (Django admin is not mounted).
**accounts.ApiToken**: user FK CASCADE, `name`, `prefix` (first 12 chars, indexed), `key_hash` (sha256 hex, unique), `last_used_at`, `revoked_at`, `is_demo` (bool). Header: `Authorization: Bearer vd_<40 url-safe chars>`. Plaintext shown once at creation.

**events.Event**: `slug` (unique), `source_id` (nullable, unique), `name`, `tagline`, `description`, `submissions_open_at` (nullable), `submissions_close_at` (required), `judging_open_at` (nullable → defaults to submissions_close_at), `judging_close_at` (nullable = open until organizer closes), `voting_open_at`, `voting_close_at` (nullable, T3), `max_team_size` (default 4, 1..10), `reviews_per_project` (default 3), `judging_mode` (rubric | pairwise | both; default rubric), `ranking_method` (normalized | raw | pairwise; default normalized), `shrinkage_lambda` (Decimal, default 2.0), `scoring_locked_at` (nullable; set when first review is submitted; locks rubric + ranking_method + lambda), `gallery_public` (default true), `created_by` FK User.
Validation: `submissions_open_at < submissions_close_at`; `judging_open_at >= submissions_close_at` (projects never change under judges); `judging_close_at > judging_open_at` when set.
**events.Track**: event FK CASCADE, `public_id` (trk_), `name`, `description`, `position`. Unique (event, name).
**events.Prize**: event FK CASCADE, `public_id` (prz_), `name`, `description`, `value` (text), `track` FK nullable (track prize), `position`.
**events.CustomQuestion**: event FK CASCADE, `public_id` (q_), `prompt`, `help_text`, `kind` (short_text | long_text | url | choice | yes_no), `choices` (JSON list), `required` (bool), `is_public` (bool; false = judges/organizers only), `position`.
**events.EventRole**: event FK CASCADE, user FK, `role` (participant | judge | organizer), `public_id` (usr-facing id: jdg_ for judges e.g. `jdg_24`, par_ for participants, org_ for organizers), `tracks` M2M Track (judges), `source_id`, `added_by` FK nullable. UniqueConstraint(event, user); UniqueConstraint(event, public_id).

**teams.Team**: event FK, `public_id` (tm_), `name` (NOT unique: the fixture reuses team names inside one event; identity is `public_id`), `source_id`, `created_by` FK nullable.
**teams.TeamMember**: team FK CASCADE, user FK, `event` FK (denormalized for the constraint), `is_owner` (bool), `joined_at`. UniqueConstraint(event, user) = one team per person per event.
**teams.TeamInvite**: team FK CASCADE, `token` (43-char url-safe random, unique), `created_by`, `expires_at` (default now+7d, capped at submissions_close_at), `max_uses` (nullable), `use_count`, `revoked_at`. Rotating creates a new invite and revokes the old one.

**projects.Project**: event FK, team FK, track FK nullable (required to submit), `public_id` (prj_), `source_id`, `title` (≤120), `summary` (tagline ≤200), `description` (≤10000), `thumbnail` (ImageField nullable), `demo_video_url`, `repo_url`, `live_url`, `tech_tags` (JSON list of ≤10 lowercase strings ≤24 chars), `status` (draft | submitted | withdrawn | disqualified | superseded), `first_submitted_at`, `last_submitted_at`, `revision` (int, 0 until first submit), `superseded_by` FK self nullable, `status_reason` (text, for withdraw/disqualify/supersede).
Partial UniqueConstraint(team) where status in (draft, submitted) = one active project per team.
**projects.ProjectRevision**: project FK CASCADE, `number`, `snapshot` (JSON of all public+private fields and answers), `created_by` FK nullable, `created_at`. One per submit and per edit while submitted. Unique (project, number).
**projects.ProjectImage**: project FK CASCADE, `image`, `caption`, `position`. Max 6 per project.
**projects.Answer**: project FK CASCADE, question FK CASCADE, `value` (text ≤2000). Unique (project, question).

**judging.Rubric**: event OneToOne, `version` (int). **judging.Criterion**: rubric FK CASCADE, `key` (slug), `name`, `description`, `weight` (Decimal(6,3) > 0), `min_score` (int, default 1), `max_score` (int, default 5, > min), `position`. Unique (rubric, key). Rubric edits are rejected with 409 `scoring_locked` once `event.scoring_locked_at` is set.
**judging.JudgeInvite**: event FK CASCADE, `email`, `tracks` M2M, `token` (unique), `created_by`, `expires_at`, `accepted_at`, `accepted_by` FK nullable, `revoked_at`. Accepting requires the logged-in account's email to equal the invite email.
**judging.Conflict**: event FK CASCADE, judge FK EventRole, team FK, `reason`, `source` (declared_by_judge | organizer), `created_by`. Unique (judge, team).
**judging.AssignmentBatch**: event FK, `method` (manual | auto | import), `params` (JSON), `note`, `created_by` FK nullable.
**judging.Assignment**: event FK, judge FK EventRole, project FK, batch FK nullable, `public_id` (asg_). UniqueConstraint(judge, project). Service rejects: judge not role=judge in this event; project not submitted; project.track not in judge.tracks; conflict exists.
**judging.Review**: event FK, assignment OneToOne, judge FK EventRole, project FK, `public_id` (rev_), `status` (draft | submitted), `comment` (≤2000, private to judge + organizers), `submitted_at`, `rubric_version`, `project_revision` (int, the revision the judge saw), `source` (live | import).
**judging.CriterionScore**: review FK CASCADE, criterion FK, `value` (int within [min,max]). Unique (review, criterion).
**judging.ReviewExclusion**: review OneToOne, `reason`, `created_by`. Excluded reviews are kept, shown, and left out of results (organizer action, audited, only before publication).
**judging.Comparison** (pairwise): event FK, judge FK EventRole, `left` FK Project, `right` FK Project, `winner` FK Project nullable (null = skipped), `created_at`.

**results.ResultPublication**: event FK, `public_id` (pub_), `method`, `params` (JSON: lambda, weights, rubric version), `input_digest` (sha256 of the canonical input set), `rows` (JSON), `judge_rows` (JSON, organizer-only), `note`, `published_by`, `published_at`, `supersedes` FK self nullable.

**audit.AuditEvent**: event FK nullable, actor FK User nullable, `actor_label` (snapshot "Priya (participant)"), `action` (dotted: `project.submitted`), `target_type`, `target_id` (public id), `summary` (one human sentence), `data` (JSON), `ip_hash` (nullable, sha256(ip + SECRET_KEY)[:16]), `created_at`. Index (event, created_at).

## 5. Lifecycle rules

- Register / team create / join / leave: allowed while `now < submissions_close_at` (and after open if set).
- Project create, edit, submit, image/answer changes, withdraw: only while the submission window is open. After close: **403 `window_closed`** with the close time, checked first after authentication, before any validation. Drafts never submitted stay drafts and are excluded everywhere public and from judging.
- Submit requires title, summary, track, description, repo_url and every required custom question. Edits while submitted create a new revision immediately (no "unsubmit" state).
- Judging: reviews writable while `judging_open_at <= now < judging_close_at` (null close = open). "Close judging now" (organizer) sets `judging_close_at = now`.
- `scoring_locked_at` is set when the first review is submitted (import sets it too). After that: rubric, ranking_method, shrinkage_lambda → 409 `scoring_locked`.
- Publication requires judging closed (and voting closed if configured). Publishing again creates a new publication that `supersedes` the previous one, with a mandatory note.
- Deadline changes: organizer can change windows before judging starts (audited with old/new values); once `scoring_locked_at` is set, submission window changes → 409.

## 6. API contract (base `/api/v1`, OpenAPI at `/api/schema/`, docs at `/api/docs/`)

Auth: session cookie (browser; CSRF required, token from `csrftoken` cookie sent as `X-CSRFToken`) or `Authorization: Bearer vd_...` (no CSRF). Throttles: anon 120/min, user 1200/min, login 10 per 15 min per IP+email.

Accounts: `POST /auth/login` `POST /auth/logout` `POST /auth/register` `GET /me` `GET|POST /me/tokens` `DELETE /me/tokens/{prefix}`; admin: `GET /admin/users` `PATCH /admin/users/{public_id}` (is_host, is_admin, is_active).
Events: `GET|POST /events` `GET|PATCH /events/{slug}` `POST /events/{slug}/close-judging` ; `GET|POST /events/{slug}/tracks` `PATCH|DELETE /events/{slug}/tracks/{id}` ; same for `/prizes` and `/questions` ; `POST /events/{slug}/register` ; `GET|POST /events/{slug}/organizers`.
Teams: `GET|POST /events/{slug}/teams` (participant sees own; organizer all) `GET /events/{slug}/teams/{id}` `POST /events/{slug}/teams/{id}/invite` (rotate, returns link) `POST /invites/{token}/accept` `POST /events/{slug}/teams/{id}/leave` `DELETE /events/{slug}/teams/{id}/members/{user_public_id}` (owner).
Projects: `GET /projects` (public gallery: q, event, track, tag, sort=title|newest, page) `GET|POST /events/{slug}/projects` `GET|PATCH /events/{slug}/projects/{id}` `POST .../submit` `POST .../withdraw` `POST .../disqualify` (organizer, reason) `POST .../images` (multipart) `DELETE .../images/{n}` `PUT .../answers`.
Judging: `GET|PUT /events/{slug}/rubric` ; `GET /events/{slug}/judges` `POST /events/{slug}/judges` (add existing user by email + tracks) `PATCH|DELETE /events/{slug}/judges/{jdg_id}` ; `POST /events/{slug}/judge-invites` `POST /judge-invites/{token}/accept` ; `GET|POST /events/{slug}/conflicts` ; `GET|POST /events/{slug}/assignments` (batch: {judges:[], projects:[]}) `DELETE /events/{slug}/assignments/{asg_id}` `POST /events/{slug}/assignments/auto` ({target, max_load, dry_run}) ; judge console: `GET /judge/assignments` `GET|PUT /events/{slug}/judge/reviews/{prj_id}` (draft save) `POST /events/{slug}/judge/reviews/{prj_id}/submit` `POST /events/{slug}/judge/conflicts` ; pairwise: `GET /events/{slug}/judge/pairs/next` `POST /events/{slug}/judge/comparisons` ; `GET /events/{slug}/progress` ; `POST|DELETE /events/{slug}/reviews/{rev_id}/exclusion`.
**Scores:** `GET /judge/scores` → the caller's own reviews across events (200 judge, 403 `not_a_judge` for anyone without a judge role, 401 anonymous). If `?judge=X` is given and X is not the caller's judge id → 403. `GET /events/{slug}/judges/{jdg_id}/scores` → 200 for that judge themself and for organizers/admins; **403 for everyone else, including other judges**.
Results: `GET /events/{slug}/results/preview` (organizer) `POST /events/{slug}/results/publish` `GET /events/{slug}/results` (public only after publication).
Exports (organizer, `text/csv`, formula-injection safe): `GET /events/{slug}/exports/{kind}.csv` for kind in participants, teams, projects, judges, assignments, reviews, progress, results, audit (+ votes in T3); `GET /events/{slug}/exports/event.json` (fixtures-shaped round trip). Import: `POST /imports` (admin/host, fixtures-shaped JSON → new event).
Audit: `GET /events/{slug}/audit` (organizer; filters action, actor, since).

## 7. Acceptance checker contract (.dogfood.toml)

```toml
[portal]
base_url = "http://localhost:8080"

[tiers]
claimed = ["T1", "T2"]
pitch = "A one-command hackathon portal where deadlines, judge isolation and score normalization are enforced in the backend and explained to organizers."

[auth]
organizer   = "Authorization: Bearer vd_demo_organizer_7f2a91c4e0b3"
judge_a     = "Authorization: Bearer vd_demo_judge_a_91bc5d2e8f10"
judge_b     = "Authorization: Bearer vd_demo_judge_b_44de0a7c3b92"
participant = "Authorization: Bearer vd_demo_participant_2e88f1d4a6c5"

[routes]
gallery      = "/projects"
submit       = "/api/v1/events/sample-hack-2026/projects"
judge_scores = "/api/v1/judge/scores"
peer_scores  = "/api/v1/events/sample-hack-2026/judges/jdg_24/scores"
csv_export   = "/api/v1/events/sample-hack-2026/exports/results.csv"
```
No `#` inside values (run.py's fallback parser strips from `#`). judge_a = jdg_24 Diego Herrera (diego.herrera@example.org), judge_b = jdg_26 Jonas Vogel (jonas.vogel@example.org), participant = priya1@example.org (team tm_01 NorthKiln, project "Glass Signal"). `/projects` is server-rendered HTML sorted by title with 48 per page, so fixture titles are in the body.

## 8. Bootstrap, seed and demo mode

`python manage.py runportal` (container CMD): wait for DB (retry 60 s), `migrate --noinput`, `bootstrap`, then `os.execvp` gunicorn (`-b 0.0.0.0:8080 -w 3 verdict.wsgi`). Pure Python, no shell scripts.
`bootstrap` (idempotent, one transaction):
1. If no Event with source_id `evt_01`: import `fixtures.json` (`settings.FIXTURES_PATH`) via `interop.importer` into event "Sample Hack 2026" slug `sample-hack-2026`, `submissions_close_at` from the fixture, `submissions_open_at` null, `judging_open_at` = close, `judging_close_at` null (judging still in progress: the fixture has unfinished batches). Rubric: functionality, quality, innovation, weight 1 each, scale 1–5.
   - Users for every judge (name from fixture) and team member (display_name = email local part). All seeded users share one precomputed demo password hash (hash once, assign to all: PBKDF2 per user would take a minute).
   - Teams, members (first member = owner), projects (status submitted, revision 1, first/last_submitted_at = fixture submitted_at, a ProjectRevision snapshot).
   - Duplicate rule: same team and same case-folded title → every earlier one becomes `superseded` with `superseded_by` = the latest and a status_reason; record an import note. (Fixture: prj_07 superseded by prj_41, "Dry Harbour", tm_07.)
   - Each score → Assignment (batch method import) + submitted Review (source import) + CriterionScores. Comments kept. Set `scoring_locked_at`.
   - Import report (counts, duplicates, constant scorers, under-reviewed projects) stored as an AuditEvent `import.completed` with data.
2. Demo accounts (always ensured): `admin@verdict.local` (is_admin), `organizer@verdict.local` (is_host; organizer of both events).
3. Demo event "Demo Hack (live)" slug `demo-hack`: submissions open now → now+14 days (fixed at first boot), 3 tracks, 2 prizes, 2 custom questions (one public, one private), rubric of 4 criteria with unequal weights, unlocked. Seed 5 demo teams (`demo-team-1..5@verdict.local` members) with submitted projects spread over the tracks and 3 demo judges (`judge1..3@verdict.local`, each on 2 tracks) so the video's judging segment has material; no reviews (judging opens only after "Close submissions now"). The fixture participant priya1@example.org has no role here, so the live demo can register, form a team and submit.
4. If `DEMO_MODE` (default on in compose): ensure the four deterministic demo tokens from section 7 (plus `vd_demo_admin_c0ffee5eed01` for admin), stored hashed, `is_demo=True`. Demo password for all seeded users: `verdict-demo`.
5. Print the banner every boot:
```
VERDICT is running at http://localhost:8080   (DEMO MODE: demo credentials below, never use in production)
password for every demo account: verdict-demo
  admin        admin@verdict.local          Authorization: Bearer vd_demo_admin_c0ffee5eed01
  organizer    organizer@verdict.local      Authorization: Bearer vd_demo_organizer_7f2a91c4e0b3
  judge_a      diego.herrera@example.org    Authorization: Bearer vd_demo_judge_a_91bc5d2e8f10
  judge_b      jonas.vogel@example.org      Authorization: Bearer vd_demo_judge_b_44de0a7c3b92
  participant  priya1@example.org           Authorization: Bearer vd_demo_participant_2e88f1d4a6c5
seeded: 1 fixture event (41 projects incl. 1 duplicate, 30 judges, 126 reviews), 1 open demo event
```
With `DEMO_MODE=0`: no demo tokens/passwords; seeded users get unusable passwords; admin from `ADMIN_EMAIL`/`ADMIN_PASSWORD` env.
Login page in demo mode shows one-click "sign in as" buttons for the five roles (POST to `/api/v1/auth/demo-login` which only exists when DEMO_MODE is on).

## 9. Judging engine (`results/engine.py`, pure Python, no Django imports, fully unit tested)

**Review score.** For review r with criterion values v_c: `s_r = 100 * Σ w_c (v_c − min_c)/(max_c − min_c) / Σ w_c` (0–100).
**Raw.** `raw_p` = mean of s_r over included reviews of p; `n_p` = count.
**Normalized (official default): additive judge offset with shrinkage.** Model `s_r = μ_p + b_j + ε`. Minimize `Σ_r (s_r − μ_p − b_j)² + λ Σ_j b_j²` by block coordinate descent: start b=0; repeat { μ_p = mean_{r∈p}(s_r − b_j); b_j = Σ_{r∈j}(s_r − μ_p)/(n_j + λ) } until max change < 1e−10 (cap 10 000 iterations). If λ = 0, re-centre so Σ_j n_j b_j = 0. `norm_p = μ_p`. Deterministic: iterate in sorted id order.
**Components.** Connected components of the judge–project graph; report count. Cross-component comparisons are flagged "weakly supported" (fixture: 1 component).
**Diagnostics.** Under-reviewed (n_p < event.reviews_per_project); single-review judge (offset weakly estimated); constant scorer (≥2 reviews, all criterion values identical across their reviews: fixture jdg_07); superseded/excluded reviews listed with reasons.
**Judge table.** n_j, mean given, offset b_j, label harsh (b < −5) / generous (b > +5) / typical, spread (std of s_r).
**Explanation.** Per project: each review's judge, s_r, b_j, adjusted s_r − b_j; rank change vs raw.
**Ranking.** Sort by the event's ranking_method value desc; equal values after rounding to 2 dp share a rank ("=3"); display tie-break by title.
**Bradley–Terry.** Input comparisons (winner, loser, weight). MM algorithm (Hunter 2004) with a regularizing virtual opponent (each item gets 1 win and 1 loss against it) so undefeated items stay finite. Output strength = log p, rank. Items with no comparisons are unranked. Two sources: live pairwise `Comparison` rows, and "derived pairwise" from rubric reviews (every pair of projects the same judge scored; higher s_r wins; equal = half win each). The derived ranking is shown as a cross-check because within-judge comparisons cancel judge offsets.
**Why not z-scores** (for JUDGING.md): jdg_07 has zero variance (division by zero), and z-scores depend on which projects a judge happened to get.

## 10. Assignment algorithm (`judging/assign.py`)

Inputs: target k (default event.reviews_per_project), max_load (default ceil(total_needed / eligible_judges) + 1), seed (default: event slug). Eligible pair: judge role in event, project submitted, project.track in judge.tracks, no conflict, not already assigned.
1. need_p = k − active assignments of p.
2. Order projects by (fewest eligible judges, need desc, public_id): most constrained first.
3. For each unit of need pick the eligible judge with the lowest current load; tie-break by fewest shared projects with the project's other reviewers (spreads overlap, improves connectivity), then seeded RNG.
4. Output: proposed assignments + unfilled needs with reasons ("track Health has 2 eligible judges; target 3 unreachable"). dry_run returns the proposal; apply re-validates inside a transaction and records an AssignmentBatch.
Also "fill gaps" = the same run on existing data (fixes the fixture's incomplete batches).

## 11. Progress dashboard

Per judge: tracks, assigned, submitted, drafts, remaining, last activity, status (not started | in progress | done | no assignments). "Not started" judges listed first. Per project: submitted reviews / target, under-covered highlighted. Per track: coverage %. JSON at `/events/{slug}/progress`; page refreshes it every 15 s.

## 12. UI pages (server-rendered; extend `base.html`, blocks: `title`, `content`, `page_actions`, `extra_js`)

Public: `/` home, `/projects` gallery (search, filters, pagination), `/events/`, `/events/{slug}/`, `/events/{slug}/projects/{id}`, `/events/{slug}/results`, `/login`, `/register`, `/invite/{token}`, `/judge-invite/{token}`.
Participant: `/me`, `/events/{slug}/team`, `/events/{slug}/submission` (editor with deadline countdown, required-field checklist, revision list).
Judge: `/judge` (queue across events with progress), `/judge/{slug}/review/{prj}` (project evidence left, rubric right, keys 1–9 set scores, autosave draft, explicit submit, next/prev), `/judge/{slug}/pairwise`.
Organizer: `/events/new`, `/manage/{slug}/` (phase checklist, numbers, next actions, data issues), `/manage/{slug}/settings`, `/manage/{slug}/tracks-prizes-questions`, `/manage/{slug}/rubric`, `/manage/{slug}/judges`, `/manage/{slug}/assignments`, `/manage/{slug}/progress`, `/manage/{slug}/results`, `/manage/{slug}/exports`, `/manage/{slug}/audit`.
Admin: `/admin-panel/` (users: host/admin/active toggles; all events).
Forms: `<form data-api-method="POST" data-api-url="/api/v1/..." data-success="reload|redirect:/path|redirect-field:<json key>">`; `api-forms.js` sends JSON (or multipart when a file input exists) with CSRF, shows field errors under inputs (`is-invalid` + `invalid-feedback`) and a toast. Input coercion via `data-type="int|float|bool|list|json"` (checkbox → bool, multi-select → list, `list` = comma-separated). Non-field errors render into `[data-form-errors]` inside the form. Standalone buttons work too: `<button data-api-method="POST" data-api-url="..." data-success="reload" data-confirm="Withdraw this project?">` (confirmation uses a Bootstrap modal, never `window.confirm`).

**UI kit (shared by all pages; defined in `src/static/css/app.css`, everything else is stock Bootstrap 5.3):** page header `<header class="page-header"><h1>…</h1><p class="lead">…</p><div class="page-actions">…</div></header>`; `.stat-tile` (label + big number) inside `.stat-grid`; status badges `.status-badge status-draft|status-submitted|status-withdrawn|status-disqualified|status-superseded|status-open|status-closed|status-not-started|status-in-progress|status-done`; `.project-card` (thumbnail or generated initials placeholder, title, summary, team, track badge, tags); `.empty-state` (icon-less message + primary action); `.countdown` on `<time datetime="ISO" data-countdown>` (JS renders "closes in 3 h 12 min" and local time); `.data-table` (Bootstrap table + sticky header + `table-sm`); `.role-pill role-participant|role-judge|role-organizer|role-admin`. Brand: indigo primary (#4f46e5), system font stack, generous whitespace, cards with subtle borders, dark mode automatic (`data-bs-theme` set by `theme.js` from prefers-color-scheme). Every page: one `h1`, breadcrumbs for nested pages, visible focus, labels on every input, responsive down to 360 px.

## 13. Docker and offline

`docker-compose.yml`: `db` postgres:16-alpine (healthcheck pg_isready, volume `pgdata`), `web` build `.`, ports `8080:8080`, env `DATABASE_URL=postgres://verdict:verdict@db:5432/verdict`, `DEMO_MODE=1`, volume `appdata:/data` (media + generated secret key), depends_on db healthy, healthcheck via python urllib on `/healthz`.
Dockerfile: python:3.12-slim, non-root user, `pip install --no-cache-dir -r requirements.txt`, `collectstatic` at build, `CMD ["python","manage.py","runportal"]`.
SECRET_KEY: env, else generated on first boot and persisted to `/data/secret_key`. Static via whitenoise `CompressedStaticFilesStorage` (not manifest). Media served by a small Django view from `/data/media`. Security headers middleware: CSP `default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'` (embed route overrides frame-ancestors *), X-Content-Type-Options, Referrer-Policy same-origin. Nothing at runtime touches the network.

## 14. Tests that must exist

- Permission matrix: every role × every sensitive endpoint (allowed and denied), incl. judge B → judge A scores = 403, participant → judge scores = 403, judge → other track project review = 403, judge → unassigned project review = 403, participant → other team's draft = 404/403.
- Window boundaries with patched clock: exactly at close → refused; one second before → allowed; closed event POST → 403 `window_closed` before validation.
- Import: counts (41 projects, 1 superseded, 30 judges, 126 reviews), idempotent second bootstrap, constant scorer detected, duplicate handled.
- Engine: shift invariance (add +10 to one judge's scores, λ=0 → identical ranking), constant judge no crash, disconnected components reported, BT recovers a known order, ties share rank.
- CSV: header row, formula-injection escaping.
- Checker parity: Django test client replays the seven run.py checks.

## 15. Tier plan (revised 2026-09-25 after organizer confirmation)

Organizer confirmation (Discord, 2026-09-25, relayed by the team's mentor): "run.py only covers T1 and T2. T3 and T4 are judged by hand so judges review your code, UI, and architecture directly." So T3/T4 count toward the 40% tier score when complete and correct; `run.py` will print "claimed but not verified: T3 T4", which the README explains with this confirmation.

Order: T1 → T2 (+ normalization proof, API-first by construction) → **T3 complete** → threat model → **T4 complete** → pairwise bonus last (tie-breaker only). Claim a tier only when every bullet works end to end in the UI and API with tests; a partial tier is written up as partial and not claimed.
Evidence for hand-judged tiers: `docs/TIER-EVIDENCE.md` lists every T1–T4 bullet with where it lives (page + endpoint), a 1-minute manual check, the curl command, and the test names that prove it.

## 16. Hardening addendum (from the pre-kickoff architecture review; mandatory)

Checker safety
- **No redirects on checker routes.** API URLs have no trailing slash (`/api/v1/events/{slug}/projects`, not `.../projects/`) and `/projects` is routed exactly. A trailing-slash redirect makes urllib turn the POST into a GET (→ 200 → the closed-event check fails). Parity tests use `follow=False` and assert exact status codes.
- DRF authentication order: `BearerTokenAuthentication` **first** (its `authenticate_header` returns `Bearer realm="api"`), then `SessionAuthentication`, so anonymous API calls get a real 401 instead of DRF's 403 fallback.
- No `format_suffix_patterns`; export views return `HttpResponse` directly so `.csv` paths never hit DRF content negotiation.
- In DEMO_MODE bootstrap re-activates the demo tokens every boot (a judge revoking one while exploring must not break `run.py` on the next `docker compose up`).
- The fixture event's submission window can never be reopened (scoring is locked at import), so the closed-event check cannot be broken from the UI.

Isolation and integrity
- **Cross-event consistency:** every relation between event-scoped rows must stay inside one event (project.track, prize.track, judge tracks, assignment judge/project, conflict team, answer question). Services reject violations with 400 `cross_event`; tests cover each.
- **Media authorization:** uploads are served by a Django view that resolves the owning project and applies `policy.visible_project`: public for publicly visible projects, otherwise only the team, organizers and assigned judges (404 for everyone else). No direct static serving of `/data/media`.
- **Unranked is a status, not a zero.** Result rows carry `status`: ranked | unranked_no_reviews | withdrawn | disqualified | superseded, with a reason. Publishing while an eligible project is unranked requires `acknowledge_unranked: true` (UI checkbox naming the projects).
- **Decision record.** Each publication stores the canonical input set (included reviews with criterion values, weights, λ, method, exclusions with reasons, superseded projects) plus its sha256 digest. Page `/manage/{slug}/results/publications/{pub}` shows: locked rule in plain words, parameters, inputs digest, included/excluded reviews with reasons, interventions pulled from the audit log (window changes, exclusions, assignment batches), limitations, and a **Verify** action (`POST .../verify`, also `python manage.py verify_publication pub_x`) that recomputes from the stored inputs with the current engine and checks (a) rows are identical and (b) the live database still hashes to the stored digest. Organizer-only; public results stay a separate projection.
- Imported events adopt the default policy (normalized, λ = 2) locked at import because historical reviews already exist; this is stated in JUDGING.md and on the decision record.

Lifecycle and UX
- **Close submissions now:** `POST /events/{slug}/close-submissions` (organizer; only while open and before scoring lock) sets `submissions_close_at = now` and `judging_open_at = now` (audited). Needed for the live demo lifecycle.
- **Optimistic concurrency:** project PATCH accepts `base_updated_at`; if it differs from the stored value → 409 `stale_edit` ("This project changed in another tab; reload to see the latest version"). The editor sends it.
- **Submission receipt:** each ProjectRevision stores `digest` (sha256 of its canonical snapshot). The editor shows "Submitted · revision 3 · 2026-… UTC · receipt 3f9a…".
- Custom questions with answers cannot be deleted (409 `question_in_use`); edit the wording or mark `is_active = false`.
- Organizers: list/add/remove co-organizers; the last organizer cannot be removed (409 `last_organizer`).
- Offline password reset: an admin generates a one-time reset link (`/reset?token=…`, 24 h, single use) shown in the admin panel; users can change their password from `/me`.

Tokens, logs, hosting
- Invite links carry tokens in the query string (`/invite?token=…`, `/judge-invite?token=…`) and gunicorn uses an access-log format without query strings or referrers (`%(h)s %(t)s "%(m)s %(U)s" %(s)s %(b)s %(L)s`), so secrets never land in logs. `Referrer-Policy: same-origin`.
- Login: generic error message (no account enumeration); `next` parameter validated with `url_has_allowed_host_and_scheme`.
- `base.html` renders `<meta name="csrf-token" content="{{ csrf_token }}">` (this also guarantees the CSRF cookie exists); `api-forms.js` reads it.
- DEMO_MODE: `ALLOWED_HOSTS = ["*"]` unless `ALLOWED_HOSTS` is set; production requires it explicitly. Compose publishes `"${VERDICT_PORT:-8080}:8080"`; `.env.example` documents DEMO_MODE, SECRET_KEY, ALLOWED_HOSTS, ADMIN_EMAIL/ADMIN_PASSWORD, POSTGRES_PASSWORD, VERDICT_PORT.
- README quickstart: `docker compose up -d --wait` then `python3 run.py .dogfood.toml`; the first build needs internet once (base images + packages), after which the portal runs with networking disabled (we test exactly that).
- Import report records the fixture file's sha256 and byte size.

Documentation obligations (feed README/ARCHITECTURE/DATA-MODEL/JUDGING)
- Third-party license inventory (Django BSD-3, DRF BSD-3, drf-spectacular BSD-3, psycopg LGPL-3 as an unmodified dependency, gunicorn MIT, whitenoise MIT, Pillow MIT-CMU/HPND, Bootstrap MIT).
- Backup/restore: `docker compose exec db pg_dump …` + copy of the `appdata` volume (media + secret key), restore steps, and what a lost secret key invalidates (sessions only; API tokens and invites are independent of it).
- Honest limitations section, including: public gallery is public to judges too; offset model limits; no email delivery offline (links shown to organizers); IP-based rate limits are weak against shared or rotating IPs.

## 17. Winning addendum (from the full dogfoodhack.com brief, judges' profiles and scoring figures)

The panel is 36 senior engineers and architects (Microsoft, AWS, Meta, Walmart, security and DevOps leads) reading "for what breaks under load rather than what demos well", "ownership of state and error paths", and "where data models either hold or quietly lose records". Each project gets 3 independent reviews. Build for that reader.

Judging engine (Best Judging Engine prize + Normalization Proof bonus)
- **Answer "the judge who marks everything a 3" explicitly** (the brief asks it verbatim). Constant scorers are detected, kept by default (their level is absorbed by the offset; their reviews add no ordering information and only pull their projects toward a common value), shown with that explanation, and the proof reports the rank changes if they are excluded. Organizer may exclude with a recorded reason before publication.
- **Spread before/after** (the brief's own figure): report σ of per-judge mean scores raw vs after offset removal, on the results page and in the proof, plus a rank-movement list (▲/▼ per project).
- **Outlier reviews** (collusion / undeclared-conflict signal): residual r = s − μ_p − b_j; flag |r| > 2.5 × residual SD when the project has ≥ 3 reviews. Shown to organizers as "Diego Herrera scored Glass Signal 31 points above consensus", never auto-excluded. Named in the threat model as detection, not prevention.
- **Feedback to teams — privacy correction, 2026-09-27:** after publication an organizer may release the official score, rank and per-criterion averages for each team's own project. `Review.comment` was collected under a private judge/organizer-only notice and MUST NOT be released, even with names removed or order shuffled. The feedback API keeps `comments: []` for compatibility; author/own-event organizer access to private notes remains through authorized review/export routes. Written team-facing feedback is an outstanding feature requiring a separately labelled, explicitly shareable field/workflow; never infer retroactive consent from existing private notes. Release toggle is audited; `GET /events/{slug}/projects/{id}/feedback` remains team-members-after-release/organizer scoped.

Engineering signals senior reviewers look for
- No N+1 queries: list views/exports use `select_related`/`prefetch_related`; tests assert query counts (`assertNumQueries`) for the gallery, judge queue, progress and results.
- `pyproject.toml` with ruff (lint + format) config; code formatted; `.github/workflows/ci.yml` runs ruff, `manage.py test tests`, then `docker compose up -d --wait` + `python run.py .dogfood.toml` and fails unless 7/7 PASS.
- `scripts/loadtest.py` (standard library threads + urllib): 50 concurrent users mixing gallery, project pages, judge queue and score saves; prints p50/p95/p99 and error count; results with hardware noted go in README "Operations".
- Compose: `restart: unless-stopped`, healthchecks on both services, named volumes, no host bind mounts required.
- Optional outbound email: if `EMAIL_HOST` is set, invites and "results published" notices are emailed through Django's SMTP backend; otherwise (and always in DEMO_MODE) messages go to an **Outbox** (DB-backed email backend) visible to organizers, so the offline portal still shows exactly what would have been sent.

Documents judges and the team must be able to defend (out-of-scope rule: "LLM dumps with no architecture document and nobody able to defend the schema in writing")
- ARCHITECTURE.md: diagram, request lifecycle (auth → policy → service → audit), why a modular monolith, why server-rendered + API-as-the-only-write-path, and a "Decisions worth stealing" section (one role per person per event as a DB constraint; scoring locks at the first review; verifiable publications; half-open windows on server time checked after the row lock; import provenance with source ids and checksums; media behind the same policy as data).
- DATA-MODEL.md: ER diagram, every table with the invariant each constraint protects, import/export paths (fixtures JSON in, event.json round trip out, CSVs per stage), what is never exported (password hashes, token hashes, secrets).
- `docs/SCHEMA-DEFENSE.md` (for the team, also public): the 15 questions a database reviewer would ask and our written answers, so a human on the team can answer follow-ups during the evaluation window.
- Commit history: small, descriptive commits per reviewed packet across the whole window (no single giant dump).

Bonus plan (all four, each only if done properly; the brief warns "one done properly beats four started")
1. Normalization Proof: P2-EN + JUDGING.md (flagship).
2. API First: every UI write already goes through the API; add committed `docs/openapi.yaml` (generated, validated with `spectacular --validate --fail-on-warn` in CI), `docs/API.md` with a table mapping every UI action to its endpoint, token auth guide and curl examples, and a test that walks all `data-api-url` attributes in templates and asserts each path exists in the schema.
3. Threat Model: `docs/THREAT-MODEL.md` naming stopped and not-stopped attacks (Sybil votes, ballot stuffing, submission scraping, judge collusion, deadline gaming, IDOR, CSRF, XSS, token leakage, brute force, DoS), each tied to the control and the test that proves it.
4. Pairwise Mode: full alternative judging mode (packet P3-PW) with its own console, Bradley–Terry ranking, pair-selection and stopping policy, documented; claimed only if complete.

## 18. Built for a hard-to-convince panel (mentor: "this event's judging will be the most complex ever")

Assume each reviewer spends limited time, checks claims adversarially, and has a different lens (security, DevOps, data, UX, architecture). Make every claim checkable in minutes.
- **`scripts/verify_tiers.py`**: our own run.py-style checker (standard library only, same `.dogfood.toml`, prints PASS/FAIL per line) for everything run.py does not cover: T1 depth (invite-link join, draft→submit→edit, edit after close refused, gallery search/filter), T2 depth (track isolation, rubric lock 409, progress, every CSV stage, normalization output present), T3 (each voting access mode, quadratic budget, results hidden during window, ballot order random per voter, rate limit 429, duplicate ballot refused, comments moderation), T4 (webhook signature, certificate access, signed record verifies and tamper fails, embed route, export→import round trip). Its output is committed as `tier-evidence-report.txt` next to acceptance-report.txt. It creates its own disposable event so it never disturbs fixture data.
- **`scripts/attack.py`**: the curl attacks a skeptical judge would try (peer scores by path and query, other-track project, unassigned review write, IDOR on drafts and media, closed-window writes, CSRF without token, results during voting, token reuse after revoke, formula injection in CSV); prints each request and the refusal. Output committed as `attack-report.txt`.
- **README "Reviewer's map"**: one table routing each lens to evidence in under a minute ("Security → THREAT-MODEL.md + attack-report.txt; DevOps → Quick start, CI, Operations; Data → DATA-MODEL.md, SCHEMA-DEFENSE.md; Judging maths → JUDGING.md, NORMALIZATION-PROOF.md; UX → 5 screenshots + demo logins").
- **Screenshots** of the key screens (organizer overview with data issues, judge console, results with rank movement, decision record Verify) in `docs/screenshots/`, linked from README.
- **Fresh-clone rehearsal on a clean volume** before freeze, with the exact commands and timings recorded in README (first build, boot to healthy, run.py, verify_tiers.py).
- Every number in the docs must be reproducible from a command in the repo; no hand-typed results.


## 19. Decision: adaptive λ (2026-09-27)
The locked scoring policy fixes the *procedure*: λ is chosen by seeded 5-fold cross-validation over the grid (0.5, 1, 2, 5, 10, 20, 50, 100), ties to the larger λ, computed at preview/publication time and stored in the publication params. `Event.shrinkage_lambda` becomes nullable, null = auto (default; the fixture event uses auto). A fixed number may still be predeclared before the scoring lock. Evidence: on the fixture, leave-one-review-out RMSE is 19.28 for project means, 19.86 for λ=2, 19.17 for λ=10; permutation test p = 0.381 (no detectable judge effect), so a fixed λ=2 over-corrects; simulations show strong gains from normalization when judges are biased. The migration for the nullable field lands with P2-RX (after P1-EV, which owns src/events/ now).
