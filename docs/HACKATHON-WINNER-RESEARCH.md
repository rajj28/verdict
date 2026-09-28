# VERDICT: whole-product winning strategy

Research date: 2026-09-27. Research and recommendations, not new implementation claims.

This report combines an actual Claude Code Opus 5.5 / max-effort research pass with orchestrator source checks and architecture review. The completed, redirected Claude pass reported USD 1.7891186; an earlier narrow pass was interrupted when the user changed direction, and its final cost was not captured. No competitor project was executed or copied.

## The decision

**Build the platform an organizer can use for an entire event, with excellent participant and judge experiences, dependable operations, and a convincing handover.**

Closure, counterfactuals and score replay are optional research, not the product identity. The user's explicit direction supersedes the older Closure-first roadmap in [FEATURES.md](../FEATURES.md).

Our strongest package is a connected lifecycle:

- Participants understand requirements, collaborate safely, submit with certainty and receive appropriately released feedback.
- Judges can start quickly, review comfortably, recover from interruptions and report problems without exposing peer ballots.
- Organizers can set up, operate, resolve incidents, publish, archive and run the next event without spreadsheets or developer intervention.

The aim is top overall placement, Best Judging Engine consideration and evidenced bonus eligibility. These are separate evaluation opportunities, not guaranteed or automatically stackable awards. Correctness and judging integrity account for 65% of the stated base rubric; operational readiness and maintainability must be visible too. [Dogfood specification and evaluation materials](https://dogfoodhack.com/spec/)

## What real winners suggest

This is a targeted sample: ten projects across four event editions, plus operational references. NASA category winners are not a single overall first place; ETHGlobal finalists are not described as overall winners. Award status does not prove that a particular feature caused the win. Every proposed transfer in the last column is **our inference**, not a capability claimed by the original project.

| Project and primary source | Verified recognition | Distinctive mechanism described by its source | Useful VERDICT adaptation |
|---|---|---|---|
| [EasyInsurance](https://devpost.com/software/easyinsurance) | HackZurich 2023, first overall | Converts an existing insurance policy into a proposed cheaper equivalent; the team reports an actual policy switch | Finish a concrete task end to end: import a roster, resolve errors, send invites and see acceptance. Do not borrow its cloud dependencies |
| [Landsat Connect](https://www.nasa.gov/learning-resources/stem-engagement-at-nasa/nasa-international-space-apps-challenge-announces-2024-global-winners/) | NASA Space Apps 2024, global Best Mission Concept | Location-targeted satellite notifications | Role- and state-specific reminders, with a recipient preview and locally inspectable outbox |
| [G.R.O.W. / Team I.O.](https://www.nasa.gov/learning-resources/stem-engagement-at-nasa/nasa-international-space-apps-challenge-announces-2024-global-winners/) | NASA 2024, global Local Impact | Makes complex environmental information accessible | Explain rules, submission status and publication states in plain language rather than exposing database terminology |
| [Eco-Metropolis](https://www.nasa.gov/learning-resources/stem-engagement-at-nasa/nasa-international-space-apps-challenge-announces-2024-global-winners/) | NASA 2024, global Most Inspirational | A data-informed city-building game | A labelled practice event where an organizer learns the real workflow without touching a live event |
| [PureFlow](https://www.nasa.gov/learning-resources/stem-engagement-at-nasa/nasa-announces-2025-international-space-apps-challenge-global-winners/) | NASA 2025, global Best Mission Concept | Habitat design, validation and threat testing | Operational rehearsal and setup checks before opening an event; no need for a 3D interface |
| [Astro Sweepers](https://www.nasa.gov/learning-resources/stem-engagement-at-nasa/nasa-announces-2025-international-space-apps-challenge-global-winners/) | NASA 2025, global Galactic Impact | Combines operational risk analysis with compliance reporting | A usable event handover: archive manifest, data inventory, export checks and recovery instructions |
| [HerCode Space](https://www.nasa.gov/learning-resources/stem-engagement-at-nasa/nasa-announces-2025-international-space-apps-challenge-global-winners/) | NASA 2025, global Best Use of Storytelling | Illustrated explanations of scientific concepts | A coherent project presentation and five-minute lifecycle demo, not a tour of unrelated dashboards |
| [Ephi](https://ethglobal.medium.com/ethglobal-brussels-2024-recap-68512e2502a8) | ETHGlobal Brussels 2024 finalist | Contextual actions within existing web pages | An organizer can embed the gallery and public event status without maintaining a second event website |
| [Oh Snap!](https://ethglobal.medium.com/ethglobal-brussels-2024-recap-68512e2502a8) | ETHGlobal Brussels 2024 finalist | Context supplied before a transaction | Explain consequences before publishing, disqualifying or sending a bulk notification; revalidate on execution |
| [BananaBets](https://ethglobal.medium.com/ethglobal-brussels-2024-recap-68512e2502a8) | ETHGlobal Brussels 2024 finalist | Event-outcome predictions | **Reject the literal feature.** Betting and predictions are a poor fit for a trusted judging platform |

Additional references sharpen the operating model:

- [Gavel's creator](https://anishathalye.com/gavel-an-expo-judging-system/) describes an operational system that directs judges to projects and collects comparisons. The useful lesson here is reducing judge coordination overhead, not copying a platform or adding another estimator.
- [MadHacks' organizer account](https://ben.enterprises/hackathon-judging) reports judge shortages, an assignment algorithm that stalled during the event and inconsistent table labels. These are reasons for bounded assignment, capacity checks and usable fallbacks. Its simulation results are not benchmarks for VERDICT.
- [MLH's judging guide](https://guide.mlh.com/general-information/judging-and-submissions/judging-plan) recommends rehearsing custom systems, retaining exports/backups and communicating progress. Its in-person timing assumptions should not become defaults for a multi-day online event.
- [AnonVote](https://ethglobal.com/showcase/anonvote-uc7w0) received MACI and Worldcoin category prizes at ETHGlobal London 2024. Its identity infrastructure is not an offline feature we can simply remove while retaining its claims. Borrow the threat-model discipline, not that dependency.

## Five whole-product upgrades

These are ranked investment areas, not permission to implement every bullet. Extend existing services and pages first. An existing route or passing unit test does not prove an excellent browser experience.

### 1. Participant submission studio

**Outcome:** a team knows what is required, what is saved, what was submitted and what judges will see.

Already present in inspected templates: a checklist, deadline display, stale-edit handling, revision list and receipt digests. Do not rebuild these under a new name.

The useful next slice is server-authoritative readiness with actionable field errors, a scoped presentation preview, clearer saved/submitted states and a downloadable revision receipt. Use the same validation logic as submission. A preview may reveal only the team's own data; it is not role impersonation. Add revision diffs only after the core journey is reliable. Public rules should come from the configured rubric rather than duplicated prose.

**Demonstration:** two teammates join, edit concurrently, resolve a stale update, submit, obtain a receipt and see a correct closed-window state. Complete the same flow on a narrow screen with keyboard navigation. A required-field omission must produce the same reason in readiness and in the submission API.

**Tradeoff:** attractive client-side checklists can lie. Server parity and clear recovery matter more than animations. A digest is a consistency aid, not proof of external authorship or legal timestamping.

### 2. Focused, resilient judge workspace

**Outcome:** judges spend their effort evaluating projects, not learning the portal or recovering lost input.

The current review template already combines evidence and rubric, offers shortcuts, autosave and explicit submit. Improve onboarding, visible save failures, resumability and conflict/problem reporting. A reported unavailable demo must remain a coverage incident, never turn into a zero score. Reassignment must reuse track, conflict and ownership checks.

Local draft recovery is a conditional enhancement: bind any buffer to the authenticated user, event, review and server revision; expire it, clear it on logout/account change, and never automatically submit after a deadline. Shared-device privacy and stale-save races need browser tests before shipping. Start with trustworthy server save status and navigation protection.

**Demonstration:** complete several reviews with the keyboard; interrupt a save; recover without silently overwriting a newer revision; report a conflict; show another judge's direct API request being denied. Do not leak standings through task ordering or messages.

**Tradeoff:** new assignment states affect progress counts, ranking inputs and exports. Specify those semantics before adding a button. Physical table maps and QR routing are deferred: Dogfood is online, and those additions do not justify immediate schema expansion.

### 3. Organizer runbook and rehearsal event

**Outcome:** an organizer always knows what needs attention and can safely act on it.

Use the existing Command Center, Decision Room, setup, progress and outbox pages. Add a concise phase-oriented action list with concrete blockers, links and bulk-action previews. Commit actions must recheck the same policies under lock; a preview is not authorization and can become stale.

The most interesting addition to evaluate is a **practice event generated from configuration, with synthetic people and projects**. Walk through invitations, submission, assignment, scoring, incidents, publication and archive using the ordinary APIs. Keep it unmistakably separate from the real fixture and live events. Do not clone private ballots, credentials or actual participant information into a public demo. Rehearsal outcomes cannot certify production data.

**Demonstration:** an organizer completes a practice lifecycle, handles an unstarted assignment, reviews a notification's recipients and publishes through normal guards. Show a stale action preview being rejected or recomputed. Measure queries and elapsed time on actual data.

**Tradeoff:** keep the runbook actionable; do not create a second policy engine or another dashboard full of charts. Historical fixture shortfalls are not a known outstanding-assignment roster, so do not invent missing judges or review batches.

### 4. Clear public experience and controlled feedback

**Outcome:** participants and visitors understand the event, while sensitive judging information stays private.

Connect the gallery, search/filter, public rules, voting, published awards, team feedback and certificates into a coherent journey. Make draft, withdrawn, closed and published states visually distinct. Expose result version history and public explanation only under an explicit release policy. Never reveal private organizer notes, review identities, private answers or hidden vote totals through an embed or export.

**Important current review finding:** the judge template promises private comments are never shown to teams, but `project_feedback()` collects `review.comment`. That contract must be resolved before presenting released feedback as safe. Removing names or shuffling comments is not anonymity and does not authorize disclosure. Keep organizer-private notes separate from feedback explicitly intended for participants.

**Demonstration:** a visitor cannot obtain hidden results from pages, APIs, exports or widgets; a team receives only its authorized released feedback; tied awards and superseded publications remain understandable; a signed judge record verifies and a tampered record fails.

**Tradeoff:** certificates, HMAC-based links and Ed25519 participation records are different artifacts. Do not claim identical public verification properties for all of them.

### 5. Adoption and recovery package

**Outcome:** Raptors can install, operate, integrate with and leave VERDICT without contacting its authors.

Prioritize validated OpenAPI plus a UI-action map, accurate startup instructions, a private event export inventory and a demonstrated restore path. Build on the existing JSON/CSV exports, signed records, embed and durable webhook worker. A dry-run roster importer with clear row errors is useful after these fundamentals work.

Separate two contracts:

- **Portable event export:** authorized event data, media, schema/version manifest and public verification material; no passwords, active tokens or private signing keys. Imports need an explicit identity/ID-remapping policy and provenance. They must not silently rewrite historical signed artifacts.
- **Operational backup:** PostgreSQL plus media and protected server/signing secrets, with restricted storage and a tested restoration procedure. Losing `SECRET_KEY` can invalidate HMAC-derived certificate links as well as sessions; the worker's initial sessions-only claim was rejected.

**Demonstration:** create an event, export and restore into an isolated fresh environment, compare semantic records and media bytes, validate preserved historical artifacts, and reject corrupt/path-traversal/oversized archives without partial writes. Exercise webhook failure/retry against a local test receiver without weakening production SSRF protections.

**Tradeoff:** media counts alone do not establish a correct restore; equal record counts do not prove equal data. Checksums detect byte changes, not trustworthy origin. Do not merge audit chains or regenerate signatures and call the result the original history.

## Tiers, prizes and bonuses: separate evidence

The organizer clarification supplied to this project distinguishes the T1/T2 portal checks from manual T3/T4 review. Automated coverage is a minimum, not complete certification. Manual review includes the UI, source, architecture and evidence; it does not excuse missing backend enforcement. See [organizer-file audit](ORGANIZER-DOWNLOAD-AUDIT.md).

| Target | What must be convincing |
|---|---|
| T1 | Complete auth/roles/event/team/submission/gallery journey; deadline and ownership checks survive direct requests |
| T2 | Usable judge invitations/assignment/scoring/progress; weighted rubric, isolation, defensible normalization, reliable exports |
| T3 | Working access modes, voting/comments, hidden totals, fair ordering and abuse controls, demonstrated through both UI and direct API tests |
| T4 | UI/API parity and documentation, webhook delivery lifecycle, certificates/records, embed and real bulk portability |
| Grand Prize | The entire seeded portal works, looks coherent, is maintainable and can be operated from its documentation |
| Best Judging Engine | Assignment constraints and coverage, transparent scoring assumptions, appropriate normalization, privacy, reproducibility and honest mathematical limits |
| Normalization Proof | Actual-fixture results plus clearly labelled synthetic known-truth experiments; disclose negative results and selection assumptions |
| Pairwise Mode | Complete alternative judging workflow, model assumptions, disconnected/tied/sparse comparison cases and reproducible tests |
| Threat Model | Concrete submission/voting/judging attacks, implemented controls, residual risks and evidence; not just an attack-name list |
| API First | Every UI action mapped to a documented, validated API; an OpenAPI file's mere existence is insufficient |
| Write Up Quest | A substantive account of measured results, rejected designs and debugging decisions, supported by reproducible artifacts |

The current organizer specification explicitly says the four bonus challenges break ties and inform Best Judging Engine; they do not increase the weighted base score. Do not assume the separate cash awards can stack. Do not alter `.dogfood.toml` claims merely to match ambition. [Official bonus clarification](https://dogfoodhack.com/spec/)

## Delivery order and final evaluation

1. Capture a fresh PostgreSQL/Compose/official-acceptance baseline and evaluate T1-T4 independently. Preserve existing event data. Repair privacy and operational blockers before expanding state machines.
2. Complete the three core user journeys. Work in separate participant, judge and organizer slices with shared policy contracts; give each schema change its own reviewed migration rather than one giant speculative migration.
3. Add the most useful missing experience improvements, preferably the runbook/rehearsal and adoption workflow. Defer in-person maps, extra estimators, cloud AI, betting, blockchain infrastructure and decorative 3D.
4. Test browser journeys, API isolation, role/cache boundaries, restart recovery and export/restore. Measure latency and query counts instead of describing the app as fast without data.
5. Finish the required documents, screenshots, accurate tier matrix and a five-minute complete lifecycle demo. Include a real-fixture segment and label synthetic practice data. Record shortcomings explicitly.

The whole-product gate is a stranger using the instructions successfully: start the prepared offline stack, join and submit, judge in isolation, operate the event, publish appropriately and recover data. An offline runtime demonstration with locally available images is distinct from building images from a clean cache; document preparation and downloads honestly.

## Review limits

The source descriptions establish published award status and claimed mechanisms, not independent performance. This is not a survey of every event or project. The redirected Claude pass made ten web calls; four yielded usable primary-page content. NASA judging-process pages were inaccessible or unhelpful and are not used as evidence. Winner pages supplied to that pass had been checked by the orchestrator; they were not all reopened by Claude. Current competitor README claims were not treated as verified benchmarks.

The orchestrator rejected or narrowed worker suggestions involving unassigned-team exposure, automatic private-comment release, mandatory new rubric fields, in-person routing, a single large schema migration, simplistic secret-loss claims and checksum-only restoration claims. The resulting plan is a reviewed direction, not an instruction to blindly build the entire list.
