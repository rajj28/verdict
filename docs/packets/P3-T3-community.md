# Packet P3-T3: community voting, comments, hidden results, random ballots, anti-abuse (T3)

Only starts after G2 (T2 complete). Read AGENTS.md, BUILD-SPEC 2, 3, 5, 16, 17.

## Model (src/community)
`VotingConfig` (event OneToOne: `access` open_link | email | authenticated, `style` single | quadratic, `credits` default 16, `max_votes_per_project` for single = 1, `link_token` for open_link), `Voter` (event, kind user|email|device, `user` FK nullable, `email_hash`, `device_hash`, `verified_at`, `ip_hash`, `created_at`; UniqueConstraint per event on each identity), `Ballot` (voter OneToOne, `order_seed`, `submitted_at`, `voided_at`, `void_reason`), `BallotItem` (ballot, project, `votes` int ≥ 0; quadratic cost = votes², sum of costs ≤ credits enforced in the service under a row lock), `Comment` (project, author user, body ≤ 1000 plain text, `created_at`, `hidden_at`, `hidden_by`, `hide_reason`), `AbuseFlag` (event, kind, subject, detail JSON, created_at, resolved_at).

## Rules
- Voting window `voting_open_at <= now < voting_close_at`; outside → 403 `voting_closed`. Voting requires a published gallery; projects = public projects of the event.
- Access: open_link needs `?v=<link_token>` + device cookie (random, HttpOnly) → one ballot per device; email → verification link sent via outbox/SMTP, ballot after verification, one per email (normalized: lowercase, plus-addressing stripped for gmail-style domains documented as heuristic); authenticated → one per user; judges and organizers of the event cannot vote (409 `role_conflict`).
- Ballot order: per-ballot `order_seed` shuffle stored so the order is reproducible and auditable; never sorted by popularity.
- Results hidden: tallies/ranking only for organizers until the window closes (403 `results_hidden` for everyone else, including via exports, API and counts); after close, public results show votes (single) or quadratic sums.
- Anti-abuse: throttles (per IP hash 30 ballots/hour, per voter 10 changes/hour), duplicate detection (same device/email/user → reuse ballot, not a new one), burst detector (> N new voters from one IP hash in 10 minutes → AbuseFlag), organizer panel `/manage/{slug}/voting` (live tallies, flags, void/restore ballot with reason, audit), votes.csv export, every vote change audited (without exposing voter emails).
- Comments: authenticated users, 5 per 10 minutes, escaped plain text, organizers hide/restore with reason (audited), hidden comments disappear publicly.
- Quadratic explanation shown to voters: "Each extra vote on the same project costs more: 1 vote = 1 credit, 2 votes = 4, 3 votes = 9."

## Tests (`tests/test_community.py`)
Window, each access mode, one ballot per identity, quadratic budget (cannot exceed, concurrent edits), random order reproducible per ballot and different across ballots, results hidden for everyone but organizers during the window (API, pages, exports), voiding, throttles, burst flag, role conflict, comments CRUD/moderation/escaping.

## Files you own
src/community/**, src/templates/community/**, src/templates/manage/voting.html, src/static/js/voting.js, tests/test_community.py, THREAT-MODEL voting section notes for the orchestrator.
