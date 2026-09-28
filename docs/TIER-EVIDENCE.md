# Tier evidence: what `scripts/verify_tiers.py` proves and how to check it by hand

`scripts/verify_tiers.py` is the hand-judged-tier counterpart to `run.py`: stdlib
only, same `.dogfood.toml`, creates its own disposable events (never touches the
fixture event), and prints one indented `METHOD path  as <actor>  -> status`
line per request under each `TIER  label ..... PASS|FAIL` line, plus a summary
line per tier. Query strings and response bodies are never printed, so bearer
tokens, voting link tokens and email tickets cannot leak into the report.

Run it against a running portal. In the default configuration
(`WEBHOOKS_ALLOW_PRIVATE=0`) the SSRF guard refuses the checker's local webhook
receiver: the checker records that refusal as a PASS and prints the two delivery
checks as `SKIPPED` with the reason. Start the portal with
`WEBHOOKS_ALLOW_PRIVATE=1` to watch a real signed delivery to the local receiver
(its logic mirrors `scripts/webhook_receiver.py`). Re-running within ten minutes
also prints the comment checks as `SKIPPED`: the seeded participant has used up the
five-comments-per-ten-minutes limit, which is itself a T3 control. Skips are never
counted as passes.

```
python scripts/verify_tiers.py .dogfood.toml | tee tier-evidence-report.txt
```

The committed `tier-evidence-report.txt` is that output. Every check below names
the label printed by the script, the file that enforces the rule, and a
one-minute manual check. `ORG`, `PART`, `JA` below are the organizer,
participant and judge_a bearer values from `.dogfood.toml`.

## T1 (also covered by `run.py`; depth checks live here)

| Bullet | Check name(s) in `verify_tiers.py` | Implements it | Verify by hand in one minute |
|---|---|---|---|
| Public gallery renders | `public gallery` | `src/projects/views.py`, `src/projects/policy.py` | Open `/projects` in a private window; fixture titles are listed. |
| Gallery search/filters | `gallery search returns matching projects` | `src/projects/policy.py` (`filter_gallery`) | Open `/projects?q=Glass`; only matching titles show. |
| Create event / track / team / project draft | `create disposable verification event`, `create event track`, `participant creates a team`, `create project draft` | `src/events/services.py`, `src/teams/services.py`, `src/projects/services.py` | `curl -X POST $BASE/api/v1/events -H "Authorization: Bearer $ORG" -d '{"name":"x","submissions_close_at":"2030-01-01T00:00:00Z"}'` returns 201 with a slug. |
| Draft to submitted to edited, then closed | `submit project draft`, `edit submitted project`, `close submissions` | `src/projects/services.py` (`submit_project`) | Submit, then PATCH the title; both return 200 and the revision list grows on `/events/{slug}/submission`. |
| Invite-link join | `create invite link`, `invite-link join` | `src/teams/services.py`, `src/teams/api.py` | POST `.../teams/{id}/invite` as organizer, open the returned `/invite?token=…` link in a second browser profile, join. |
| Edit after close is refused | `edit after close is refused` | `src/projects/services.py` (`check_submission_window`, 403 `window_closed`) | POST `.../close-submissions`, then PATCH the project: 403 with code `window_closed`. |

## T2 (also covered by `run.py`; depth checks live here)

| Bullet | Check name(s) in `verify_tiers.py` | Implements it | Verify by hand in one minute |
|---|---|---|---|
| Track isolation for judges | `other-track review isolation` | `src/judging/policy.py` (`judge_project`) | As judge_b, `GET /api/v1/events/sample-hack-2026/judge/reviews/prj_01` returns 403. |
| Rubric lock after first review | `locked rubric rejects edits` | `src/judging/services.py` (`replace_rubric`, 409 `scoring_locked`) | `PUT .../rubric` as organizer returns 409 `scoring_locked`. |
| Organizer progress | `organizer progress` | `src/judging/api.py` (`EventProgressView`) | `GET .../progress` as organizer returns per-judge/per-project coverage; page `/manage/{slug}/progress` shows it. |
| Every CSV stage | `participants/teams/projects/judges/assignments/reviews/progress/results/audit CSV export` | `src/interop/exports.py`, `src/interop/api.py` | `GET .../exports/reviews.csv` as organizer downloads CSV; as judge it is 403. |
| Normalization output present | `normalization preview is available` | `src/results/engine.py`, `src/results/api.py` | `GET .../results/preview` as organizer returns rows with a `normalized` score. |

## T3 (community voting; hand-judged)

| Bullet | Check name(s) in `verify_tiers.py` | Implements it | Verify by hand in one minute |
|---|---|---|---|
| Open-link voting | `open-link voting opens a ballot` | `src/community/services.py` (`create_ballot`, `VotingAccess.OPEN_LINK`) | `GET .../voting/config` as organizer for the link token, then `POST .../votes/ballot?v=TOKEN` anonymously returns 201 and sets a `verdict_voter` cookie. |
| Email-gated voting via offline outbox | `email link is queued offline`, `offline outbox holds the link`, `email ticket opens a ballot` | `src/community/services.py` (`request_email_verification`), `src/core/mail.py` (`OutboxBackend`), `src/core/outbox_api.py` | `POST .../votes/email` returns 202 `queued`; organizer reads `GET .../outbox`, hands the link over out-of-band; `POST .../votes/email/verify` with the ticket returns 201. |
| Authenticated voting | `authenticated voting opens a ballot` | `src/community/services.py` (`VotingAccess.AUTHENTICATED`) | `POST .../votes/ballot` with a participant bearer token returns 201. |
| Quadratic budget enforced | `quadratic budget is enforced`, `ballot within budget is accepted` | `src/community/services.py` (`submit_ballot`, 400 `budget_exceeded`) | PUT 5 votes on one project with 16 credits returns 400 `budget_exceeded`; 2+2 returns 200. |
| Results hidden from non-organizers during the window (API, page, export) | `results hidden from voters during window`, `results hidden from visitors during window`, `results page hidden during window`, `votes export hidden from voters`, `organizer still sees live tallies`, `organizer votes export` | `src/community/policy.py` (`visible_tallies`), `src/community/views.py`, `src/community/api.py` (`VotesExportView`) | Before the close, `GET .../voting/results` as a voter is 403 `results_hidden`, the `/events/{slug}/voting/results` page is 403, `votes.csv` as a voter is 403; as organizer all three return 200. |
| Ballot order random per voter, stable per ballot | `ballot order is stable per ballot, random per voter` | `src/community/services.py` (`ballot_order`, seeded shuffle) | GET the same ballot twice: identical project order. Two voters' ballots: different order. |
| Rate limit 429 | `comment rate limit answers 429` (also `fifth comment still allowed`) | `src/community/services.py` (`_comment_rate_limit`, 5 per 10 min) | POST six comments quickly: the sixth returns 429. |
| Duplicate ballot refused | `duplicate ballot is refused`, `open-link duplicate is refused` | `src/community/services.py` (`_reject_duplicate`, 409 `duplicate_voter`) | POST `.../votes/ballot` twice as the same voter: the second returns 409. |
| Comments create / moderate / hide | `comment create`, `comment hide with reason`, `hidden comment leaves the thread`, `comment restore` | `src/community/services.py`, `src/community/api.py` (`ProjectCommentsView`, `ModerateCommentView`) | POST a comment (201), hide it as organizer (200, reason required), list the thread (empty), restore it (200). |
| Abuse audit trail readable | `abuse audit trail is readable`, `event audit log is readable` | `src/community/api.py` (`VotingManagementView`), `src/audit/api.py` | `GET .../voting/manage` as organizer lists ballots, flags and the voting audit; `GET .../audit` lists the event chain. |

## T4 (interop and trust; hand-judged)

| Bullet | Check name(s) in `verify_tiers.py` | Implements it | Verify by hand in one minute |
|---|---|---|---|
| Every UI action has a documented endpoint | `schema documents every UI endpoint`, `API docs page is served` | `drf-spectacular` via `src/verdict/urls.py` (`/api/schema/`, `/api/docs/`), `extend_schema` on every view | `GET /api/schema/` contains `community_ballot`, `record_verify`, `event_export_json`, `import_fixture`; open `/api/docs/` in a browser. |
| Webhook delivery with valid HMAC | `webhook endpoint is created`, `webhook delivery carries a valid HMAC signature`, `webhook secret is never listed` | `src/interop/webhooks.py` (`_signature`, `_send`), `src/interop/services.py`, `scripts/webhook_receiver.py` | Start `WEBHOOK_SECRET=… python scripts/webhook_receiver.py`, subscribe its URL, POST `.../webhooks/{id}/test`; the receiver prints `valid_signature: True`. |
| Certificate access (owner yes, stranger no) | `certificate owner can read`, `certificate stranger is refused` | `src/interop/services.py` (`certificate_access`), `src/interop/views.py` (`certificate_page`) | Open `/events/{slug}/certificates/participation/{prj}` as a team member (200) and as an unrelated user (403). |
| Signed judge record verifies; tampered copy fails | `signed judge record is issued`, `signed judge record is public`, `signed judge record verifies`, `tampered judge record fails` | `src/interop/signing.py`, `src/interop/services.py` (`issue_judge_records`), `src/interop/api.py` (`RecordVerifyView`) | Issue records after judging closes, `GET /records/{id}?format=json`, `POST /api/v1/records/verify` with the document (`valid: true`), change one letter and re-post (`valid: false`). |
| Embed route public, framing relaxed only there | `embed is public with relaxed framing`, `framing stays denied outside the embed` | `src/interop/views.py` (`embed_gallery`), `src/core/middleware.py` (`EMBED_CSP`) | `curl -i $BASE/embed/{slug}` returns 200 with `frame-ancestors *` and no `Set-Cookie`; `curl -i $BASE/projects` returns `frame-ancestors 'none'`. |
| event.json export then import round trip with equal counts | `event.json export is produced`, `event.json round trip keeps equal counts` | `src/interop/exports.py` (`event_json`), `src/interop/importer.py` (`import_fixture`) | `GET .../exports/event.json`, `POST /api/v1/imports` with that body: the report counts for projects, judges, teams and reviews equal the exported list lengths. |
