# B2: Threat model (bonus), complete and evidenced

Read `AGENTS.md` first. This is a documentation packet with evidence checks; it
changes no application code.

## Why

The bonus reads: "Sybil votes, ballot stuffing, judge collusion, deadline
gaming. Name the attacks you stopped and the ones you did not." The current
`THREAT-MODEL.md` is 16 lines about voting. Reviewers are senior security and
platform engineers: every claim must point at the code that enforces it and the
test that proves it. An honest "not stopped" list is part of the deliverable,
not a weakness.

## Deliverable: rewrite `THREAT-MODEL.md` (repo root)

1. Scope and assets: what VERDICT protects (ballots and scores before release,
   private judge notes, participant emails, result integrity, audit log,
   signing keys and secret key, availability during judging).
2. Actors and trust boundaries: anonymous visitor, participant, judge,
   organizer, admin, webhook receiver, host operator/DB superuser. Draw the
   boundaries as a small text diagram or table.
3. The four named attacks, one section each, same structure:
   attack in one paragraph; what VERDICT does (mechanism, file and function);
   proof (test module and test name, and integrity-probe case from
   `scripts/attack.py` / `manage.py integrity_probe` where one exists); what it
   does not stop (residual risk, stated plainly).
   - Sybil votes (community voting access modes, identity gates, one vote per
     identity, what an attacker with many email addresses can still do).
   - Ballot stuffing (duplicate ballots, replays, concurrent submissions, rate
     limits, tallies hidden until close).
   - Judge collusion (assignment and conflict rules, one role per person per
     event, outlier flags, robustness certificate, leave-one-judge-out; what
     cannot be detected, e.g. coordinated moderate inflation).
   - Deadline gaming (server-time half-open windows checked after row locks,
     edits after close, late score changes, clock skew, results reopening).
4. Other threats in a STRIDE table (one row per threat: category, threat,
   mitigation with file reference, test reference, residual): IDOR and
   cross-event access, CSRF, XSS and the CSP, SSRF through webhooks, token
   theft and revocation, privilege escalation between roles, scraping and
   enumeration of private data (emails never public, public_id only), audit
   log tampering (hash chain), result tampering (digest + two-part Verify,
   limits against a DB superuser who rewrites both), certificate forgery
   (HMAC/Ed25519), denial of service (rate limits, sizes, what is out of
   scope), supply chain (pinned and vendored wheels, offline build, no CDN).
5. "Attacks we did not stop" as its own section, a numbered list, each with why
   and what an organizer can do about it operationally.
6. "How to re-check these claims" section: the exact commands (test modules,
   `scripts/attack.py`, `manage.py integrity_probe`).

## Evidence rules

- Read the code and the tests before writing a claim. Every "stopped" row must
  name a test that exists (`grep` for it) and passes (run it). If you cannot
  find a test, the claim goes to "partially mitigated" with the reason, or to
  "not stopped".
- Run every test module you cite on PostgreSQL and report the command and
  result line in your summary.
- No marketing words. No claim the code does not support. Use the product's
  own terms (public_id, publication version, Verify, review exclusion).

## Scope

Touch only `THREAT-MODEL.md`. Do not edit code or tests; if you find a real
vulnerability, describe it under GAPS with a reproduction instead of fixing it.
Never git commit.

## Done means

`THREAT-MODEL.md` complete, every cited test found and run. Finish with SUMMARY
/ FILES / TESTS / GAPS as `AGENTS.md` says.
