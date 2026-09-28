# Adversarial T1: invite races and the frozen roster (2026-09-27)

Scope: `src/teams/services.py`, `tests/test_adversarial_t1.py`. PostgreSQL only
(isolated server 127.0.0.1:55433, test DB `test_verdict_hardt1`). No app DB writes.

## Command (red and green used the same env)

    DATABASE_URL=postgresql://postgres:verdict-test-only@127.0.0.1:55433/verdict_hardt1 \
    SECRET_KEY=adversarial-test-only \
    DATA_DIR=C:/Users/Acer/hackathonwinnigproject/verdict/.data-hardt1 \
    .venv/Scripts/python.exe manage.py test tests.test_adversarial_t1 --noinput -v2

Green: same env, `manage.py test tests.test_adversarial_t1 tests.test_teams --noinput -v1`.

## How the races are made deterministic

The test transaction holds the rows first, starts each worker on its own
connection, and continues only once `pg_blocking_pids(worker_pid)` is non-empty,
i.e. PostgreSQL itself reports the worker queued behind a lock. Sleep is only a
10 ms polling interval. Every wait is bounded (15 s, `lock_timeout` on both
sides); worker connections are closed in `finally`. Nothing is mocked in the
races; the window tests freeze the clock with the same patch list as
`tests/test_deadlines.py`.

## Red run (before the fix): Ran 8, FAILED (failures=5), 0 errors

| Test (tests.test_adversarial_t1...) | Red result |
|---|---|
| InviteRaceTests.test_a_link_rotated_while_the_accept_waited_is_refused | joined with the stale revoked invite (`ok <TeamMember>`) |
| InviteRaceTests.test_a_single_use_link_admits_one_of_two_racing_strangers | `2 != 1`: both strangers joined a max_uses=1 link |
| InviteRaceTests.test_one_person_accepting_two_teams_at_once_lands_in_exactly_one | `IntegrityError role_unique_per_event_user` (a 500), not 409 |
| FrozenRosterTests.test_removing_a_member_exactly_at_the_close_is_refused | `ApiError not raised` |
| FrozenRosterTests.test_rotating_the_link_exactly_at_the_close_mints_nothing | `ApiError not raised` |

Controls that passed before and after the fix, so a service that refused
everything cannot go green: an accept that waited on an unchanged team joins
(use_count 1); remove and rotate one microsecond before the close succeed (the
rotated link expires exactly at the close). Every race also asserts exactly one
winner. An earlier attempt errored in setup (`events_event.created_by_id` NOT
NULL, a test fixture bug); it was fixed in the test and is not counted as red.

## Fix (src/teams/services.py only)

- `_locked_team`: every team write now locks event, then team (then invite),
  the order `projects.services` already uses; `team.event` is the locked row.
- `accept_invite`: the unlocked read only finds the team; the invite is re-read
  with `select_for_update` after the event and team locks, and all rules
  (invite state, window, role, one team per event, capacity) run on locked data.
- `rotate_invite` and `remove_member`: window check after the locks, before any
  write, so at `submissions_close_at` nothing is revoked, minted or removed.
- `leave_team` takes the same event-then-team order. Without this, leave (team
  lock, then the FK KEY SHARE on the event from its audit insert) could
  deadlock against accept (event lock, then team lock).

## Green run: Ran 49 tests, OK (8 adversarial + 41 in tests.test_teams)

## Gaps

- Not run: the full suite (another worker is editing) and `tests.test_deadlines`.
- Races are asserted at the service layer (ApiError code and status); the HTTP
  envelope for these races is not exercised.
- Untested: a team deleted while an accept waits (now `invite_invalid` 410, or
  `team_not_found` 404 for rotate, leave and remove).
- Behaviour change: after the close a non-owner calling remove, or a non-member
  calling rotate, gets 403 `window_closed` rather than 403 `not_team_owner` or
  `not_a_member`, following the documented check order.
- The event lock serializes all team writes within one event (`create_team` already did).
- Not re-audited: `community.services` locks User, then Project and Event in one
  statement; unrelated to this change.
