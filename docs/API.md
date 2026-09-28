# VERDICT API

Every write in the UI goes through this JSON API. The HTML pages only read;
forms and buttons carry `data-api-method` / `data-api-url` and post through
`src/static/js/api-forms.js`, so what the prose below documents is exactly
what the buttons do. The machine-readable contract is `openapi.yaml` next to
this file (generated, never hand-edited).

## How to explore

- `GET /api/schema/` — the OpenAPI 3 document as served by the running portal.
- `GET /api/docs/` — Swagger UI, served offline from the vendored
  `drf-spectacular-sidecar` bundle. No CDN, no network calls at runtime.
- `docs/openapi.yaml` — the same document, committed, so reviewers can read it
  without booting the portal.

All three come from one generator (`manage.py spectacular`); the test
`tests/test_api_first.py` fails the suite if they ever disagree.

## Authentication

Two mechanisms, bearer first (so anonymous API calls get a real `401` with a
`Bearer realm="api"` challenge instead of a `403`):

- **Browser session.** Sign in at `/login`; the session cookie authenticates
  you. Every write then needs the CSRF token: `base.html` renders it as
  `<meta name="csrf-token" content="...">` and `api-forms.js` sends it back as
  the `X-CSRFToken` header.
- **Bearer token.** `Authorization: Bearer vd_<40 url-safe chars>`, no CSRF
  needed. Tokens never expire on their own; they stop working when revoked.

### Tokens: create, use, revoke

An organizer (any signed-in user, for their own tokens) creates one:

```bash
curl -s -X POST -H "Authorization: Bearer $ORG" \
  -H "Content-Type: application/json" -d '{"name":"ci"}' \
  http://localhost:8080/api/v1/me/tokens
# 201 {"token":{"prefix":"vd_DXhQ6RAB6","name":"ci",...},"plaintext":"vd_DXhQ6RAB6..."}
```

The `plaintext` value is shown **once**, at creation — only its sha256 hash is
stored. Use it, then revoke by prefix when it is no longer needed:

```bash
curl -s -H "Authorization: Bearer vd_DXhQ6RAB6..." http://localhost:8080/api/v1/me  # 200
curl -s -X DELETE -H "Authorization: Bearer $ORG" \
  http://localhost:8080/api/v1/me/tokens/vd_DXhQ6RAB6                                 # 200
curl -s -H "Authorization: Bearer vd_DXhQ6RAB6..." http://localhost:8080/api/v1/me
# 401 {"error":{"code":"not_authenticated","message":"detail: Invalid or revoked token.","fields":{}}}
```

(The three commands above were run against a local server during the
walkthrough below: create `201`, use `200`, revoke `200`, reuse `401`.)

### Demo tokens (DEMO_MODE only)

With `DEMO_MODE=1` the bootstrap ensures four deterministic tokens (plus an
admin one) from `.dogfood.toml`, re-activated on every boot so exploring never
breaks the checker. The password for every demo account is `verdict-demo`,
and the login page offers one-click sign-in (which posts to
`demo_login`, an endpoint that only exists in demo mode).

| Role | Account | Token |
|---|---|---|
| admin | `admin@verdict.local` | `Authorization: Bearer vd_demo_admin_c0ffee5eed01` |
| organizer | `organizer@verdict.local` | `Authorization: Bearer vd_demo_organizer_7f2a91c4e0b3` |
| judge_a | `diego.herrera@example.org` | `Authorization: Bearer vd_demo_judge_a_91bc5d2e8f10` |
| judge_b | `jonas.vogel@example.org` | `Authorization: Bearer vd_demo_judge_b_44de0a7c3b92` |
| participant | `priya1@example.org` | `Authorization: Bearer vd_demo_participant_2e88f1d4a6c5` |

Never use these in production (`DEMO_MODE=0` creates no demo tokens; seeded
users get unusable passwords and the admin comes from `ADMIN_EMAIL` /
`ADMIN_PASSWORD`).

## Conventions

- **IDs.** Events are addressed by `slug`; everything else by `public_id`
  (`usr_…`, `trk_…`, `prz_…`, `q_…`, `par_…` / `jdg_…` / `org_…`, `tm_…`,
  `prj_…`, `asg_…`, `rev_…`, `pub_…`). Integer PKs never leave the server.
  Imported records keep the fixture id as `public_id`. Emails are never shown
  publicly — only display names.
- **Errors.** One envelope, always: `{"error": {"code", "message", "fields"}}`,
  produced by `core.errors`. `fields` carries per-field messages for `400`
  validation failures and is `{}` otherwise. Status codes the code returns:
  `400` (`invalid`, `incomplete`, …), `401` (`not_authenticated`),
  `403` (`window_closed`, `forbidden`, … — the rule that refused is in
  `error.code`), `404` (missing, or present but hidden from the caller),
  `409` (`scoring_locked`, `stale_edit`, `last_organizer`, `question_in_use`,
  `stale_preview`, `unranked_projects`, …), `410` (`invite_invalid`,
  `judge_invite_invalid` — the link was replaced or revoked), `429`
  (`throttled`), and `503` when a configured email backend is unavailable.
- **Pagination.** List endpoints paginate with `?page=` / `?page_size=`:
  50 rows a page, 100 at most. Responses are
  `{"count": N, "next": url, "previous": url, "results": [...]}`.
- **Time and windows.** Server time (`core.clock.now()`, UTC). Windows are
  half-open: `open_at <= now < close_at`; `null` means no bound. Writes after
  the close are refused with `403 window_closed` before any validation — the
  lifecycle below shows this happening for real.
- **Rate limits.** Anonymous `120/min`, signed-in `1200/min`, login
  `10 per 15 min` per IP+email. Over budget answers `429` with a `Retry-After`
  header.
- **No trailing slashes on API URLs** (`/api/v1/events/{slug}/projects`, never
  `.../projects/`): a slash redirect would turn a POST into a GET.
- **Exports** are `text/csv` downloads (formula-injection safe), served as
  plain `HttpResponse`s so `.csv` paths never hit content negotiation.
- **Webhooks.** An organizer subscribes a URL (`event_webhook_subscribe`),
  optionally queues a signed test delivery (`event_webhook_test`), and replays
  past deliveries (`event_webhook_replay`). Every audit event in the event
  fans out to matching endpoints (`*` or the dotted action name, e.g.
  `project.submitted`). Each delivery POST carries `X-Verdict-Event` (the
  action), `X-Verdict-Delivery` (deduplication id, stable across retries) and
  `X-Verdict-Signature: sha256=<hmac-hex of the body with the endpoint
  secret>`. Delivery is at least once with retries after 60 s, 5 min, 30 min
  and 2 h (`manage.py deliver_webhooks` does the sending); destinations must
  be http/https, resolvable, and non-private, and redirects are not followed.

## Operations

Grouped by product area. "UI control" names the template (or JS file) that
calls the operation; "read" rows are rendered server-side into their pages or
followed as download links, which is why they carry no `data-api-*` control.
Every `operationId` here exists in `docs/openapi.yaml`, enforced by
`tests/test_api_first.py`.

### Accounts (13)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `admin_user_patch` | `PATCH /api/v1/admin/users/{public_id}` | admin | `admin_panel/index.html` | Toggle is_host, is_admin or is_active on an account. |
| `admin_users` | `GET /api/v1/admin/users` | admin | read: server-rendered page or download link | Every account (admin only). |
| `change_password` | `POST /api/v1/me/password` | account owner | `accounts/me.html` | Change your own password (current password required). |
| `create_token` | `POST /api/v1/me/tokens` | account owner | `accounts/tokens.html` | Create an API token. |
| `demo_login` | `POST /api/v1/auth/demo-login` | anyone (DEMO_MODE only) | `accounts/login.html` | Sign in as one of the seeded demo accounts. |
| `login` | `POST /api/v1/auth/login` | anyone (no auth) | `accounts/login.html` | Sign in with email and password and start a session. |
| `logout` | `POST /api/v1/auth/logout` | signed-in user | `base.html` | End the current session. |
| `me` | `GET /api/v1/me` | signed-in user | read: server-rendered page or download link | Your profile and your roles. |
| `my_tokens` | `GET /api/v1/me/tokens` | account owner | read: server-rendered page or download link | List your API tokens. |
| `register` | `POST /api/v1/auth/register` | anyone (no auth) | `accounts/register.html` | Create an account and sign in. |
| `reset_password` | `POST /api/v1/auth/reset-password` | anyone (no auth) | `accounts/reset.html` | Set a new password with a reset link token. |
| `revoke_token` | `DELETE /api/v1/me/tokens/{prefix}` | account owner | `accounts/tokens.html` | Revoke one of your API tokens. |
| `user_reset_link` | `POST /api/v1/admin/users/{public_id}/reset-link` | admin | `admin_panel/index.html` | Generate a one-time password reset link. |

### Events (22)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `event_close_judging` | `POST /api/v1/events/{slug}/close-judging` | organizer | `manage/overview.html`<br>`manage/results.html`<br>`manage/settings.html` | Close judging now. |
| `event_close_submissions` | `POST /api/v1/events/{slug}/close-submissions` | organizer | `manage/overview.html`<br>`manage/settings.html` | Close submissions now. |
| `event_create` | `POST /api/v1/events` | host or admin | `events/new.html` | Create an event; the caller becomes its first organizer. |
| `event_detail` | `GET /api/v1/events/{slug}` | anyone | read: server-rendered page or download link | Read one event, including whether its submission window is open now. |
| `event_organizer_add` | `POST /api/v1/events/{slug}/organizers` | organizer | API/script use (no UI control) | Give an existing account the organizer role in this event. |
| `event_organizer_remove` | `DELETE /api/v1/events/{slug}/organizers/{public_id}` | organizer | API/script use (no UI control) | Remove an organizer's role in this event. |
| `event_organizers` | `GET /api/v1/events/{slug}/organizers` | organizer | read: server-rendered page or download link | List the event's organizers. |
| `event_prize_create` | `POST /api/v1/events/{slug}/prizes` | organizer | `manage/setup.html` | Create a prize (organizer). |
| `event_prize_delete` | `DELETE /api/v1/events/{slug}/prizes/{public_id}` | organizer | `manage/setup.html` | Delete a prize (organizer). |
| `event_prize_update` | `PATCH /api/v1/events/{slug}/prizes/{public_id}` | organizer | `manage/setup.html` | Change a prize (organizer). |
| `event_prizes` | `GET /api/v1/events/{slug}/prizes` | anyone | read: server-rendered page or download link | List the event's prizes and the track each is limited to. |
| `event_question_create` | `POST /api/v1/events/{slug}/questions` | organizer | `manage/setup.html` | Add a custom submission question (organizer). |
| `event_question_delete` | `DELETE /api/v1/events/{slug}/questions/{public_id}` | organizer | `manage/setup.html` | Delete a custom question (organizer). |
| `event_question_update` | `PATCH /api/v1/events/{slug}/questions/{public_id}` | organizer | `manage/setup.html` | Change a custom question (organizer). |
| `event_questions` | `GET /api/v1/events/{slug}/questions` | anyone | read: server-rendered page or download link | List the event's custom submission questions. |
| `event_register` | `POST /api/v1/events/{slug}/register` | signed-in user, no role yet (window open) | `events/detail.html` | Join an event as a participant. |
| `event_track_create` | `POST /api/v1/events/{slug}/tracks` | organizer | `manage/setup.html` | Create a track (organizer). |
| `event_track_delete` | `DELETE /api/v1/events/{slug}/tracks/{public_id}` | organizer | `manage/setup.html` | Delete an empty track (organizer). |
| `event_track_update` | `PATCH /api/v1/events/{slug}/tracks/{public_id}` | organizer | `manage/setup.html` | Change a track (organizer). |
| `event_tracks` | `GET /api/v1/events/{slug}/tracks` | anyone | read: server-rendered page or download link | List the event's tracks. |
| `event_update` | `PATCH /api/v1/events/{slug}` | organizer | `manage/settings.html` | Change an event's settings, submission window or judging window. |
| `events` | `GET /api/v1/events` | anyone | read: server-rendered page or download link | List every event with its current phase. |

### Teams (7)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `event_team_create` | `POST /api/v1/events/{slug}/teams` | participant (window open) | `teams/team.html` | Create your own team (you become its owner). |
| `event_team_detail` | `GET /api/v1/events/{slug}/teams/{public_id}` | member or organizer | read: server-rendered page or download link | Read one team you are a member of (organizers may read any). |
| `event_team_invite` | `POST /api/v1/events/{slug}/teams/{public_id}/invite` | team owner | `teams/team.html` | Mint a team invite link, revoking the one it replaces. |
| `event_team_leave` | `POST /api/v1/events/{slug}/teams/{public_id}/leave` | team member | `teams/team.html` | Leave your team; the owner passes ownership to the earliest member. |
| `event_team_remove_member` | `DELETE /api/v1/events/{slug}/teams/{public_id}/members/{user_public_id}` | team owner | `teams/team.html` | Remove a member from your team (owner only). |
| `event_teams` | `GET /api/v1/events/{slug}/teams` | member (own team) or organizer (all) | read: server-rendered page or download link | List the teams you may see, or create your own. |
| `invite_accept` | `POST /api/v1/invites/{token}/accept` | signed-in user (invite link) | `teams/invite.html` | Join the team an invite link points at. |

### Projects (11)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `event_project_answers` | `PUT /api/v1/events/{slug}/projects/{public_id}/answers` | owning team (window open) | `projects/editor.html` | Save the answers to the event's custom questions. |
| `event_project_create` | `POST /api/v1/events/{slug}/projects` | owning team (window open) | `projects/editor.html` | Create your team's draft project. |
| `event_project_detail` | `GET /api/v1/events/{slug}/projects/{public_id}` | anyone (submitted) / team drafts stay private | read: server-rendered page or download link | Read one project as the caller is allowed to see it. |
| `event_project_disqualify` | `POST /api/v1/events/{slug}/projects/{public_id}/disqualify` | organizer | `manage/results.html via js/consequences.js` | Disqualify a project (organizer only, with a reason). |
| `event_project_image_detail` | `DELETE /api/v1/events/{slug}/projects/{public_id}/images/{position}` | owning team (window open) | `projects/editor.html` | Remove one gallery image by its position in the editor. |
| `event_project_images` | `POST /api/v1/events/{slug}/projects/{public_id}/images` | owning team (window open) | `projects/editor.html` | Attach a gallery image (jpeg, png, webp or gif, up to 5 MB, six per project). |
| `event_project_submit` | `POST /api/v1/events/{slug}/projects/{public_id}/submit` | owning team (window open) | `projects/editor.html` | Submit your team's project. |
| `event_project_update` | `PATCH /api/v1/events/{slug}/projects/{public_id}` | owning team (window open) | `projects/editor.html` | Edit your team's project. |
| `event_project_withdraw` | `POST /api/v1/events/{slug}/projects/{public_id}/withdraw` | owning team (window open) | `projects/editor.html` | Withdraw your team's submission while the window is open. |
| `event_projects` | `GET /api/v1/events/{slug}/projects` | anyone (submitted only) | read: server-rendered page or download link | List the event's submitted projects. |
| `gallery` | `GET /api/v1/projects` | anyone (submitted only) | read: server-rendered page or download link | The public project gallery (submitted projects only). |

### Judging (26)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `event_assignment_delete` | `DELETE /api/v1/events/{slug}/assignments/{assignment_id}` | organizer | `manage/assignments.html` | Delete one assignment. |
| `event_assignments` | `GET /api/v1/events/{slug}/assignments` | organizer | read: server-rendered page or download link | Read the assignment table of an event. |
| `event_assignments_auto` | `POST /api/v1/events/{slug}/assignments/auto` | organizer | `manage/assignments.html` | Preview or apply the automatic assignment planner. |
| `event_assignments_create` | `POST /api/v1/events/{slug}/assignments` | organizer | `manage/assignments.html` | Create assignments for explicit judge/project pairs. |
| `event_command_center` | `GET /api/v1/events/{slug}/command-center` | organizer | `manage/command_center.html (poll/read)` | Judge pace forecast, projected finish and rebalance proposal. |
| `event_conflict_create` | `POST /api/v1/events/{slug}/conflicts` | organizer | `manage/judges.html` | Record a conflict between a judge and a team (manager). |
| `event_conflicts` | `GET /api/v1/events/{slug}/conflicts` | organizer | read: server-rendered page or download link | List the recorded conflicts of interest in an event. |
| `event_judge_add` | `POST /api/v1/events/{slug}/judges` | organizer | `manage/judges.html` | Add a judge to the event, creating the account if needed. |
| `event_judge_invites` | `POST /api/v1/events/{slug}/judge-invites` | organizer | `manage/judges.html` | Mint judge invite links for a list of email addresses. |
| `event_judge_remove` | `DELETE /api/v1/events/{slug}/judges/{judge_id}` | organizer | `manage/judges.html` | Remove a judge role from the event. |
| `event_judge_scores` | `GET /api/v1/events/{slug}/judges/{judge_id}/scores` | that judge, or organizer/admin | read: server-rendered page or download link | One judge's reviews in one event. |
| `event_judge_update` | `PATCH /api/v1/events/{slug}/judges/{judge_id}` | organizer | `manage/judges.html` | Change which tracks a judge may review. |
| `event_judges` | `GET /api/v1/events/{slug}/judges` | organizer | read: server-rendered page or download link | List the judges of an event with their tracks. |
| `event_progress` | `GET /api/v1/events/{slug}/progress` | organizer | `manage/progress.html via js/progress.js` | Judging coverage of an event, counted from submitted reviews. |
| `event_rebalance` | `POST /api/v1/events/{slug}/assignments/rebalance` | organizer | `manage/command_center.html` | Preview or apply a rebalance of untouched assignments. |
| `event_rubric` | `GET /api/v1/events/{slug}/rubric` | judge or organizer | read: server-rendered page or download link | Read the event's active judging rubric. |
| `event_rubric_replace` | `PUT /api/v1/events/{slug}/rubric` | organizer (409 once scoring is locked) | `manage/rubric.html` | Replace the event's rubric with a new versioned one. |
| `judge_assignments` | `GET /api/v1/judge/assignments` | judge (own queue) | read: server-rendered page or download link | The caller's own review queue across every event. |
| `judge_conflict_declare` | `POST /api/v1/events/{slug}/judge/conflicts` | assigned judge | API/script use (no UI control) | Declare your own conflict with a team. |
| `judge_invite_accept` | `POST /api/v1/judge-invites/{token}/accept` | signed-in user matching the invite email | `judge/invite.html` | Accept a judge invite and gain the judge role. |
| `judge_review` | `GET /api/v1/events/{slug}/judge/reviews/{prj_id}` | assigned judge (own draft state) | read: server-rendered page or download link | Read one project with its rubric and the caller's review of it. |
| `judge_review_draft` | `PUT /api/v1/events/{slug}/judge/reviews/{prj_id}` | assigned judge (window open) | `judge/review.html` | Save or update your draft review without submitting it. |
| `judge_review_submit` | `POST /api/v1/events/{slug}/judge/reviews/{prj_id}/submit` | assigned judge (window open) | `judge/review.html` | Submit your review of one project for the results. |
| `judge_scores` | `GET /api/v1/judge/scores` | judge (own only) | read: server-rendered page or download link | The caller's own reviews across every event they judge. |
| `review_exclusion_create` | `POST /api/v1/events/{slug}/reviews/{review_id}/exclusion` | organizer (before publication) | `manage/results.html via js/consequences.js` | Exclude one review from the results, with a recorded reason. |
| `review_exclusion_delete` | `DELETE /api/v1/events/{slug}/reviews/{review_id}/exclusion` | organizer (before publication) | `manage/results.html via js/consequences.js` | Put an excluded review back into the results. |

### Pairwise mode (4)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `judge_comparison_create` | `POST /api/v1/events/{slug}/judge/comparisons` | assigned judge | `judge/pairwise.html` | Record one pairwise comparison, or an abstention. |
| `judge_comparison_undo` | `POST /api/v1/events/{slug}/judge/comparisons/{comparison_id}/undo` | assigned judge (own latest) | `judge/pairwise.html` | Retract your most recent pairwise comparison. |
| `judge_comparisons` | `GET /api/v1/events/{slug}/judge/comparisons` | assigned judge (own history) | read: server-rendered page or download link | Your own pairwise comparisons in an event. |
| `judge_pair_next` | `GET /api/v1/events/{slug}/judge/pairs/next` | assigned judge | read: server-rendered page or download link | The next pair this judge should compare (pairwise mode). |

### Community voting (17)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `community_ballot` | `GET /api/v1/events/{slug}/votes/ballot` | voter (link or email ticket) | `community/ballot.html (poll/read)` | Read the caller's ballot for an event, or start one. |
| `community_ballot_create` | `POST /api/v1/events/{slug}/votes/ballot` | voter (window open) | `js/voting.js` | Open a ballot before placing any votes. |
| `community_ballot_detail` | `GET /api/v1/events/{slug}/votes/ballot/{public_id}` | voter (own ballot) | read: server-rendered page or download link | Read one ballot by its public id. |
| `community_ballot_detail_create` | `POST /api/v1/events/{slug}/votes/ballot/{public_id}` | voter (window open) | API/script use (no UI control) | Open a ballot from the detail route (same operation as the collection). |
| `community_ballot_detail_submit` | `PUT /api/v1/events/{slug}/votes/ballot/{public_id}` | voter (window open) | `js/voting.js` | Submit the votes on a ballot addressed by its public id. |
| `community_ballot_submit` | `PUT /api/v1/events/{slug}/votes/ballot` | voter (window open) | API/script use (no UI control) | Submit the votes on a ballot. |
| `community_project_comment_create` | `POST /api/v1/events/{slug}/projects/{public_id}/comments` | signed-in user | `community/comments.html` | Post a comment on a project. |
| `community_project_comments` | `GET /api/v1/events/{slug}/projects/{public_id}/comments` | anyone | read: server-rendered page or download link | Read the public comment thread on one project. |
| `community_voting_config` | `GET /api/v1/events/{slug}/voting/config` | anyone (reads the rules) | read: server-rendered page or download link | Read the event's voting configuration. |
| `community_voting_config_update` | `PATCH /api/v1/events/{slug}/voting/config` | organizer | `manage/voting.html` | Change the access mode, ballot style, credits or window. |
| `community_voting_manage` | `GET /api/v1/events/{slug}/voting/manage` | organizer | read: server-rendered page or download link | Organizer view of ballots, abuse flags and the voting audit. |
| `community_voting_results` | `GET /api/v1/events/{slug}/voting/results` | anyone (hidden until voting closes) | read: server-rendered page or download link | Aggregate vote totals for an event. |
| `moderate_ballot` | `POST /api/v1/events/{slug}/voting/ballots/{public_id}/{action}` | organizer | `manage/voting.html` | Void or restore one ballot, with a recorded reason. |
| `moderate_comment` | `POST /api/v1/events/{slug}/comments/{public_id}/{action}` | organizer | `community/comments.html` | Hide or restore one comment, with a recorded reason. |
| `request_voting_email` | `POST /api/v1/events/{slug}/votes/email` | anyone (rate limited) | `community/ballot.html` | Request a voter link by email; the link itself never appears in the response. |
| `resolve_abuse_flag` | `POST /api/v1/events/{slug}/voting/flags/{public_id}/resolve` | organizer | `manage/voting.html` | Mark one voting abuse flag as resolved. |
| `verify_voting_email` | `POST /api/v1/events/{slug}/votes/email/verify` | anyone with the emailed link | `community/email_verify.html` | Exchange a delivered email ticket for a ballot. |

### Results (10)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `event_decision_room` | `GET /api/v1/events/{slug}/decision-room` | organizer | read: server-rendered page or download link | Organizer-only publication readiness and next actions |
| `feedback_release` | `POST /api/v1/events/{slug}/feedback-release` | organizer (audited) | `manage/results.html` | Release the score summary to each team (organizer). |
| `feedback_retract` | `DELETE /api/v1/events/{slug}/feedback-release` | organizer (audited) | `manage/results.html` | Stop releasing the score summary to teams (organizer). |
| `project_feedback` | `GET /api/v1/events/{slug}/projects/{project_id}/feedback` | own team after release, or organizer | read: server-rendered page or download link | The released score summary for your own project (team members). |
| `publication_detail` | `GET /api/v1/events/{slug}/results/publications/{pub_id}` | organizer | read: server-rendered page or download link | The full decision record for one publication (organizer). |
| `publication_verify` | `POST /api/v1/events/{slug}/results/publications/{pub_id}/verify` | organizer | `manage/decision_record.html` | Replay a publication from its stored inputs and compare with live data. |
| `results_consequences` | `POST /api/v1/events/{slug}/results/consequences` | organizer (read-only preview) | `manage/results.html via js/consequences.js` | Preview the exact consequences of a results action (organizer). |
| `results_preview` | `GET /api/v1/events/{slug}/results/preview` | organizer | read: server-rendered page or download link | Preview the ranking without publishing anything (organizer). |
| `results_public` | `GET /api/v1/events/{slug}/results` | anyone (after publication) | read: server-rendered page or download link | Public results for an event. |
| `results_publish` | `POST /api/v1/events/{slug}/results/publish` | organizer (note required; re-publish supersedes) | `manage/results.html via js/consequences.js` | Publish results (organizer). |

### Exports (4)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `community_votes_export` | `GET /api/v1/events/{slug}/exports/votes.csv` | organizer | read: server-rendered page or download link | Download the event's vote totals as CSV. |
| `event_export_csv` | `GET /api/v1/events/{slug}/exports/{kind}.csv` | organizer | read: server-rendered page or download link | Download one CSV export of an event. |
| `event_export_json` | `GET /api/v1/events/{slug}/exports/event.json` | organizer | read: server-rendered page or download link | Export an event as fixtures-shaped JSON (organizer). |
| `import_fixture` | `POST /api/v1/imports` | admin or host | API/script use (no UI control) | Import a fixtures-shaped JSON as a new event (admin/host). |

### Webhooks (5)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `event_webhook_replay` | `POST /api/v1/events/{slug}/webhook-deliveries/{public_id}/replay` | organizer | `manage/webhooks.html` | Re-queue one delivery that already went out. |
| `event_webhook_subscribe` | `POST /api/v1/events/{slug}/webhooks` | organizer | `manage/webhooks.html` | Subscribe a URL to an event's webhooks. |
| `event_webhook_test` | `POST /api/v1/events/{slug}/webhooks/{public_id}/test` | organizer | `manage/webhooks.html` | Queue a signed test delivery for one subscriber. |
| `event_webhook_unsubscribe` | `DELETE /api/v1/events/{slug}/webhooks/{public_id}` | organizer | `manage/webhooks.html` | Disable a webhook subscriber. |
| `event_webhooks` | `GET /api/v1/events/{slug}/webhooks` | organizer | read: server-rendered page or download link | List the event's webhook subscribers. |

### Certificates (4)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `event_certificates_export` | `GET /api/v1/events/{slug}/certificates.csv` | organizer | read: server-rendered page or download link | Download the certificate index with per-row verification links. |
| `event_judge_record_revoke` | `POST /api/v1/events/{slug}/judge-records/{record_id}/revoke` | organizer | `manage/certificates.html` | Revoke one signed judge record. |
| `event_judge_records_issue` | `POST /api/v1/events/{slug}/judge-records` | organizer | `manage/certificates.html` | Issue signed participation records for every judge who reviewed. |
| `record_verify` | `POST /api/v1/records/verify` | anyone (no auth) | `interop/verify.html` | Verify a signed judge record against the published key. |

### Audit (7)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `admin_audit_chain_checkpoint` | `GET /api/v1/admin/audit/{chain_id}/checkpoint` | admin | read: server-rendered page or download link | Download one archived audit chain by its public id (admin). |
| `admin_audit_chain_verify` | `GET /api/v1/admin/audit/{chain_id}/verify` | admin | read: server-rendered page or download link | Re-hash one archived audit chain by its public id (admin). |
| `admin_audit_checkpoint` | `GET /api/v1/admin/audit/checkpoint` | admin | read: server-rendered page or download link | Download the platform-wide audit checkpoint (admin). |
| `admin_audit_verify` | `GET /api/v1/admin/audit/verify` | admin | read: server-rendered page or download link | Re-hash the platform-wide audit chain (admin). |
| `audit_list` | `GET /api/v1/events/{slug}/audit` | organizer | read: server-rendered page or download link | Audit log for an event. |
| `event_audit_checkpoint` | `GET /api/v1/events/{slug}/audit/checkpoint` | organizer | read: server-rendered page or download link | Download an event's private audit checkpoint. |
| `event_audit_verify` | `GET /api/v1/events/{slug}/audit/verify` | organizer | read: server-rendered page or download link | Re-hash an event's audit chain and report consistency. |

### Outbox (offline messages) (2)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `admin_outbox_list` | `GET /api/v1/admin/outbox` | admin | `core/outbox.html` | Read the platform's private offline outbox (admins only). |
| `event_outbox_list` | `GET /api/v1/events/{slug}/outbox` | organizer | `core/outbox.html` | Read an event's private offline outbox (organizers and admins). |

### Core (1)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `integrity_probe` | `POST /api/v1/integrity/probe` | organizer or admin | `core/integrity.html` | Run the adversarial integrity probe (admin, or an event's organizer). |

### Tour (4)

| operationId | Method + path | Who may call it | UI control | Purpose |
|---|---|---|---|---|
| `tour_reset` | `POST /api/v1/tour/reset` | anyone (tour sandbox) | `js/tour.js` | Delete the current private copy and create a fresh sandbox. |
| `tour_start` | `POST /api/v1/tour/start` | anyone | `js/tour.js` | Start or resume a private, full-copy tour sandbox. |
| `tour_step` | `POST /api/v1/tour/step` | anyone (tour sandbox) | `js/tour.js` | Record the visitor's current step. |
| `tour_switch_role` | `POST /api/v1/tour/role` | anyone (tour sandbox) | `js/tour.js` | Switch to the designated organizer, judge or participant account. |

## Full lifecycle with curl

Run against a local server with demo data. Every command below was executed
against `manage.py runserver 127.0.0.1:8471` with `DEMO_MODE=1` on a seeded
database on 2026-09-28; the status codes and trimmed bodies are the real ones.
Replace the host with your own; the demo bearer tokens are in the table above
(`$ORG`, `$PART`, `$JA` below).

```bash
B=http://127.0.0.1:8471
ORG="Authorization: Bearer vd_demo_organizer_7f2a91c4e0b3"
PART="Authorization: Bearer vd_demo_participant_2e88f1d4a6c5"
JA="Authorization: Bearer vd_demo_judge_a_91bc5d2e8f10"

# Who am I, and what does "not authenticated" look like?
curl -s -H "$ORG" $B/api/v1/me
# 200 {"user":{"public_id":"usr_befuc3vhuj","display_name":"Event organizer",...}}
curl -s $B/api/v1/me
# 401 {"error":{"code":"not_authenticated","message":"detail: Authentication credentials were not provided.","fields":{}}}

# 1. Create an event whose submissions open tomorrow (201).
curl -s -X POST -H "$ORG" -H "Content-Type: application/json" \
  -d '{"name":"API Lifecycle","submissions_open_at":"2026-09-29T00:00:00Z","submissions_close_at":"2026-10-12T00:00:00Z","reviews_per_project":1}' \
  $B/api/v1/events
# 201 {"slug":"api-lifecycle","name":"API Lifecycle",...,"phase":"judging","submission_window_open":false,...}

# 2. Register before submissions open: refused before any other check (403).
curl -s -X POST -H "$PART" $B/api/v1/events/api-lifecycle/register
# 403 {"error":{"code":"window_closed","message":"Submissions for API Lifecycle closed at 2026-10-12T00:00:00+00:00.",...}}

# 3. Open submissions (organizer removes the lower bound) and register (201).
curl -s -X PATCH -H "$ORG" -H "Content-Type: application/json" \
  -d '{"submissions_open_at":null}' $B/api/v1/events/api-lifecycle
# 200 {...,"phase":"submissions_open","submission_window_open":true,...}
curl -s -X POST -H "$PART" $B/api/v1/events/api-lifecycle/register
# 201 {"public_id":"par_g5iumark6o","user":"priya1","role":"participant"}

# 4. Create a track (201) and a team (201).
curl -s -X POST -H "$ORG" -H "Content-Type: application/json" \
  -d '{"name":"General"}' $B/api/v1/events/api-lifecycle/tracks
# 201 {"public_id":"trk_ngml6qobw2","name":"General",...}
curl -s -X POST -H "$PART" -H "Content-Type: application/json" \
  -d '{"name":"Lifecycle Crew"}' $B/api/v1/events/api-lifecycle/teams
# 201 {"public_id":"tm_4r3gwrckir","name":"Lifecycle Crew",...}

# 5. Draft a project (201). Submitting the bare draft fails with the
#    missing fields named (400); fill them (200) and submit (200, revision 1).
curl -s -X POST -H "$PART" -H "Content-Type: application/json" \
  -d '{"title":"Lifecycle Beacon","summary":"End-to-end API walkthrough project"}' \
  $B/api/v1/events/api-lifecycle/projects
# 201 {"public_id":"prj_zxl5bxeoa2","title":"Lifecycle Beacon",...,"status":"draft","revision":0,...}
curl -s -X POST -H "$PART" $B/api/v1/events/api-lifecycle/projects/prj_zxl5bxeoa2/submit
# 400 {"error":{"code":"incomplete","message":"Fill in the highlighted fields before submitting.",
#   "fields":{"description":[...],"repo_url":[...],"track":[...]}}}
curl -s -X PATCH -H "$PART" -H "Content-Type: application/json" \
  -d '{"track":"trk_ngml6qobw2","description":"A beacon project created through the documented API.","repo_url":"https://example.org/lifecycle-beacon"}' \
  $B/api/v1/events/api-lifecycle/projects/prj_zxl5bxeoa2
# 200 {...,"status":"draft",...}
curl -s -X POST -H "$PART" $B/api/v1/events/api-lifecycle/projects/prj_zxl5bxeoa2/submit
# 200 {...,"status":"submitted","revision":1,...,"revisions":[{"number":1,...,"receipt":"cec91076"}],...}

# 6. Set the rubric (200), add two judges (201 each).
curl -s -X PUT -H "$ORG" -H "Content-Type: application/json" \
  -d '{"criteria":[{"key":"functionality","name":"Functionality","weight":1,"min_score":1,"max_score":5},{"key":"quality","name":"Quality","weight":1,"min_score":1,"max_score":5},{"key":"innovation","name":"Innovation","weight":1,"min_score":1,"max_score":5}]}' \
  $B/api/v1/events/api-lifecycle/rubric
# 200 {"version":2,"criteria":[...]}
curl -s -X POST -H "$ORG" -H "Content-Type: application/json" \
  -d '{"email":"diego.herrera@example.org","tracks":["trk_ngml6qobw2"]}' \
  $B/api/v1/events/api-lifecycle/judges
# 201 {"public_id":"jdg_tclyxerysr","name":"Diego Herrera",...}
curl -s -X POST -H "$ORG" -H "Content-Type: application/json" \
  -d '{"email":"jonas.vogel@example.org","tracks":["trk_ngml6qobw2"]}' \
  $B/api/v1/events/api-lifecycle/judges
# 201 {"public_id":"jdg_jtcu32mz6j","name":"Jonas Vogel",...}

# 7. Close submissions, which opens judging (200), then auto-assign (200).
curl -s -X POST -H "$ORG" $B/api/v1/events/api-lifecycle/close-submissions
# 200 {...,"judging_open_at":"2026-09-28T09:47:16.743624Z","judging_close_at":null,...}
curl -s -X POST -H "$ORG" -H "Content-Type: application/json" \
  -d '{"target":1,"dry_run":false}' $B/api/v1/events/api-lifecycle/assignments/auto
# 200 {"dry_run":false,"created":[{"public_id":"asg_74z3sd7bv2","judge":"jdg_tclyxerysr","project":"prj_zxl5bxeoa2"}],...}

# 8. Judge isolation holds (403), then the assigned judge drafts (200) and
#    submits (200) a review.
JB="Authorization: Bearer vd_demo_judge_b_44de0a7c3b92"
curl -s -H "$JB" $B/api/v1/events/api-lifecycle/judges/jdg_tclyxerysr/scores
# 403 {"error":{"code":"forbidden","message":"Only this judge and the organizers may read these scores.","fields":{}}}
curl -s -X PUT -H "$JA" -H "Content-Type: application/json" \
  -d '{"scores":{"functionality":4,"quality":5,"innovation":4},"comment":"Strong execution, clear write-up."}' \
  $B/api/v1/events/api-lifecycle/judge/reviews/prj_zxl5bxeoa2
# 200 {"review_id":"rev_enlaqsp4dq","status":"draft",...}
curl -s -X POST -H "$JA" -H "Content-Type: application/json" \
  -d '{"scores":{"functionality":4,"quality":5,"innovation":4}}' \
  $B/api/v1/events/api-lifecycle/judge/reviews/prj_zxl5bxeoa2/submit
# 200 {"review_id":"rev_enlaqsp4dq","status":"submitted",...}

# 9. Close judging (200) and preview the ranking (200).
curl -s -X POST -H "$ORG" $B/api/v1/events/api-lifecycle/close-judging
# 200 {...,"judging_close_at":"2026-09-28T09:47:32.161921Z",...}
curl -s -H "$ORG" $B/api/v1/events/api-lifecycle/results/preview
# 200 {"method":"normalized",...,"rows":[{"project_id":"prj_zxl5bxeoa2","title":"Lifecycle Beacon",
#   "raw_mean":83.33,"normalized":83.33,"rank":"1","status":"ranked",...}],...}

# 10. Preview the consequences of publishing (200), then publish (201).
curl -s -X POST -H "$ORG" -H "Content-Type: application/json" \
  -d '{"action":"publish"}' $B/api/v1/events/api-lifecycle/results/consequences
# 200 {"action":"publish",...,"sentence":"First publication: 1 ranked project, 0 awards.",...}
curl -s -X POST -H "$ORG" -H "Content-Type: application/json" \
  -d '{"note":"First official ranking for the API lifecycle walkthrough."}' \
  $B/api/v1/events/api-lifecycle/results/publish
# 201 {"pub_id":"pub_iutrqf2w4h","version":1,...,"method":"normalized",
#   "input_digest":"2ba2fbe8dc209a1b2dc08cc9af8f6bb95e962dd8826dcaa9ddf062ab83d13ab3",...}

# 11. Verify the publication (200: identical), read the public results (200).
curl -s -X POST -H "$ORG" $B/api/v1/events/api-lifecycle/results/publications/pub_iutrqf2w4h/verify
# 200 {"pub_id":"pub_iutrqf2w4h","verdict":"identical",...,"rows_match":true,"digest_match":true,...}
curl -s $B/api/v1/events/api-lifecycle/results
# 200 {"pub_id":"pub_iutrqf2w4h","version":1,...,"rows":[{"rank":"1",...}],...}

# 12. Issue judge records and fetch the certificate index (200 CSV),
#     then export the results CSV (200).
curl -s -X POST -H "$ORG" $B/api/v1/events/api-lifecycle/judge-records
# 200 {"records":["rec_tuwnlyplhr"],"count":1}
curl -s -H "$ORG" $B/api/v1/events/api-lifecycle/certificates.csv
# 200 kind,public_id,verification_code,url
#   participation,prj_zxl5bxeoa2,f5282e26bd14841b0b27,http://127.0.0.1:8471/certificates/verify/...?code=...
#   judge,jdg_tclyxerysr,0bd74c5e146cd3aec603,...
curl -s -H "$ORG" $B/api/v1/events/api-lifecycle/exports/results.csv
# 200 rank,project_id,title,team,track,reviews,raw_mean,status
#   1,prj_zxl5bxeoa2,Lifecycle Beacon,Lifecycle Crew,General,1,83.33,ranked

# 13. The schema and the docs this file describes (both 200).
curl -s -o /dev/null -w "schema:%{http_code}\n" $B/api/schema/
curl -s -o /dev/null -w "docs:%{http_code}\n" $B/api/docs/
```

The walkthrough used a disposable event (`api-lifecycle`) on an isolated
database, so the seeded fixture and demo events were untouched. The server was
a throwaway `manage.py runserver` started for the walkthrough and stopped
afterwards.

## How we keep this true

`tests/test_api_first.py` pins all of the above with five tests (run
`manage.py test tests.test_api_first`):

1. **Strict schema** — builds the schema in-process with the same generator
   settings `manage.py spectacular` uses and asserts it validates with zero
   warnings and zero errors.
2. **Drift** — `docs/openapi.yaml` must equal the freshly generated schema
   (parsed data, not bytes). Regenerate with
   `.venv\Scripts\python.exe manage.py spectacular --file docs/openapi.yaml --validate --fail-on-warn`.
3. **UI parity** — every `data-api-url` (+ `data-api-method`, default POST) in
   `src/templates/`, plus the `data-execute-url` / consequences / publish /
   ballot / poll URLs the page JS drives, plus every `/api/v1/` literal in
   `src/static/js/`, must match an operation in the schema.
4. **Completeness** — every operation has an `operationId`, a summary or
   description, a 2xx with a schema (`204` and file downloads exempt by rule),
   and error responses using the shared `ErrorEnvelope` component.
5. **Docs coverage** — every `operationId` in the schema appears in this file,
   so the table cannot silently fall behind the code.

The committed schema is generated with validation and zero warnings. Every
operation, including tour, outbox, decision-room and voting-email operations,
documents errors through the shared `ErrorEnvelope` component. The API First
tests contain no operation, generator-error, or UI-operation allow-lists.
