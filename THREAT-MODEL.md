# Voting threat notes

## Community voting (T3)

| Threat | Control | Evidence / limitation |
|---|---|---|
| Repeated ballots from one identity | Unique per-event voter constraints for user, normalized email hash, and device hash; event and ballot row locks; duplicate creation returns 409. | `tests.test_community` checks authenticated, email, and device duplicates. Open-link device cookies deter casual repeats but do not stop a voter clearing cookies or changing devices. |
| Judge or organizer self-voting | Role conflict is checked in the write service for every access mode and returns 409. | `test_window_and_role_conflict_are_enforced`. |
| Quadratic budget manipulation or concurrent over-spend | Complete ballot replacement is validated under the event and ballot locks; the server recomputes sum(votes²) against the configured credits before writes. | `test_quadratic_budget_is_enforced_without_mutating_previous_ballot`. |
| Leaking live vote counts | Read policy gates tallies, rankings, pages, and CSV output until `voting_close_at`; organizers retain live access. Voided ballots are excluded. | `test_results_hidden_from_voters_in_api_and_page_until_close` and `test_results_become_public_at_close_and_voided_ballots_are_excluded`. |
| Ballot stuffing from one network | Hashed remote IP limits ballot creation to 30 per hour; more than 10 new voters in 10 minutes creates one burst flag for the interval. | `test_ip_hash_limits_new_ballots_to_thirty_per_hour` and `test_burst_from_one_ip_hash_creates_an_abuse_flag`. Shared NATs can throttle legitimate voters; rotating IPs can evade the control. |
| Rapid edits or comment flooding | Ballot changes are capped at 10 per voter per hour; comments are capped at five per user per ten minutes. | Covered by voter/comment throttle tests. |
| Comment script injection | Comments are plain text and rendered by Django's auto-escaping template pipeline; organizers hide/restore with a reason and each state change is audited. | `test_public_comments_escape_and_organizer_moderation_is_audited`. |
| Email identity aliasing | Emails are lowercased; Gmail/Googlemail dots are removed and plus suffixes stripped as a documented alias heuristic. Only an HMAC-like keyed hash is stored; a signed, expiring link verifies control of the mailbox. | Email provider alias behavior is not universal; only Gmail-style normalization is applied. |

Ballot order uses a stored per-ballot random seed and is replayable for audit. Voter emails are never included in public output, audit summaries, tallies, or votes.csv.
