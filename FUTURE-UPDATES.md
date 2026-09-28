# VERDICT: master roadmap through submission and adoption

Planning date: 28 September 2026 (IST). Owner: lead orchestrator. This is a delivery plan, not a list of shipped capabilities. The latest recorded execution baseline is 27 September, approximately 18:53 UTC; no application tests were rerun to write this document.

## 1. Our strongest feature: Judging Readiness Planner + Event Runbook

**The product promise: help an organizer prepare a feasible judging plan, run the whole event, and recover from problems without a spreadsheet or a developer.**

Put this first in our product story. Connect the existing assignment engine, review-budget research, Command Center, submission receipts, judge workspace, outbox and publication controls into one understandable workflow. This is not another dashboard, an AI judge, or a Closure-first product.

The technical differentiator is **planning the quality and coverage of judging before scores arrive**, with honest explanations of what the available reviews cannot establish. The product differentiator is taking that plan all the way through a comfortable participant experience, focused judging, safe publication and operational recovery.

### What the flagship actually does

| Stage | Organizer sees and can do | What we must not imply |
|---|---|---|
| Plan | Eligible capacity by track, assignment shortfalls, workload, overlap/connectivity and concrete reasons a target was not filled | A heuristic's failure to find a plan is not a mathematical proof that no plan exists |
| Understand | A separately labelled experimental review-budget scenario, with explicit assumptions and resource cost | Simulated detection shares are not probabilities that a real judge is biased, or guarantees of a fair winner |
| Operate | A short phase-specific list of actions: invite, resolve eligibility, assign, contact inactive judges, review coverage, close and publish | No automatic exclusions, deadline extensions, reassignments or publication |
| Resolve | Preview affected people/projects, take an authorized action, recheck state under lock and retain an audit entry | A preview is not authorization; it can become stale |
| Finish | Publish coherent awards, release only authorized feedback, issue records and prepare export/backup | An event export is not a full operational backup; signing bytes does not establish their truth |

**Current foundation, not a finished flagship:** pure `estimability()` and `review_budget_curve()` exist in [the engine](src/results/engine.py), with [engine tests](tests/test_engine.py). Assignment, Command Center, Decision Room and outbox implementations exist. A complete, reviewed planner API/UI and joined-up runbook are not established by the present evidence.

Important mathematical boundary: the existing budget simulator uses fixed lambda, a balanced track-agnostic design, independent Gaussian noise and an injected positive offset. It does not simulate the full adaptive production policy or this event's exact constrained assignment plan. Do not place its percentages beside a real plan as if they describe that plan. The first safe UI is an explicitly separate experiment; a plan-aware estimator is a later research upgrade with its own validation.

### The one thing I would bet our remaining effort on

**A stranger completes an offline event rehearsal, including a deliberate failure and recovery, without our intervention.**

Not just a successful demo by its author. Someone unfamiliar with VERDICT follows the README, joins as a participant, submits, judges, handles an organizer incident, publishes, and checks a restored copy. Record where they hesitate or need help; fix those points before adding another feature.

This is our highest-leverage bet because it tests the whole product at once: workflow, correctness, judging integrity, interface, documentation and operability. It is a strategic judgment, not a promise of first place. A fancy new algorithm cannot compensate for a judge losing a ballot or an organizer being unable to run the portal.

Our defensible claim is the quality of this integrated experience and its evidence. **We have not established that it is unique in the competition.** Do not advertise "world first", "nobody else has this", "bias eliminated" or a predicted winning probability.

## 2. Source of truth and competition contract

Use this file for priorities, backlog and release decisions. Use [BUILD-SPEC](docs/BUILD-SPEC.md) for implementation contracts. If a new decision conflicts with that spec, record the decision and align spec, code, tests and UI together; this roadmap alone does not change runtime policy. It supersedes older priority lists, not historical evidence.

The official homepage currently confirms **29 September 2026, 18:00 UTC / 23:30 IST** as freeze. The base weights are correctness 40%, integrity 25%, operability 20%, quality/innovation 15%. These priorities favor a complete, reliable product. [Official timeline and criteria](https://dogfoodhack.com/)

The organizer runner has seven checks; passing them is not exhaustive T1/T2 certification. Saved organizer clarification says T3/T4 receive manual review. Bonus challenges break ties and inform Best Judging Engine, rather than adding to the weighted base score. Required handoff includes working seeded Compose, honest tier claims, acceptance output, required documents, tests, license and a five-minute demo. [Official specification](https://dogfoodhack.com/spec/), [local organizer audit](docs/ORGANIZER-DOWNLOAD-AUDIT.md)

Do not optimize for a stale prize amount: saved announcement and homepage figures have conflicted. Confirm final amounts, submission link and any award-stacking rules with organizers before making financial claims. No private Discord access is assumed here.

### Non-negotiables

- PostgreSQL everywhere: runtime, local development, CI and tests. No SQLite fallback or new SQLite use.
- Preserve official `run.py` and `fixtures.json`; never move fixture deadlines to make a demo easier.
- Preserve the existing working tree, databases and volumes. Test in isolated environments with explicit names.
- Never access the pre-kickoff archive or import code from it. Do not copy a competing platform's implementation.
- Backend policy/service enforcement, scoped queries, one API write path, server time, half-open windows, transactional audit and consistent lock order remain mandatory.
- No required cloud, hosted authentication, remote font/CDN, external API or network dependency at runtime.
- No secrets, private notes, credentials or signing keys in screenshots, public exports or release artifacts.
- Keep the repository private until the user explicitly authorizes publication. Do not push, publish a write-up, submit a form or change visibility as an incidental roadmap task.

## 3. Where we actually stand

Statuses: **VERIFIED** = specified checks passed on a named build; **PARTIAL** = implementation exists but the whole experience is not established; **OPEN** = unfinished obligation; **PLANNED** = proposed work; **DEFERRED** = intentionally outside the release path. None means "perfect".

| Area | Recorded status | Evidence / remaining boundary |
|---|---|---|
| PostgreSQL regression | VERIFIED for recorded source | 987 Django tests + 57 pure engine tests passed; overlapping categories are not a unique-test total. Host virtualenv, not the complete container suite |
| Evaluated image | VERIFIED for named checks | Official 7/7; extension 25/25; attacks 28/28 plus 14 direct HTTP checks |
| Invite/team races | VERIFIED for tested interleavings | Five reproduced failures repaired: revoked invite, use-count race, simultaneous team join, remove/rotate at close |
| Private judge notes | VERIFIED for tested privacy paths | Legacy `Review.comment` excluded from released team feedback; API retains `comments: []` |
| Publication verifier | PARTIAL, hardened | Stored-input digest, missing/duplicate rows and malformed inputs covered; historical roster independence and every displayed field are not covered |
| T1/T2 browser experience | PARTIAL | Strong backend evidence; complete multi-role visual/keyboard journey still needs sign-off |
| T3 | PARTIAL | Voting/comments/abuse controls exist; full browser/manual tier sign-off pending |
| T4 / API First | PARTIAL / OPEN | Last strict schema run: 306 errors, 62 unique, 5 warnings; full action/webhook map and adoption proof unfinished |
| Required documentation | OPEN | Core documents need completion and reconciliation with current code |
| Offline/recovery evidence | PARTIAL | Earlier isolated-network checks exist; latest host preview is internet-capable. Final distributable runtime/recovery test pending |
| Demo and manual evidence | OPEN | No complete five-minute recording or full browser sign-off established |

Evidence: [integration and final gate](docs/ADVERSARIAL-INTEGRATION-20260927.md), [T1 races](docs/ADVERSARIAL-T1-RACES.md), [T2 privacy](docs/ADVERSARIAL-T2-PRIVACY.md), [tier evaluation](docs/TIER-EVALUATION-20260927.md). The tested image was `sha256:03c26d6df597ca58322944fd8e6667ca2e939350f0cc482461afbb8f6a180567`. Any application changes require renewed evidence; do not carry these results forward automatically.

**Honest competitive position:** credible contender, not demonstrably first. Existing public research found serious competing whole-product, API, operator-documentation and presentation claims. Those projects were not executed under equal conditions. Our integrity work is a strength; our unfinished handoff and user journeys can still lose the overall contest. Use [whole-product research](docs/HACKATHON-WINNER-RESEARCH.md) for rationale, not as evidence of shipped features.

## 4. Ordered work queue: fixes before expansion

P0 blocks a release-ready claim. P1 completes the whole product and our strongest differentiation. P2 is useful only after its prerequisites pass. Priority is not permission to start every row in parallel.

| ID | Priority / current status | Work item | Completion condition |
|---|---|---|---|
| R01 | P0 / OPEN | Inventory current source, migration and evidence state | Scoped changes reviewed; no official-file drift; tests tied to the source/build actually submitted |
| R02 | P0 / OPEN | Repair strict OpenAPI and UI/API parity | Generation validates without errors/warnings; every actual UI read/write action mapped and exercised, including dynamic JS actions |
| R03 | P0 / OPEN | Resolve publication/correction and feedback contracts | One written policy aligned across guards, UI, exports, verifier and tests; private legacy notes remain private |
| R04 | P0 / OPEN | Complete required docs and reviewer map | A stranger starts and operates the released stack without planning-directory files or author assistance |
| R05 | P0 / OPEN | Finish three-role browser journey and failure recovery | Participant, judge and organizer happy/error paths recorded and reviewed; no data loss or privacy leak |
| R06 | P0 / OPEN | Validate final Compose, PostgreSQL migrations, restart and offline runtime | Distributable configuration passes on isolated fresh volumes, then repeated boot; preparation requirements explicit |
| R07 | P0 / OPEN | Prove recovery and secret/key handling | Restore into a separate environment; compare semantic data/media and verify historical artifacts |
| F01 | P1 / PARTIAL | Participant Submission Studio | Server readiness, preview, stale-edit recovery, receipt and deadline states agree with API |
| F02 | P1 / PARTIAL | Resilient Judge Workspace | Clear save/submit distinction, visible failure, safe retry/resume, conflict reporting, blind queue |
| F03 | P1 / PARTIAL | Phase-oriented Event Runbook | Existing pages joined by actionable next steps; safe bulk previews; every mutation revalidated |
| F04 | P1 / PARTIAL | Judging Readiness Planner | Real capacity/coverage and experimental simulations clearly separated; bounded API/UI with tests |
| F05 | P1 / PARTIAL | Gallery-to-awards public journey and T3 completion | Voting modes, moderation, hidden results, release states, team feedback and records work coherently |
| F06 | P1 / PARTIAL | T4 adoption surface | Verified webhook coverage, signed records, embed, bulk paths and API docs; gaps explicitly disclosed |
| F07 | P1 / OPEN | Judging explanation and current proof | JUDGING completed; fixture outputs regenerated from current engine; synthetic limits and negative results retained |
| F08 | P1 / OPEN | Security, performance and manual tier evidence | Test catalogue below dispositioned; reproducible reports; no unsupported T3/T4 or bonus claims |
| F09 | P2 / PLANNED | Configuration-only practice-event creation | Synthetic identities, isolated event, normal APIs/policies; cannot affect fixture/live events |
| F10 | P2 / PLANNED | Explicitly shareable written team feedback | Separate field/notice, release rules, moderation and privacy tests; no backfill from private notes |
| F11 | P2 / PLANNED | Submission-integrity triage and COI suggestions | Explainable signals, organizer review and recorded reasons; no automatic plagiarism/cheating verdict |
| F12 | P2 / PLANNED | Dry-run CSV roster/project onboarding | Row errors, duplicate/identity policy, all-or-nothing or explicit partial semantics, safe retries |
| F13 | P2 / PARTIAL | Pairwise experience and bonus proof | Existing live mode exercised end to end; sparse/disconnected/retraction behavior documented |
| F14 | P2 / PLANNED | Revision comparison and richer archive navigation | Authorized readable diffs/history without exposing deleted/private content |
| X01 | DEFERRED | Closure certifier and replay-first UI | Optional research only; cannot displace the full product or be sold as proven stability |
| X02 | DEFERRED | Appeals, extra estimators, in-person maps, advanced automation | Only after release and a real operator requirement; no last-minute competitor feature copying |

First work wave: R02, R04 and R05 can proceed in disjoint ownership lanes while the orchestrator resolves R03. Any newly reproduced data-loss/privacy bug jumps ahead of polish. F09 does not block the central rehearsal: a manually created synthetic event using normal product screens is sufficient initially.

## 5. Feature contracts: what to finish, add and refuse

### F01 — Submission Studio: certainty for participants

Reuse the existing editor, checklist, revisions, stale-edit token and receipt. Add only missing pieces:

- A server-authoritative readiness response using the same submit validation; links to the exact fields requiring attention.
- An own-project presentation preview with accurate public/private field boundaries, not role impersonation.
- Distinct states for unsaved edits, draft saved, submitted revision, stale version, network failure and closed window.
- A downloadable receipt identifying event, project, revision, server timestamp and digest; do not call it proof of external authorship.
- Clear team invite/ownership states, attachment errors, custom-question rules and accessible narrow-screen forms.

Done: two teammates can resolve concurrent edits, submit and verify the recorded revision. Refreshing, retrying or crossing the deadline never silently changes the submitted revision. Required fields fail for the same reason through UI and API.

### F02 — Judge Workspace: no lost work, no leaked judgment

Keep project evidence and rubric together. Finish keyboard navigation, criterion descriptions, save status, submit confirmation and next-review flow. A failed save must visibly remain unsaved; a successful toast requires server acknowledgement.

Define edit-version semantics before adding recovery. A retry cannot overwrite a newer server draft or silently submit after close. If local recovery is added, scope it to user/event/review/revision, expire it, clear it on logout/account switch, and require explicit reconciliation. Prefer tested server recovery over an unreviewed browser-storage feature.

Conflict, unavailable demo and inability-to-review reports must have distinct meanings. None is a zero score. Reassigning goes through ordinary eligibility and track checks. Judges see their own workload, never standings, peer ballots or task hints chosen from who might win.

Done: keyboard-only review, interrupted save, resume, conflict and reassignment all work; unauthorized direct reads/writes fail without leaking private content.

### F03/F04 — The flagship: one runbook, honest planning

Extend existing setup, assignments and Command Center pages rather than duplicating them. Show the next action, why it matters, affected scope and the exact policy preventing it.

Capacity view must distinguish total slots needed, eligible capacity, assignments, submitted reviews and unavailable reviewers. Total free slots alone does not imply a feasible track/COI assignment. Report "unfilled by this planner" unless a proved constraint establishes infeasibility. Never invent pending judge identities from the fixture's missing score rows.

Experimental simulation is organizer-only, opt-in and bounded. Display assumed noise, bias magnitude, fixed lambda, seed, repetitions, design and limitations. Show "not reached on tested grid" where appropriate. Do not silently use fitted residual spread as known generating noise or convert the scenario into a judge accusation. Keep computation off routine page refreshes; record method/version and invalidate stale cached inputs.

Bulk invitation/reminder/rebalance previews show recipients, scopes and effects without sending anything. Execution rechecks permissions, deadlines and object versions under lock. Manual approval remains essential. Reminder order follows a declared coverage/load/age policy, not standings. Calibration/anchor controls must not imply improvement: prior fixture experiments did not establish that anchors helped at the tested budget.

Done: an organizer identifies a constrained track, understands an unfilled assignment, repairs the roster or chooses a documented target, executes safely, follows progress, and publishes. No spreadsheet required. The simulator may remain experimental without weakening this real operational path.

### F05/F10 — Public experience and feedback that respect boundaries

Connect event rules, gallery/search/filter, project pages, ballot, comments, published awards, authorized team feedback and records. Finish light-theme consistency, readable empty/error/closed states, mobile navigation and accessible forms. Public rules must agree with configured rubric and dates.

Exercise all supported voting modes. Explain identity guarantees honestly: cookies, email gates and accounts do not establish one-human-one-vote. Quadratic credits need a precise cost/influence formula consistent in UI, backend and exports; do not rely on ambiguous marketing wording. Moderation requires reasons and audit, not hidden retroactive changes.

Legacy judge notes stay private permanently under their collection contract. A new shareable-feedback field, if implemented, must be explicitly labelled at entry, separately stored, tested and released by policy. De-identification is not anonymity. Score summaries may ship without written feedback; document that limitation honestly.

Done: no hidden tallies or private notes leak through HTML, JSON, exports, widgets, caches or errors. Each team sees only its released feedback. Published and superseded awards have understandable states.

### R03 — Publication and correction decision to resolve before release

Proposed release contract: publication versions remain immutable; subsequent authorized corrections affect live inputs and require a reason and a new publication version to change public results. Display which version is official. Never silently rewrite an award or reinterpret an earlier certificate.

This is a proposed alignment decision, not a claim that every guard currently implements it. Resolve whether post-close score edits are permitted; the safer default is no hidden reopening and no rewriting submitted ballots through an organizer shortcut. Corrections/exclusions must follow an explicit authorized policy.

Existing verification uses the live project roster and checks a limited row projection. Either document that exact contract everywhere for this release, or implement a separately versioned full snapshot and migration with tests. Do not relabel it an independent historical verifier. Review title/team/track, count and status-reason integrity if those fields are included in the claimed contract. Hashes alone do not detect a privileged actor rewriting both data and digest.

### F06/R02/R07 — Adoption, integration and recovery

- OpenAPI: typed requests/responses/errors, stable operation IDs, authentication, pagination, multipart uploads, examples and offline docs. Map buttons, forms, dynamic JS calls, reads, downloads and bulk actions; a regex over `data-api-url` alone is insufficient.
- Webhooks: maintain an action-to-event catalogue and explicit policy for read-only/sensitive actions. Test real emitters, scoped payloads, transactional outbox, signatures, retries, leasing and duplicate delivery. Do not silently claim every UI action is covered if only mutations are emitted; document/clarify the tier boundary. Never emit private score/note payloads to unauthorized subscribers.
- Records: distinguish certificate download authorization, HMAC-derived links and Ed25519 public verification. Test tampering, revocation, persistence and key lifecycle; explain trust boundaries.
- Embed: verify in a real iframe, including CSP/frame policy, navigation, responsive layout and the same public-data rules as the gallery.
- Portable export: specify included tables/media, schema version, identity remapping and provenance. Exclude credentials/private signing keys. Preserve the identity of historical signed bytes; do not regenerate and call them the original record.
- Operational backup: PostgreSQL plus media, protected `SECRET_KEY` and persisted signing material. Separate restricted backup storage from public portability files. A lost `SECRET_KEY` can affect HMAC certificate links as well as sessions.
- Import: bounded files, type/schema validation, referential consistency, path-traversal/oversize refusal, duplicate-ID rules and transactional failure behavior. Compare contents and meaning, not only counts.
- Recovery: restore into a distinct environment; verify data, media hashes, roles, publications and historical signatures before declaring success. Record elapsed time and actual losses rather than claiming zero-loss disaster recovery without evidence.

### F07/F11/F13 — Judging engine and integrity advantage

Finish the explanation of weighted scoring, assignment constraints, duplicate policy, missing reviews, constant scorers, shrinkage, adaptive selection, ties, exclusions and prize allocation. Reproduce raw/normalized/rank-change outputs from current source. Keep fixture analysis separate from known-truth synthetic experiments.

Explain where normalization does not help. Lower score spread is not proof of accuracy. Report sparse/disconnected designs, small-sample uncertainty and negative results. Never tune to reproduce an illustrative website chart. Distinguish live pairwise ballots from comparisons derived from rubric scores; they are not independent evidence.

Submission similarity and COI suggestions, if added, use already supplied local data. No network scraping dependency, automatic disqualification or unsubstantiated collusion label. An organizer sees the reason, examines the source and records a decision. Preserve legitimate duplicate/resubmission semantics.

Pairwise mode must support its real workflow, not just an estimator: assigned access, skips/abstentions, repeated/retracted comparisons, progress, exports and publication. Document regularization and graph connectivity. No ranking guarantee from too little evidence.

## 6. Test programme: attempts to break the product

This is the required risk catalogue, not a statement that every test exists or has passed. Map each ID to exact tests and evidence before marking it complete. Reuse existing tests; add missing assertions, HTTP integration and browser coverage rather than inflating counts. Proposed files must be labelled as new in worker packets.

### T1: identity, teams, submissions and deadlines

| ID | Attack / scenario | Required oracle |
|---|---|---|
| A01 | Session/token revoke, inactive user, role removed during a request, account switch | Subsequent access obeys current policy; no stale credential or browser state exposes another account; allowed control works |
| A02 | Same user joins two teams; last team slot contested; one-use invite used twice | One valid result, no over-capacity or duplicate membership, controlled losing response and consistent audit/counter |
| A03 | Rotate/revoke invite while acceptance waits on locks | Fresh token state checked after locking; revoked invite cannot succeed |
| A04 | Create/edit/submit/upload/answer/withdraw/join/leave/remove/rotate at exact close | Every covered mutation respects `[open, close)` with a server clock recheck after blocking |
| A05 | Two tabs edit same project; repeat submit after lost HTTP response | Stale changes rejected or explicitly reconciled; no silent overwrite/duplicate revision or receipt |
| A06 | Malformed JSON, wrong types, oversized bodies, missing questions, cross-event track/answer/image | Documented 4xx/envelope where applicable, not 500; closed-window guard ordering tested; no partial write |
| A07 | Draft/private media URL guessing, search/count leak, hostile text/URLs/images | Same visibility policy everywhere; escaped content, safe URL schemes, bounded verified media and no private bytes |
| A08 | Repeated bootstrap and demo-to-production switch | No duplicate/reset event data; fixture dates preserved; production does not expose/reactivate demo access |

Existing anchors: [teams](tests/test_teams.py), [adversarial T1](tests/test_adversarial_t1.py), [projects](tests/test_projects.py), [deadlines](tests/test_deadlines.py), [media](tests/test_media.py), [accounts](tests/test_accounts_api.py), [tokens](tests/test_tokens.py), [bootstrap](tests/test_bootstrap.py). Service-level race tests do not establish concurrent HTTP response behavior: add that layer where material.

### T2: isolation, assignment, mathematics and publication

| ID | Attack / scenario | Required oracle |
|---|---|---|
| B01 | Visitor/participant/peer judge/unassigned judge/other-track judge/other-event organizer against every sensitive read/write/export | Denied content never appears; own judge and own-event organizer controls still succeed; inspect response bodies, not just status |
| B02 | Assignment/track/COI or role changes while review save, submit or rebalance waits | Current authorization rechecked under consistent locks; no stale assignment write, deadlock or cross-event edge |
| B03 | Rubric change races first submitted review; judge submits twice | One locked scoring policy, one valid review state, coherent criterion set and audit |
| B04 | Greedy assignment trapped by scarce eligible judges, capacity shortage, disconnected tracks | No illegal assignments; clear unfilled reasons; small exhaustive oracle distinguishes heuristic failure from true infeasibility |
| B05 | Constant scorer, one judge, one review, zero reviews, zero/invalid weights, missing criteria, NaN/infinity, giant values | Finite correct outputs or explicit validation/unranked state; missing score never becomes zero |
| B06 | Ties at rounding boundaries, input reorder, CV fallback, duplicate supersession, exclusions, prize constraints | Independent tiny arithmetic/oracle expectations, deterministic semantics and no arbitrary title-order winner |
| B07 | Synthetic known-truth biased/unbiased panels, sparse/disconnected graphs, budget scenarios | Report accuracy/error and negative results; reproducible seeds and assumptions; no false statistical guarantee |
| B08 | Stored publication inputs/rows/awards corrupted; digest retained or rewritten; project roster changes | Genuine recomputation and stated scope; no missing/duplicate row accepted, no crash, no unsupported historical-integrity claim |
| B09 | Publish/correct/release races; private-note marker in feedback and all exports | No partial publication or cross-version mismatch; immutable-version policy upheld; private marker absent from unauthorized outputs |
| B10 | Pairwise skips, duplicate/retracted votes, self-pair, wrong event, disconnected/undefeated items | Correct identity/eligibility, explicit insufficient-data states, finite estimator and documented tie/retraction semantics |

Existing anchors: [isolation](tests/test_isolation.py), [judging](tests/test_judging.py), [assignment](tests/test_assignment.py), [engine](tests/test_engine.py), [robustness](tests/test_robustness_contract.py), [results](tests/test_results.py), [prizes](tests/test_prizes.py), [pairwise](tests/test_pairwise.py), [adversarial T2](tests/test_adversarial_t2.py).

### T3/T4: public surfaces and integrations

| ID | Attack / scenario | Required oracle |
|---|---|---|
| C01 | All voting access modes; duplicate concurrent ballots; exceeded/negative/non-integer credit budgets; exact close | Intended identity/window/budget rules enforced atomically; no double spend; valid ballot succeeds |
| C02 | Hidden results through page/API/CSV/widget/cache/error; shuffled ballot repeat visits | No aggregate leakage; stable-per-identity behavior if promised and reproducible ordering-distribution check, not flaky randomness assertion |
| C03 | Shared NAT, rotating identities, email variants; abusive comments and moderation | Measure false positives and residual Sybil risks; escaped comments, scoped reasons/audit; no "Sybil-proof" claim |
| C04 | Enumerate UI actions against schema and backend; malformed API payloads | No undocumented action/incorrect response contract; strict schema passes; session CSRF and bearer behavior both tested |
| C05 | Webhook timeout, receiver 500, worker crash before/after acknowledgement, duplicate delivery | Durable retry/lease recovery, receiver-visible delivery ID and idempotency guidance; no exactly-once guarantee |
| C06 | Forged webhook signatures, redirects, DNS rebinding, private/link-local/metadata destinations, payload leakage | SSRF/signature/privacy controls hold; local test receiver uses controlled test setup, not weakened production guards |
| C07 | Record tampering/revocation/wrong key, unauthorized certificate, iframe embedding | Actual public verifier rejects invalid records; historical key semantics and private access hold; browser embed works |
| C08 | Export/import with corrupt media, repeated IDs, traversal, wrong version or interrupted transaction | Clear refusal/rollback, preserved authorized contents and provenance; no secrets exported; semantic round trip |

Existing anchors: [community](tests/test_community.py), [T4](tests/test_t4.py), [webhook delivery](tests/test_webhook_delivery.py), [exports](tests/test_exports.py), [import](tests/test_import.py), [outbox](tests/test_outbox.py), [audit](tests/test_audit_chain.py), [release security](tests/test_release_security.py). These anchors are starting points, not blanket coverage claims.

### Browser, operational and performance gates

| ID | Test | Pass condition |
|---|---|---|
| D01 | Three-role full lifecycle in separate browser contexts | Real forms/APIs, no impersonation shortcuts or manual DB repair; saved artifact per role |
| D02 | Interrupted judge save, stale teammate edit, session expiry, refresh/back/navigation | Visible recovery with no unauthorized replay, silently lost confirmed data or stale overwrite |
| D03 | Keyboard-only, focus/errors, 360px layout, 200% zoom, contrast and labels | Core actions usable; failures documented. Automated checks alone are not accessibility certification |
| D04 | Fresh final Compose, repeated boot, web/worker/DB restart in disposable stack | Migrations/seed work; committed data survives; no duplicate bootstrap or insecure fallback |
| D05 | Prepared offline runtime with blocked egress | UI assets, APIs, all roles and public verification work locally; attempted external dependencies inventoried |
| D06 | Backup then restore to separate empty environment | Semantic rows, media bytes, access rules and historical signed records checked; original untouched |
| D07 | Mixed concurrent browse/save/vote/export plus publication load | Measure errors, p50/p95/p99, throughput, RAM/CPU and lock waits; no lost writes; bounded behavior under overload |
| D08 | Growing gallery/judge queue/progress/export datasets | Query-count regression checks and measured runtime; no evidence of N+1 growth on tested paths |
| D09 | Planner runtime limits and stale results | Bounded work/timeout with honest incomplete state; no misleading success, mutation or cross-role leak |
| D10 | Fresh reviewer follows README, schema examples and demo route | No planning-directory dependency, undocumented setup, author intervention or broken link |

Suggested D07 scenarios, not existing benchmark results: official fixture; synthetic 300-project event; stretch 1,000-project event if laptop resources allow. Start at 10 concurrent clients, then 50. Keep datasets separate and report actual hardware, warm/cold conditions, request mix, duration and expected 4xx separately from failures. Agree acceptable budgets before optimizing. Preserve results and privacy when caching expensive engine work; include event/input/policy identity in cache keys. A three-request latency sample is not a p95 benchmark.

### Test execution discipline

1. A worker receives exact target files, the suspected failure, invariant and positive control. Reproduce red before fixing an alleged defect; if it does not reproduce, report that honestly.
2. Race tests use PostgreSQL, separate connections and deterministic barriers/lock evidence with bounded timeouts. Sleeping alone is not proof of an interleaving.
3. Use independent oracles for small assignment/math cases. Do not compute expected results with the same function under test. Seed randomized/property tests and retain failing seeds.
4. Run targeted checks, then the full source gate, then the rebuilt-image checks. Exercise the full suite under the container Python/PostgreSQL environment before final certification, not only host Python.
5. Keep official checker unchanged. Since a checker can exit zero with FAIL lines, the surrounding release gate must verify actual totals and absence of failures. Never fabricate acceptance output.
6. Save exact command, timestamp, environment, source/commit or dirty-tree identity, image ID, counts and failures. Keep red/green evidence and gaps; do not sum overlapping suites into inflated totals.
7. No destructive/failure-injection tests against the original application stack. Validate isolation and recovery first; no broad `down -v`, reset or prune command.

## 7. Documentation and evidence we must ship

| Deliverable | Required substance / completion test |
|---|---|
| `README.md` | One-command start, offline preparation distinction, demo roles, reviewer map, honest tier/status table, screenshots, demo link, known limits and upgrade/recovery entry points |
| `ARCHITECTURE.md` | Modular monolith rationale, request path, policy/service ownership, PostgreSQL locking, outbox, audit, engine boundary, security and deployment tradeoffs |
| `DATA-MODEL.md` | Real schema/constraints and migrations, event isolation, identity/provenance, fixture mapping, export inventory, restore versus portability |
| `JUDGING.md` | Assignment, scoring equation, normalization/selection/fallbacks, constant and missing scores, ties, exclusions, pairwise, prize and correction policies with limitations |
| `docs/NORMALIZATION-PROOF.md` | Current reproducible fixture outputs and labelled synthetic experiments; versions/seeds and negative findings |
| `docs/THREAT-MODEL.md` | Submission/deadline abuse, IDOR/media, voting/Sybil, collusion, CSRF/XSS, credentials, SSRF, DoS, imports, backup/keys; implemented controls and remaining risks |
| `docs/API.md` + generated OpenAPI | Full UI/action inventory, auth/error examples, tested curl lifecycle, webhook catalogue; strict validation evidence |
| `docs/TIER-EVIDENCE.md` | Every tier bullet to page, endpoint, exact tests, manual recipe and current status; official versus self-authored evidence separated |
| `docs/SCHEMA-DEFENSE.md` | Human-readable answers to hard design questions: roles, lock order, private notes, immutable versions, keys, imports and math |
| Operations/recovery guide | Exact release commands, prepared offline workflow, migration/rollback limitations, health/logging, backups and demonstrated restoration |
| Reports | Fresh official acceptance stdout, own tier/attack outputs, browser/manual log, performance conditions and restore results tied to the candidate |
| Repository handoff | `.dogfood.toml`, Compose, tests, approved open-source license, dependency/font notices and clean secret/provenance review |

Files listed above are obligations, not claims that each currently exists or is complete. Link only real evidence in the release README. Planning files outside the repository must not be required by an adopter.

Reconcile known stale statements: old SQLite commands; fixed-lambda defaults superseded by adaptive policy; anchor improvements not demonstrated; "secret loss affects sessions only"; claims of complete T3/T4 from backend tests; broader historical-verifier claims than the code supports; optional outbound webhooks/email versus no required runtime network. Do not copy old proof numbers without regeneration.

## 8. The five-minute story judges should see

| Time | Demonstration | What it establishes |
|---|---|---|
| 0:00–0:30 | Prepared offline startup, seeded data, roles and concise product promise | They can run the submission; fixture and practice event clearly distinguished |
| 0:30–1:25 | Team joins, fixes readiness issue, submits and gets a receipt; stale/closed edit refused | Participant certainty and real deadline enforcement |
| 1:25–2:20 | Judge reviews, sees save confirmation, recovers a deliberately interrupted save; peer request refused | Product comfort backed by isolation |
| 2:20–3:20 | Organizer sees constrained coverage, previews a safe action and follows the runbook | Our main differentiated workflow; no invented missing assignments |
| 3:20–4:10 | Explain raw/normalized results briefly, close and publish, show public awards and private-note boundary | Defensible engine integrated into an understandable product |
| 4:10–5:00 | Verify a judge record, show completed isolated restore evidence and point to tier/API/docs | Adoption and maintainability, not just a polished screen |

This is a target script, not an already recorded demonstration. Use real product actions. Clearly label any time cuts or pre-recorded recovery segment; do not imply a restore completes instantly. Keep deep maths and attack logs in linked evidence so the video remains a complete lifecycle.

The memorable sentence should be: **"You can run the event, know what still needs attention, and recover when something goes wrong."** Follow it with evidence, not victory claims.

## 9. Release gates and stopping rules

### G0 — Correctness baseline

- [ ] Current source, migrations, targeted regressions and full PostgreSQL gate pass.
- [ ] No unresolved reproduced privacy, authorization, deadline or confirmed-data-loss defect.
- [ ] R03 policy conflicts resolved or precise limitations documented without contradictory UI promises.

### G1 — Complete experience

- [ ] Participant, judge, organizer and visitor journeys pass D01–D03.
- [ ] Three-role failure/recovery rehearsal succeeds without author intervention.
- [ ] Flagship real-capacity/runbook slice works; experimental planner clearly bounded or omitted from shipped claims.
- [ ] T3/T4 manual matrix reviewed. Partial functionality remains labelled partial.

### G2 — Adoptable candidate

- [ ] Strict schema/API inventory, required documents and license notices complete.
- [ ] Final distributable Compose, repeat boot, migrations and prepared offline checks pass.
- [ ] Backup/restore demonstrated; portability boundary and keys documented.
- [ ] Current proof, tests, security and measured performance evidence attached.

### G3 — Submission

- [ ] Rebuild exact candidate, rerun checkers, save fresh reports and inspect printed totals.
- [ ] Freeze honest tier/bonus claims; no inflated T4/API-first claim while gaps remain.
- [ ] Five-minute video and reviewer map accessible; no secret/private-data exposure.
- [ ] Review all uncommitted/untracked deliverables; distinguish generated evidence from local caches/data.
- [ ] User authorizes public repository/push and submission; verify submission receipt and final links before the official deadline.

Internal schedule: aim for a submittable candidate by 28 September 18:00 UTC; stop optional feature expansion at least 12 hours before official freeze; spend the last 6 hours on candidate testing, artifacts, video and handoff. These are team safety targets, not organizer rules. If a checkpoint is already past when this document is read, reduce optional scope immediately rather than restarting an impossible schedule.

After feature freeze, only scoped critical fixes with fresh evidence. Do not change scoring semantics, add a new subsystem, rebuild the visual system or chase a competitor's newest feature. Preserve a known-good candidate for recovery; do not overwrite user work to get back to it.

## 10. Worker orchestration and living status

Root owns priorities, architecture/policy decisions, test design, review and integration. Workers implement bounded packets. No worker may mark its own feature shipped solely because a command exited zero.

- Use Claude Sonnet low/medium for mechanical documentation/schema annotations after contracts are decided; high for bounded multi-file implementation. Use Opus high/max for difficult concurrency, authorization, mathematical or architectural disputes. Honor an explicit user model override; never silently substitute an unavailable model.
- OpenCode/Copilot can handle disjoint UI, route inventories and mechanical tests using models actually available in their CLI. Verify availability and quota before launch; do not assume Kiro or any provider still has credits.
- Maximum two coding lanes plus an independent reviewer when memory allows. Shared service/model/migration files have one owner. Do not delegate overlapping edits casually.
- Give workers the minimum relevant context, exact file ownership and test oracles. Require `SUMMARY / FILES / TESTS / GAPS`, actual artifacts and red-before-green for bug fixes.
- Root reviews security/math-sensitive diffs and rejects fake evidence, weakened tests, silent policy changes, catch-all exception masking and claims beyond the tested contract.
- Quota limits trigger an explicit choice, not endless retries. Avoid repeated contender sweeps and rereading the entire repository. Research again only if it can change a live decision.

For each work item maintain this record here or in its linked evidence:

```text
ID / owner / state / last updated UTC
Scope and files:
Decision and prerequisites:
Allowed case + attack oracle:
Actual commands and source/image identity:
Results and evidence link:
Remaining limitations:
Reviewer sign-off:
```

Move an item to VERIFIED only after independent review. After code changes, mark affected evidence stale until rerun. Preserve failed hypotheses and negative results. No countdown, pass count, model availability or competitor claim should be treated as timeless.

### Post-submission roadmap, not part of tier claims

After preserving the frozen submission, consider full historical publication snapshots, calibrated plan-aware simulation, richer import/upgrade tooling, explicit feedback and appeals, advanced accessibility/localization, administrator key rotation and larger-scale operation. Closure remains research unless its proof obligations are genuinely met. Do not change the judged revision without organizer permission.

Prepare a technical write-up from actual discoveries: PostgreSQL race failures, the private-note contract bug, the limits of normalization/anchors, and the feature cuts that improved the whole product. Publishing it remains a user-authorized external action.

## Final decision

**Our route to a credible first-place case is not "add everything". It is complete product journeys + a memorable, honest judging-readiness workflow + independently repeatable operational proof.** Protect T1/T2, finish manual tier evidence, make the interface comfortable, make adoption boring, and show the failure recovery. If an extra feature delays those outcomes, it moves below the release line.

## Worker allocation (Mon 28 Sep, by orchestrator)
- Qoder and Kiro: out of credits. OpenCode (free): bulk, precisely specified tasks. Copilot (metered, strongest): only the hardest correctness/security work. Max 2 OpenCode + 1 Copilot while RAM ≈ 2 GB; no Docker builds while three workers run.
