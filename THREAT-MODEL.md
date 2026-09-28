# VERDICT threat model

Every "stopped" claim below names the file and function that enforces it and a
test that exists and passes on PostgreSQL. Anything without a passing test is
labelled "partially mitigated" or "not stopped". Product terms are used
throughout: `public_id`, publication version, Verify, review exclusion.

## 1. Scope and assets

VERDICT protects:

- Community ballots and scores before release (one ballot per identity, tallies
  hidden until the window closes).
- Private judge notes (`Review.comment` is judge/organizer-only and is never
  released, even after publication).
- Participant emails (never shown publicly, never in exports).
- Result integrity (canonical inputs plus sha256 digest per publication,
  two-part Verify, versioned publications that supersede).
- The audit log (hash-chained, append-only).
- Signing keys and the secret key (API tokens stored hashed; certificate codes
  are HMACs; judge records use Ed25519).
- Availability during judging (login throttles, ballot/comment rate limits; no
  DDoS protection beyond that — see section 6).

Out of scope for the code: the host, the network, and the database superuser
(see section 6, items 7 and 9).

## 2. Actors and trust boundaries

| Actor | What they can do | Trust |
|---|---|---|
| Anonymous visitor | Gallery, published results, open-link ballots, register/login | Untrusted |
| Participant | Own team and project only, while windows are open | Untrusted outside own team |
| Judge | Own assignments and own reviews only | Semi-trusted; may be biased, may collude |
| Organizer (event-scoped) | All data in their own event; publish, exclude reviews, disqualify | Trusted within their event, not across events |
| Admin (platform) | Organizer powers on every event; user management | Fully trusted |
| Webhook receiver | Receives signed POSTs from VERDICT | Untrusted input from VERDICT's side is signed |
| Host operator / DB superuser | Reads and writes everything, bypasses the app | Outside the threat model; limits documented in section 6 |

```
+-----------+   API (auth: session+CSRF or Bearer)   +------------------+
| visitor / | ------------------------------------> | services.py      |
| participant / judge / organizer / admin           | (writes, rules)  |
+-----------+                                       | policy.py        |
                                                    | (read scoping)   |
                                                    +--------+---------+
                                                             |
                                                    +--------v---------+
                                                    | PostgreSQL       |
                                                    | audit hash chain |
                                                    | publication      |
                                                    | digests          |
                                                    +------------------+
Boundary 1: browser <-> API (CSRF, CSP, throttles). Boundary 2: role/event
scoping in services.py/policy.py (never the template). Boundary 3: app <->
DB/host (a superuser bypasses every row below).
```

One role per person per event is structural: `events/models.py` (`EventRole`
`UniqueConstraint(event, user)`), enforced on writes by
`src/judging/services.py:add_judge` / `accept_judge_invite` (409
`role_conflict`). Proof: `tests.test_judging`
`test_add_judge_role_conflict_returns_409` and `tests.test_events`
`test_add_user_with_existing_other_role_is_409`.

## 3. The four named attacks

### 3.1 Sybil votes

Attack: one human casts many community ballots by inventing identities —
fresh accounts, many email addresses, cleared cookies or new devices — to move
a community tally.

What VERDICT does:

- Each access mode has an identity gate and exactly one ballot per identity,
  enforced in `src/community/services.py:create_ballot` (via
  `_get_or_create_ballot` / `_existing_voter` under `select_for_update`, with
  `_reject_duplicate` returning 409 `duplicate_voter`) on top of DB
  constraints in `src/community/models.py` (`Voter`: unique per
  event+user, event+email-hash, event+device-hash; `Ballot.voter` OneToOne).
- Judges and organizers of the event cannot vote at all:
  `src/community/services.py:_ensure_can_vote` returns 409 `role_conflict`.
- Email identities are normalized by `src/community/services.py:normalize_email`
  (lowercase; Gmail/Googlemail dots removed, plus-suffixes stripped) and only a
  keyed hash is stored; email access mode sends a signed expiring link that
  verifies control of the mailbox.

Proof (all in `tests.test_community`, all passing):

- `test_authenticated_identity_cannot_create_a_second_ballot` (allowed first
  ballot, 409 on the second).
- `test_open_link_uses_a_device_cookie_and_rejects_second_ballot`.
- `test_email_access_sends_verification_and_allows_one_verified_ballot`.
- `test_window_and_role_conflict_are_enforced` (judge/organizer refused).
- `test_gmail_plus_address_heuristic` (normalization mapping).
- Probe: `manage.py integrity_probe` case "The same authenticated voter cannot
  create a second ballot" (REFUSED 409); `tests.test_probe`
  `test_every_current_attack_is_refused_and_fixture_data_rolls_back`.

What it does not stop (residual): an attacker with many real email addresses
passes the email gate once per address — open-link mode only deters casual
repeats (clearing cookies or changing devices defeats the device hash). This is
**partially mitigated**: one human, one vote is not established in open-link
mode. Operational answer: use authenticated or email access mode for tallies
that matter, and watch the burst flags (section 3.2).

### 3.2 Ballot stuffing

Attack: duplicate ballots, replayed or concurrently submitted ballots,
over-budget quadratic ballots, scripted floods, and early tallies that invite
bandwagons.

What VERDICT does:

- Duplicate creation is refused: `src/community/services.py:create_ballot`
  validates the full replacement under the event and ballot row locks and
  `submit_ballot` recomputes `sum(votes^2)` against the configured credits
  before writing (over-budget yields `budget_exceeded`, leaving the previous
  ballot untouched).
- Tallies are hidden until the window closes:
  `src/community/policy.py:results_visible` / `require_visible_results` (403
  `results_hidden`) and `src/community/services.py:tallies_for_event` (voided
  ballots excluded).
- Flood controls: `src/community/services.py:_throttle_ip`
  (`IP_BALLOTS_PER_HOUR = 30`, 429 `throttled`), `_record_new_voter`
  (more than 10 new voters in 10 minutes raises one `AbuseFlag`
  `kind=ip_burst`), ballot changes capped at 10 per voter per hour in
  `submit_ballot`, comments capped at 5 per user per 10 minutes in
  `create_comment` (`_comment_rate_limit`).
- Ballot order is per-ballot random (`stored seed, replayable for audit`) and
  voter emails never appear in public output, tallies, or `votes.csv`.

Proof (all passing):

- `tests.test_community`
  `test_quadratic_budget_is_enforced_without_mutating_previous_ballot`.
- `tests.test_community`
  `test_results_hidden_from_voters_in_api_and_page_until_close` and
  `test_results_become_public_at_close_and_voided_ballots_are_excluded`.
- `tests.test_community`
  `test_ip_hash_limits_new_ballots_to_thirty_per_hour` and
  `test_burst_from_one_ip_hash_creates_an_abuse_flag`.
- `tests.test_community`
  `test_voter_change_throttle_and_single_vote_limit` and
  `test_comment_rate_limit_is_five_per_ten_minutes`.
- Probe cases "A quadratic ballot over its credit budget is refused"
  (REFUSED) and "Non-organizer cannot read live voting results" (REFUSED);
  `tests.test_probe`
  `test_every_current_attack_is_refused_and_fixture_data_rolls_back`.

What it does not stop (residual): rotating IPs evade the IP throttle and
shared NATs throttle legitimate voters — IP limits are explicitly weak (BUILD
SPEC section 16). Replay of a whole valid ballot is a duplicate and is
refused; replay across many distinct valid identities is Sybil voting
(section 3.1), **not stopped**.

### 3.3 Judge collusion

Attack: judges coordinate scores — quid-pro-quo inflation, bloc voting, or an
undeclared conflict — or an organizer quietly drops the reviews that decide
the winner.

What VERDICT does:

- Assignment blocks known conflicts: `src/judging/services.py:assign_batch`
  rejects pairs via `_assignment_ineligibility` (`conflict`,
  `track_mismatch`, `already_assigned`); conflicts are declared with
  `declare_conflict` / `add_conflict`, and declaring one removes only
  unreviewed assignments (a submitted review blocks removal, 409
  `review_exists`).
- One role per person per event (section 2) makes judge-participant and
  judge-organizer overlap structural, not procedural.
- Detection, not auto-exclusion: `src/results/engine.py:outliers`
  (`threshold=2.5`, minimum 3 reviews per project) flags reviews far from
  consensus ("Diego Herrera scored Glass Signal 31 points above consensus");
  `leave_one_out` / `robustness` / `evaluate` produce the robustness
  certificate (leave-one-judge-out, leave-one-review-out, fewest changed
  reviews that flip the winner).
- Exclusions are accountable: `src/judging/services.py:exclude_review`
  requires a submitted review and a 1–300 character reason, records
  `audit.services.record` (`judging.review_excluded`) in the same
  transaction, and `include_review` reverses it; consequence preview shows the
  exact ranks and awards that change before publishing, and a stale preview is
  refused (409 `stale_preview`).

Proof (all passing):

- `tests.test_assignment.test_conflict_blocks_assignment`.
- `tests.test_judging.test_conflict_removes_unreviewed_assignment` and
  `test_conflict_cannot_remove_assignment_with_submitted_review`.
- `tests.test_judging.test_manual_assignment_reports_ineligibility_and_creates_batch`.
- `tests.test_engine.test_injected_outlier_flagged` (flag raised).
- `tests.test_engine.test_dominant_winner_is_robust` and
  `test_flip_margin_counts` (robustness certificate).
- `tests.test_results.test_excluded_reviews_not_in_preview`.
- `tests.test_consequences`
  `test_excluding_and_reincluding_a_deciding_review_changes_then_restores_ranks`.
- Probe cases "Judge cannot select a peer with ?judge=", "Judge cannot read a
  peer score path", "Unassigned/other-track review" reads and writes
  (all REFUSED); `tests.test_probe`
  `test_every_current_attack_is_refused_and_fixture_data_rolls_back`.

What it does not stop (residual): coordinated *moderate* inflation inside the
2.5x spread is **not detected** by design; VERDICT never auto-excludes or
auto-disqualifies. The organizer stays the authority, and every intervention
is previewed, reasoned, and audited.

### 3.4 Deadline gaming

Attack: submit or edit after the deadline, save a review after judging closes,
exploit clock skew or a race at the closing second, or reopen results to
reshuffle prizes silently.

What VERDICT does:

- Time comes only from the server: `src/core/clock.py:now` (tests patch it;
  client timestamps are never trusted).
- Windows are half-open `[open, close)` and are checked after row locks:
  `src/projects/services.py:check_submission_window` runs after
  `Event.objects.select_for_update()` in `create_project` / `update_project`
  (`_lock_project`) / `submit_project`, so a save at the closing second is
  refused consistently (403 `window_closed`, checked before validation).
  Review writes go through `src/judging/services.py:_review_context` /
  `_pairwise_context` (`judging_closed`, 403), and publishing requires judging
  closed: `src/results/services.py:publish` (409 `judging_open` otherwise).
- Every submission edit while submitted creates a new `ProjectRevision` with a
  sha256 digest receipt; re-publishing needs a note and creates a new version
  that supersedes the previous one; consequence preview plus
  `src/results/services.py:check_expected_digest` (409 `stale_preview`)
  refuses to act on moved data.

Proof (all passing):

- `tests.test_deadlines`
  `test_every_participant_write_at_the_close_is_403_window_closed` (denied at
  exactly close) and `test_one_second_before_the_close_every_write_is_allowed`
  (allowed one second before) and
  `test_the_window_opens_exactly_at_the_open_bound`.
- `tests.test_community.test_voting_window_uses_a_half_open_interval`.
- `tests.test_judging.test_judging_closed_denies_review_writes` and
  `tests.test_pairwise.test_close_boundary_denies_next_save_and_undo`.
- `tests.test_results.test_publish_blocked_while_judging_open` and
  `test_publish_succeeds_when_judging_closed`.
- `tests.test_release_security`
  `test_future_judging_close_cannot_publish_through_api` and
  `test_stale_event_cannot_edit_after_database_window_closed`.
- Probe cases "Participant cannot create/edit a project, add an image, change
  answers, or join a team after submissions close" and "Closed judging rejects
  review submission" (all REFUSED).

What it does not stop (residual): an organizer with legitimate powers can
still change windows before the scoring lock and can disqualify or exclude —
but every such change is audited with old/new values and previewed. Clock skew
between app instances is deployment configuration (one `now()` source;
run NTP). Results reopening re-publishes a new version; old versions are kept,
not rewritten.

## 4. Other threats (STRIDE)

Stopped = passing test cited. Partially mitigated / not stopped = no
full-prevention test exists.

| Category | Threat | Mitigation (file and function) | Test (module.test, passing) | Residual |
|---|---|---|---|---|
| Spoofing | IDOR / cross-event access (judge reads another event's reviews, organizer exports them, track from another event) | `src/judging/services.py:_cross_event` and `_assignment_ineligibility`; `src/projects/services.py:resolve_track` / `save_answers` (400 `cross_event`); read scoping via `policy.visible_*` | `tests.test_judging.test_cross_event_track_assignment_and_conflict_are_rejected`; `tests.test_judging.test_other_event_project_is_rejected_for_assignment`; `tests.test_isolation.test_other_event_organizer_cannot_read_judging_data`; `tests.test_isolation.test_cross_event_reused_judge_public_id_does_not_expand_assignment_scope`; `tests.test_projects.test_a_track_from_another_event_is_400_cross_event` | Stopped for tested routes; new routes must use the same helpers (reviewer: grep for `objects.all()` in views). |
| Spoofing | CSRF (session POST without token) | `CsrfViewMiddleware` plus `src/accounts/api.py:require_csrf` (403 `csrf_failed`); Bearer tokens skip CSRF by design | `tests.test_accounts_api.test_login_without_csrf_is_403`; `tests.test_accounts_api.test_registration_without_csrf_is_403` | Stopped. Bearer tokens must stay secret (see token theft). |
| Tampering | XSS via comments/project text | Plain text only, rendered escaped (`linebreaksbr`); CSP in `src/core/middleware.py:SecurityHeadersMiddleware` (`script-src 'self'`, `frame-ancestors 'none'`, `X-Frame-Options DENY`) | `tests.test_core.test_csp_forbids_inline_script_and_frames` | Stopped for inline scripts/frames. Stored-XSS payload coverage for every text field is partially mitigated (escaping is framework-default; no per-field injection test for all fields). |
| Tampering | SSRF through webhook URLs | `src/interop/webhooks.py:validate_target` (http/https only, DNS-pinned connections, no redirects, rejects loopback/private/link-local) | `tests.test_t4.test_loopback_webhook_destination_is_refused` (denied); `tests.test_t4.test_pinned_http_delivery_reaches_local_receiver_and_verifies_signature` and `test_webhook_signs_exact_json_body_and_retries_with_backoff` (allowed path) | Stopped for loopback/private targets. DNS-rebinding between validation and delivery is not separately tested: partially mitigated (pinning is the control). |
| Spoofing | Token theft and reuse after revocation | `src/accounts/tokens.py:hash_token` / `issue_token` / `authenticate_token` / `revoke_token` (only sha256 hashes stored; plaintext shown once) | `tests.test_tokens.test_issue_returns_plaintext_that_authenticates` (allowed); `tests.test_tokens.test_revoked_token_stops_authenticating` (denied); `tests.test_tokens.test_only_the_hash_is_stored`; probe case "Revoked API credentials cannot authenticate" (REFUSED) | Stopped for revocation. A stolen live token works until revoked: partially mitigated (short-lived demo tokens, admin reset links expire in 24 h, single use). |
| Elevation | Privilege escalation between roles | One role per event (section 2); judges see only own assignments/reviews via `src/judging/policy.py`; judge B reading judge A scores is 403 | `tests.test_events.test_add_user_with_existing_other_role_is_409`; `tests.test_teams.test_a_judge_cannot_create_a_team`; probe cases "Judge cannot select a peer", "Judge/participant cannot export organizer CSV data" (REFUSED) | Stopped for tested transitions. |
| Info disclosure | Scraping / enumeration of private data (emails, drafts, private answers, media) | Emails never in public output; `src/audit/services.py:_target_reference` stores `public_id`/slug only; `hash_ip` stores truncated HMACs; media served by a Django view through `policy.visible_project` (404 for outsiders); drafts excluded everywhere public | `tests.test_projects.test_the_project_page_shows_the_write_up_and_no_emails`; `tests.test_exports.test_event_json_no_password_hashes`; `tests.test_exports.test_event_json_no_token_hashes`; probe cases "Participant cannot read another team's draft", "Private draft media is not served to another team" (REFUSED) | Stopped for tested surfaces. The public gallery is public to judges too (documented limitation). Integer PKs are never exposed; enumeration by `public_id` is random and unguessable but not rate-limited per object: partially mitigated. |
| Repudiation | Audit log tampering | `src/audit/services.py:record` / `canonical_entry` / `entry_digest` plus `verify_chain` / `export_chain`; `src/audit/models.py` append-only (model and queryset mutations refused) | `tests.test_audit_chain.test_append_links_heads_and_independent_serialization`; `test_raw_sql_content_edit_is_detected`; `test_raw_sql_tail_deletion_is_detected_against_head`; `test_model_and_queryset_mutations_are_refused` | Stopped against app-level edits. A DB superuser rewriting rows *and* the chain head is not stopped (section 6, item 7). |
| Tampering | Result tampering after publication | `src/results/services.py:_canonical_inputs` / `_digest` / `verify_publication` plus `src/results/management/commands/verify_publication.py`: recompute from stored inputs (rows identical?) and rehash live DB (digest matches?) | `tests.test_results.test_verify_identical_after_publish`; `test_verify_differs_after_score_tampered`; `test_digest_changes_when_review_score_changes`; `tests.test_publication_policy.test_verify_command_reports_both_verdicts` | Stopped for tampering with either stored rows or live scores. A DB superuser rewriting both rows and inputs to match is not stopped (section 6, item 7). |
| Spoofing | Certificate / signed-record forgery | `src/interop/certificates.py:verification_code` / `verify_code` / `code_matches` (HMAC, `compare_digest`); `src/interop/signing.py:sign_record` / `verify_record_signature` / `canonical_bytes` (Ed25519) | `tests.test_t4.test_signed_judge_record_is_public_verifiable_tamper_evident_and_revocable`; `tests.test_astra_final.test_public_certificate_verification_reports_superseding_award`; probe cases "A participant cannot view another team's certificate", "A tampered judge record fails signature verification" (REFUSED) | Stopped without the key. Key management is operational (section 6, item 8). |
| Availability | Denial of service / brute force | `src/accounts/throttles.py:LoginThrottle` (10 logins per 15 min per IP+email, generic error, no enumeration); anon 120/min, user 1200/min; ballot/comment throttles (section 3.2) | `tests.test_accounts_api.test_ten_failures_then_429`; `test_the_budget_is_per_email_so_one_address_cannot_lock_out_another`; `tests.test_community.test_ip_hash_limits_new_ballots_to_thirty_per_hour` | Partially mitigated: slows credential stuffing and casual floods; no protection against distributed floods (needs a reverse proxy / WAF, section 6, item 9). |
| Tampering | Supply chain (dependencies, CDN, build) | Pinned `requirements.txt`, vendored wheels (`scripts/vendor_wheels.py`), offline build, no CDN links, no web fonts, no inline `<script>` or `on*=` handlers, no runtime network calls | `tests.test_core.test_csp_forbids_inline_script_and_frames` (header control only). No test pins dependency hashes or scans templates for CDN links. | Partially mitigated: pinning + vendoring + CSP are implemented but have no dedicated regression test. Operational answer: run `pip install --require-hashes` from vendored wheels and keep the CSP test green. |

## 5. Attacks we did not stop

1. **Sybil identities with many real email addresses.** Why: the email gate
   verifies control of a mailbox, not humanity. Do: use authenticated or email
   access mode for binding tallies; monitor `AbuseFlag(kind=ip_burst)`.
2. **Coordinated moderate score inflation (within the 2.5x spread).** Why:
   statistically indistinguishable from honest agreement at 3 reviews per
   project. Do: spread assignments across judges, check leave-one-judge-out
   before publishing, require exclusion reasons.
3. **Undeclared conflicts (a judge who stays silent).** Why: recusal depends on
   honesty. Do: publish the judge roster per track, invite counter-declarations,
   treat outlier flags as investigation leads.
4. **Order effects on judges.** Why: serial-position effects are real and the
   model does not correct them. Each judge now sees a per-judge deterministic
   order (sha256 of event, judge-role and project public ids, to-do before
   submitted), so position effects spread across projects instead of piling
   onto the same ones; the model still does not correct them. (Community
   ballots already use per-ballot random order.) Do: treat any residual
   first/last skew as uncorrected signal, not as evidence about the projects.
5. **Scale (multiplicative) differences between judges.** Why: the offset model
   shifts but never stretches; a per-judge scale parameter would be fitted from
   noise at ~3 reviews per cell. Documented in `JUDGING.md` (Limitations). Do:
   brief judges with anchored rubrics.
6. **Open-link ballot-box stuffing by a determined operator.** Why: device
   cookies deter casual repeats only. Do: never bind prizes to open-link
   tallies alone.
7. **A database superuser rewriting data and hashes together.** Why: the app
   cannot observe writes that bypass it. Do: restrict DB access, ship
   `pg_dump` + `appdata` backups off-host, re-run `verify_publication` and the
   audit `verify_chain` from a copy the operator cannot rewrite.
8. **Secret-key / signing-key compromise.** Why: whoever holds the keys mints
   valid tokens, certificates, and webhook signatures. Do: set `SECRET_KEY`
   from the environment, back up `/data/secret_key` with the database, rotate
   on suspicion (sessions invalidate; API tokens and invites are independent
   of it).
9. **Volumetric DDoS and host/network attacks.** Why: in-app throttles only
   slow single-source abuse. Do: run behind a reverse proxy with rate limiting,
   TLS, and backups (`pg_dump` plus the `appdata` volume).
10. **Social attacks: phishing, password reuse, organizer account takeover.**
    Why: no code distinguishes the legitimate owner of credentials from an
    attacker holding them. Do: demo passwords never in production
    (`DEMO_MODE=0`), admin reset links are single-use and expire in 24 hours,
    revoke tokens on staff changes.

## 6. How to re-check these claims

Run on PostgreSQL with `DATABASE_URL` pointed at an isolated database (the
test runner creates and drops its own `test_<name>` database):

```
.venv\Scripts\python.exe manage.py test tests.test_community tests.test_judging tests.test_deadlines tests.test_results -v 1
.venv\Scripts\python.exe manage.py test tests.test_consequences tests.test_engine tests.test_isolation tests.test_assignment tests.test_pairwise tests.test_release_security -v 1
.venv\Scripts\python.exe manage.py test tests.test_accounts_api tests.test_tokens tests.test_core -v 1
.venv\Scripts\python.exe manage.py test tests.test_audit_chain tests.test_publication_policy tests.test_t4 tests.test_probe -v 1
.venv\Scripts\python.exe manage.py test tests.test_exports tests.test_projects tests.test_events tests.test_teams -v 1
.venv\Scripts\python.exe manage.py test tests.test_astra_final -v 1
```

Adversarial probes (rollback-only, against a migrated database):

```
.venv\Scripts\python.exe manage.py migrate --noinput
.venv\Scripts\python.exe manage.py integrity_probe
.venv\Scripts\python.exe manage.py integrity_probe --write   # writes attack-report.txt
python scripts/attack.py .dogfood.toml                       # live HTTP version against a running portal
```

`integrity_probe` runs 28 named attacks (`peer-scores-query`,
`peer-scores-path`, `unassigned-review-read/write`,
`other-track-review-read/write`, `judging-closed-write`,
`participant-create/update/image/answer/join-after-deadline`,
`other-team-draft-by-id`, `draft-media-by-url`,
`foreign-organizer-review-read/export`, `judge/participant-csv-export`,
`anonymous-api`, `session-post-without-csrf`, `revoked-token-reuse`,
`csv-formula-injection`, `voting-results-hidden`,
`second-ballot-same-identity`, `quadratic-ballot-over-budget`,
`certificate-other-user-refused`, `tampered-record-invalid`,
`webhook-loopback-refused`) through the real URL stack and rolls everything
back. Expected output ends with `summary 28/28 attacks refused`.
`tests.test_probe.test_every_current_attack_is_refused_and_fixture_data_rolls_back`
asserts the same in CI.
