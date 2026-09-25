# Packet P2-UI: judge console + organizer judging pages (split into two parallel workers: JUI and OUI)

Requires P2-JA and P2-RX APIs. Read AGENTS.md, BUILD-SPEC sections 3, 9, 10, 11, 12. HTML views only read (via policy helpers); writes use `api-forms.js` or small page scripts calling the same API. No inline scripts.

## JUI (judge worker) owns src/judging/views.py, src/judging/urls.py, src/templates/judge/**, src/static/js/judge-console.js, tests/test_judge_pages.py
- `/judge`: assignments across events grouped by event, progress bar (submitted / assigned), status badges, "continue where you left off" (first unreviewed), deadline countdown for judging close. Only the caller's data.
- `/judge/{slug}/review/{prj}`: two-column layout. Left: project evidence (title, summary, description, links, images, tags, track, private + public custom answers, revision number). Right: rubric card per criterion (name, description, weight %, radio buttons min..max with labels), comment box (private to organizers), live weighted score preview, Save draft (autosave 2 s after last change with a visible "Saved · 12:04" state and an offline/error state), Submit (enabled only when every criterion is set), Previous/Next assignment. Keyboard: 1–9 set the focused criterion, ↑/↓ move between criteria, `s` submit, `n`/`p` next/previous; shortcut help popover. After judging closes: read-only. Unassigned project → 403 page (enforced in the view via policy).
- `/judge-invite/{token}`: accept page (login/register first; email mismatch explained).
- Tests: judge pages 200 for own assignments, 403 for unassigned/other-track projects, participant 403, anonymous redirect to login; no other judge's name/score appears in any judge page HTML.

## OUI (organizer worker) owns src/templates/manage/{rubric,judges,assignments,progress,results,exports,audit}.html, src/results/views.py, src/results/urls.py, src/audit/views.py, src/audit/urls.py, src/static/js/{progress.js,results.js}, templates/events/results_public.html, tests/test_manage_pages.py
- `/manage/{slug}/rubric`: criteria editor (key, name, description, weight, min, max), weights shown as % of total, locked state explained (why and since when).
- `/manage/{slug}/judges`: judges table (name, tracks, assigned, submitted, status), add existing user, invite by email (shows generated links to copy since there is no mail server), edit tracks, conflicts list + add.
- `/manage/{slug}/assignments`: batch assign (filter by track, pick judges + projects), auto-assign form (target, max load) → preview table (judge, project, track, reason) + unfillable needs → Apply; current assignments table with filters; per-project coverage.
- `/manage/{slug}/progress`: live dashboard (refresh every 15 s via progress API): stat tiles, judges table with "not started" first, per-project coverage bars, per-track coverage.
- `/manage/{slug}/results`: official method badge + locked parameters; tabs: Ranking (rank, Δ vs raw, project, track, n, raw, normalized, derived BT, flags), Judges (offsets, labels, n, spread, constant-scorer and single-review warnings), Explain (pick a project → per-review breakdown), Data issues (superseded, excluded, under-reviewed with "fill gaps" link), Publish (close judging button if open, note field, publish; history of publications with digests).
- `/manage/{slug}/exports`: every CSV + event.json with one-line descriptions. `/manage/{slug}/audit`: filterable readable log (time, actor with role pill, summary), CSV link.
- `/events/{slug}/results` public page after publication (ranking + per-track + prizes list), "not yet published" state otherwise.
- Tests: every manage page 200 for organizer and 403 for judge/participant/other-event organizer; public results 404/"not published" before publish and visible after; public results HTML contains no judge names.

## Hardening items (BUILD-SPEC 16) in this packet
- OUI: publication history links to `/manage/{slug}/results/publications/{pub}` decision record page with a Verify button showing the verdict; publish form includes the acknowledge-unranked checkbox naming the projects.
