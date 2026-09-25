# Packet P1-UI: design system, layout, accounts UI + API

P0 is done (models, import, checker endpoints, Docker). Read AGENTS.md and BUILD-SPEC sections 2, 3, 6 (Accounts), 8 (demo login), 12 (UI pages + UI kit).

## Do
1. Vendor Bootstrap 5.3.x: `bootstrap.min.css` and `bootstrap.bundle.min.js` (download once from the npm package or jsdelivr now; commit the files) into `src/static/vendor/bootstrap/` with its LICENSE. No runtime CDN.
2. `src/static/css/app.css`: the UI kit from BUILD-SPEC 12 (page-header, stat-tile/stat-grid, status badges, project-card with initials placeholder, empty-state, countdown, data-table, role pills), brand colours, dark mode support. Make it look like a polished product, not a default Bootstrap demo.
3. `src/static/js/`: `theme.js` (dark/light via prefers-color-scheme), `api-forms.js` (full contract in BUILD-SPEC 12: JSON/multipart, CSRF from `csrftoken` cookie, `data-type` coercion, field + non-field errors, toast, `data-success` handling, standalone buttons, Bootstrap-modal confirm), `time.js` (local times + countdowns for `<time data-countdown>`). No inline scripts anywhere.
4. `src/templates/base.html` (replace the minimal one; keep blocks `title`, `content`, `page_actions`, `extra_js`): top nav with brand "VERDICT", links Gallery / Events, and role-aware links (Judge console if the user judges anywhere, Manage for organizer events, Admin for admins), user menu (My dashboard, API tokens, Sign out), demo-mode ribbon when DEMO_MODE, toast container, footer ("Self-hosted · MIT · API docs" link to /api/docs/). A context processor supplies the user's roles.
5. Accounts API (BUILD-SPEC 6) in `src/accounts/api.py` + `services.py`: login (session; throttled 10 per 15 min per IP+email → 429), logout, register (email unique, password ≥ 8, display name), `GET /me` (profile + roles per event), tokens list/create (plaintext once)/revoke, `POST /auth/demo-login {role}` (only when DEMO_MODE; logs in the seeded account for admin/organizer/judge_a/judge_b/participant), admin users list + PATCH (is_host/is_admin/is_active; admin only; an admin cannot remove their own admin flag). Audit every change.
6. Pages: `/` home (hero with the pitch, live stats from the DB: events, projects, judges; current events cards; CTA to gallery), `/login` (form + demo quick-login buttons when DEMO_MODE, each labelled with role and what it can do), `/register`, `/me` (my events with role pills; per event: team + project status for participants, assignment progress for judges, manage link for organizers), `/me/tokens` (create/revoke tokens, show plaintext once with copy button), `/admin-panel/` (users table with search + toggles, events list), error pages 403/404/500 using the layout.

## Tests
`tests/test_accounts_api.py`: login ok/bad password/throttle 429; register validation; demo-login 404 when DEMO_MODE off; token create shows plaintext once and list never shows it; revoked token → 401; non-admin PATCH users → 403; admin can toggle is_host; pages `/`, `/login`, `/me` (auth), `/admin-panel/` (admin only, 403 otherwise) render.

## Files you own
src/static/**, src/templates/base.html, src/templates/{home.html,accounts/**,errors/**,admin_panel/**}, src/accounts/**, src/core/context_processors.py, src/verdict/settings.py (only context processor + template settings), tests/test_accounts_api.py.

## Hardening items (BUILD-SPEC 16) in this packet
- `base.html` has `<meta name="csrf-token" content="{{ csrf_token }}">`; api-forms.js uses it.
- Login: generic error ("Email or password is incorrect"), validated `next`.
- Offline password reset: admin panel "Generate reset link" (`/reset?token=…`, 24 h, single use, stored hashed in PasswordResetToken) + `/reset` page; change-password form on `/me` (current password required). API endpoints for both; tests in tests/test_accounts_api.py.
