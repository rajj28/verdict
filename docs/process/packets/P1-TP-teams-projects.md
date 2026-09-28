# Packet P1-TP: teams, invite links, submissions (draft → submit → edit until deadline), gallery, project pages

Read AGENTS.md and BUILD-SPEC sections 2, 3, 4 (teams.*, projects.*), 5, 6 (Teams, Projects), 12. Templates extend `base.html`, use UI kit class names exactly as listed in the spec, and `api-forms.js` data attributes for every write.

## Do
1. `src/teams/services.py`: `create_team(actor, event, name)` (window open; actor registered as participant or auto-registers; not already in a team → 409 `already_in_team`; name unique per event case-insensitive → 400), `rotate_invite(actor, team)` (member only; returns absolute link `/invite/{token}`; expires at min(now+7d, submissions_close_at)), `accept_invite(actor, token)` (atomic: lock team; invite valid/not revoked/not expired → else 410 `invite_invalid`; window open; user has no other role in event → 409 `role_conflict`; not in a team → 409 `already_in_team`; size < max_team_size → else 409 `team_full`; auto-register participant), `leave_team(actor, team)` (window open; owner leaving passes ownership to the earliest remaining member; last member leaving deletes the team if its project is a draft or absent; if the project is submitted → 409 `withdraw_first`), `remove_member(actor, team, user)` (owner only). `src/teams/policy.py`: `visible_teams(user, event)` (organizer all, participant own, others none).
2. `src/projects/services.py` (extend P0's `create_project`; keep its check order): `update_project` (team member; window open first; status draft/submitted; field validation per BUILD-SPEC 2.9; while submitted, each save creates a ProjectRevision and bumps `revision`), `submit_project` (required fields + required answers → 400 with per-field messages; sets first/last_submitted_at; revision snapshot), `withdraw_project` (owner, window open), `disqualify_project` (organizer, reason required, any time), images add/delete (max 6, Pillow-verified, ≤5 MB, jpeg/png/webp/gif, random filenames), `save_answers`. Window check (403 `window_closed`) comes first on every participant write, including images and answers.
   `src/projects/policy.py`: `public_projects(event=None)` (submitted only, event gallery_public, never superseded/withdrawn/disqualified/draft), `visible_project(user, event, public_id)` (team members and organizers also see drafts and private answers; judges see private answers only for assigned projects), gallery search/filter helpers.
3. API (BUILD-SPEC 6 Teams + Projects) in `src/teams/api.py`, `src/projects/api.py`. `GET /api/v1/projects` is the public gallery JSON (q, event, track, tag, sort=title|newest, page).
4. Pages:
   - `/projects` gallery (replace P0's minimal template): search box, filters (event, track, tag), sort (title default, newest), 48 per page with pagination, `.project-card` grid, result count, empty state. Must keep fixture titles in the server-rendered HTML sorted by title.
   - `/events/{slug}/projects` (same gallery scoped to the event) and `/events/{slug}/projects/{id}` detail: title, summary, thumbnail + image gallery, description, links (demo video, repo, live), tags, track, team display names (never emails), public custom answers, submitted time and revision number. Drafts visible only to the team and organizers (others 404).
   - `/events/{slug}/team`: no team → create form + "join with a link" explainer; has team → members with owner badge, invite link with copy + rotate, leave/remove actions, project status + link to editor.
   - `/events/{slug}/submission`: editor for all fields + custom questions + images; shows deadline countdown, required-field checklist that updates as you type, status badge, revision history list (number + time), Save draft / Submit / Withdraw; after close: read-only with a "Submissions closed at …" banner.
   - `/invite/{token}`: shows team + event, Join button (login first if anonymous, then return here), clear errors for full/expired/closed/role conflict.

## Tests
`tests/test_teams.py`: create/join/leave/full/expired/rotated-old-token/role conflict/closed window/one team per event (DB constraint too).
`tests/test_projects.py`: create draft → edit → submit (required fields) → edit creates revision → withdraw; other team member 403; other team 403/404 on drafts; organizer disqualify; images validation (rejects svg/oversize); gallery excludes drafts/superseded/withdrawn and supports q/track/tag filters; fixture gallery contains "Glass Signal" and not a duplicate "Dry Harbour" twice.
`tests/test_deadlines.py` (patch `core.clock.now`): one second before close → allowed; exactly at close → 403 window_closed; after close every participant write (create, update, submit, images, answers, withdraw, team create/join/leave) → 403; organizer disqualify still allowed after close.

## Files you own
src/teams/** and src/projects/** (not models.py unless a migration is truly needed), src/templates/teams/**, src/templates/projects/**, tests/test_teams.py, tests/test_projects.py, tests/test_deadlines.py.

## Hardening items (BUILD-SPEC 16) in this packet
- PATCH accepts `base_updated_at`; mismatch → 409 `stale_edit`; the editor sends it and explains the conflict.
- Submission receipt: every revision stores `digest` (sha256 of canonical snapshot JSON); editor shows "Submitted · revision N · time · receipt abcd1234".
- Invite links use `/invite?token=…` (query string, not path).
- `project.track` and answer questions must belong to the project's event (400 `cross_event`).
- Image and thumbnail URLs go through the authorized media view (P0b); drafts' images 404 for the public.
Add tests for each (stale edit, digest stability, cross_event, draft image 404 anonymously).
