# Repair verification snapshot

Current backend policy (2026-09-27): PostgreSQL only for runtime, development and tests. SQLite measurements below are historical, not supported setup instructions or current acceptance evidence. Do not relabel those old measurements as PostgreSQL. All new database validation uses an isolated PostgreSQL database; a fresh combined gate is required after the backend-only changes.

Recorded 2026-09-27 13:45 UTC from the actual clock and worker test output. This is repair evidence, **not an official acceptance report**. The working tree is still changing; the complete combined gate has not yet run against the final integrated tree.

All database tests below used disposable data directories and test databases. PostgreSQL checks used an isolated PostgreSQL 16 validation container, not the application's stored event data. The test groups overlap: do not add their counts to claim a number of unique tests.

## Verified gates

Commands are run from the repository root with `.venv/Scripts/python.exe`. Database runs set a disposable `DATA_DIR`, a test-only `SECRET_KEY`, and either SQLite or the isolated PostgreSQL connection. Credentials are intentionally omitted here.

| Repair slice | Command after the Python executable | SQLite / standalone result | PostgreSQL result |
|---|---|---|---|
| Production credential transition, project locking and publication checks | `manage.py test tests.test_release_security tests.test_bootstrap tests.test_tokens tests.test_accounts_api tests.test_projects tests.test_results --noinput -v 0` | 230 tests, 20.828 s, OK | 230 tests, 91.750 s, OK |
| Durable webhook delivery and existing T4 behavior | `manage.py test tests.test_webhook_delivery tests.test_t4 -v 1` | 29 tests, 1.787 s, OK; one PostgreSQL concurrency test skipped | 29 tests, 20.683 s, OK; no skips |
| Offline outbox and community voting | `manage.py test tests.test_outbox tests.test_community --failfast -v 1` | 35 tests, 1.866 s, OK | 35 tests, 6.461 s, OK |
| Pure judging engine | `-m unittest tests.test_engine` | 57 tests, 1.351 s, OK | Not applicable |
| Robustness contract, results and organizer pages | `manage.py test tests.test_robustness_contract tests.test_results tests.test_manage_pages -v 0` | 100 tests, 2.997 s, OK | Not run for this gate |
| Decision Room, initial integration | `tests.test_decision_room` module, initial orchestrator run | 11 tests, OK; subsequent independent review remains pending | Not run for this gate |

The 230-test security run predates the next pairwise implementation edits. It verifies that security slice, not those later changes. The initial Decision Room result likewise does not establish that independent review findings have been fixed.

Additional focused checks: the outbox's isolated OpenAPI schema and administrator HTML access checks passed (3 tests, 0.159 s); its read APIs describe authentication and pagination. `makemigrations core --check --dry-run` and `makemigrations interop --check --dry-run` found no missing migrations. `docker compose config --quiet` exited 0 after the companion worker and mail configuration wiring. These checks do not establish full-schema validity or a fresh application deployment.

## Behavior exercised

- Webhooks: committed database outbox rows, bounded concurrency, persistent retry deadlines, expired-lease recovery, competing PostgreSQL claims, stale-completion protection, disabled-endpoint cancellation, and connection cleanup before HTTP or scheduler waits. A delivery can be repeated after a crash; receivers must deduplicate by event and `X-Verdict-Delivery`. Explicit organizer replay creates a new delivery ID. An already in-flight request cannot be recalled.
- Offline mail: default and demo settings use a private database outbox. Organizers read only their event's messages; administrators can read the platform outbox. Anonymous users, judges, participants and other events' organizers cannot retrieve bodies or voting capabilities. HTML escaping, pagination and constant query counts were checked. Audit records contain no message bodies or recipient addresses.
- Email voting: responses distinguish organizer-queued links from acceptance by the configured email backend. Offline and legacy links still grant ballot access but do not set a mailbox-verification timestamp. Explicit SMTP failures return a domain error and roll back database changes; Django's in-memory test backend remains supported. No live SMTP service was contacted by these tests.
- Deployment configuration: the companion webhook worker starts after web health, which follows migrations and bootstrap; it has bounded workers, signal handling, a restart policy and lease recovery. Optional SMTP variables reach the web container. `WEBHOOKS_ALLOW_PRIVATE` defaults to 0 in both relevant containers and is documented as an isolated-test override that weakens SSRF protection.

## Actual external worker use

A tool-denied OpenCode CLI call using `opencode/mimo-v2.6-flash-free` was attempted for a bounded webhook command patch. Its provider returned HTTP 403; no model patch was received. The webhook implementation used local reviewed patches and tests. Separately, the orchestrator's OpenCode `space-bunny-free` / high call returned an outbox starting design; the implementation corrected its event relation and added ownership, privacy, error handling and tests.

At this snapshot, an actual Claude Sonnet 5 medium review was connected and running with a $2 CLI budget cap and read-only tools. No completed review patch, code output or test result had yet been received from that call. Do not label the current Decision Room as Claude-approved or report a final cost before its result arrives.

## Outstanding evidence

- Final combined application gate after all workers finish, including migration consistency and PostgreSQL regression checks for subsequent edits.
- Completed independent Decision Room review and any resulting regression fixes.
- Fresh application image build, Compose startup and external acceptance on the repaired tree; an isolated PostgreSQL test container is not that deployment rehearsal.
- Full OpenAPI validation, extended tier walkthroughs, offline deployment rehearsal and final submission artifacts.

`acceptance-report.txt` is unchanged and belongs to an earlier revision; it is stale evidence for the current tree. `.dogfood.toml` still claims T1 and T2. This document adds no tier claim and does not replace the organizer's checker.
