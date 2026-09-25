# Packet P2-JA: judging services + API (rubric, judges, invites, conflicts, assignments, reviews, progress)

Read AGENTS.md and BUILD-SPEC sections 2, 3, 4 (judging.*), 5, 6 (Judging, Scores), 10, 11. This packet is API + services + tests only (UI is P2-JUI / P2-OUI). Keep P0's two score endpoints and their check order intact.

## Do
1. `src/judging/services.py`:
   - Rubric: `replace_rubric(actor, event, criteria)` (organizer; 409 `scoring_locked` if `event.scoring_locked_at`; 1–10 criteria; unique keys; weight > 0; max > min; bumps version). 
   - Judges: `add_judge(actor, event, email, tracks)` (existing user; role conflict → 409), `update_judge_tracks`, `remove_judge` (409 `judge_has_reviews` if any submitted review; otherwise deletes their assignments + drafts). Judge public_id `jdg_` + random.
   - Invites: `create_judge_invites(actor, event, emails, tracks)` → links `/judge-invite/{token}` (expires in 14 days), `accept_judge_invite(actor, token)` (logged-in email must equal invite email → else 403 `invite_email_mismatch`; revoked/expired/used → 410; role conflict → 409).
   - Conflicts: `declare_conflict(actor_judge, event, team, reason)` and organizer `add_conflict`; declaring a conflict removes an existing assignment if it has no submitted review (else 409 `review_exists`, organizer decides).
   - Assignments: `assign_batch(actor, event, judge_ids, project_ids)` (every eligible judge×project pair; returns created + skipped-with-reason), `remove_assignment` (409 if review submitted), `auto_assign(actor, event, target, max_load, seed, dry_run)` implementing BUILD-SPEC 10 in `src/judging/assign.py` as a pure function over plain data (unit-testable), apply re-validates in a transaction and records an AssignmentBatch.
   - Reviews: `save_review_draft(actor, event, project, scores, comment)` and `submit_review(...)`: caller must hold an active assignment for that project in this event; judging window open (else 403 `judging_closed`); project status submitted; values integers within each criterion's range; submit requires every criterion; on first submitted review in the event set `scoring_locked_at` (atomic). Store `rubric_version` and `project_revision`. Resubmission allowed while the window is open (audit old → new).
   - Exclusions: `exclude_review(actor, review, reason)` / `include_review` (organizer; 409 after any publication unless the next publication will supersede, i.e. allowed but recorded).
   - Every change audited with a readable summary ("Diego Herrera submitted a review of Glass Signal").
2. `src/judging/policy.py`: `visible_reviews(user, event)` (organizer all; judge own; others none), `visible_assignments(user, event)`, `judge_can_view_project(user, event, project)` (assigned only), `progress(event)` (BUILD-SPEC 11 data).
3. API (`src/judging/api.py`, `api_urls.py`): every Judging endpoint in BUILD-SPEC 6 including `GET /judge/assignments` (own, across events: event, project summary incl. private answers for assigned projects only, review status), `GET|PUT /events/{slug}/judge/reviews/{prj_id}`, `POST .../submit`, `GET /events/{slug}/progress` (organizer), auto-assign with `dry_run` default true.

## Tests
`tests/test_isolation.py` (the matrix that wins or loses points): judge A cannot read judge B's review via review endpoint, scores endpoint, `?judge=`, assignment list or progress (403); judge cannot open/save a review for an unassigned project (403) or a project in another track even if they guess the id (403); judge cannot see private answers of unassigned projects; participant/visitor get 403/401 on every judging endpoint; organizer of another event gets 403 on this event's judging data; admin allowed.
`tests/test_judging.py`: rubric replace ok before lock, 409 after first submitted review; review draft/submit validation (missing criterion, out of range, non-integer); judging closed → 403; scoring_locked_at set on first submit; judge invite email mismatch 403, accept ok; role conflict 409; conflict removes unreviewed assignment; remove judge with reviews 409.
`tests/test_assignment.py`: `assign.py` pure-function tests: respects tracks and conflicts, never double-assigns, balances load (max−min ≤ 1 when feasible), reports unfillable needs with reason, deterministic for a seed; fixture "fill gaps" to target 3 leaves no eligible project under target unless the track lacks judges.

## Files you own
src/judging/** (models.py only if a migration is truly needed), tests/test_isolation.py, tests/test_judging.py, tests/test_assignment.py.

## Hardening items (BUILD-SPEC 16) in this packet
- Cross-event checks on assignments (judge and project same event), conflicts (team in event), judge tracks (tracks of event) → 400 `cross_event`, with tests.
- Judge invite links use `/judge-invite?token=…`.
- Coverage/progress counts submitted reviews as evidence; assignments alone are "pending", never counted as reviewed.
