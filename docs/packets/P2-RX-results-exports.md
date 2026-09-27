# Packet P2-RX: results service, publication, all CSV exports, JSON round trip, audit API

Requires P2-EN (engine) and P2-JA (judging) merged. Read AGENTS.md and BUILD-SPEC sections 4 (results, audit), 5 (publication), 6 (Results, Exports, Audit), 9.

## Do
1. `src/results/services.py`: `collect_inputs(event)` (submitted reviews of eligible projects only: status submitted; excluded reviews and superseded/withdrawn/disqualified projects listed separately with reasons), `preview(event)` (engine.evaluate with the event's rubric weights, lambda, target, ranking_method; also derived-BT and live pairwise BT when comparisons exist; input digest = sha256 of canonical JSON of the included inputs + params), `publish(actor, event, note)` (organizer; judging closed else 409 `judging_open`; voting closed if configured; atomic; snapshot rows + judge_rows + params + digest; supersedes previous with mandatory note), `public_results(event)` (latest publication, public projection only: rank, project, team display name, track, official score, n reviews; no judge data).
2. API: `GET /events/{slug}/results/preview` (organizer), `POST /events/{slug}/results/publish`, `GET /events/{slug}/results` (public after publication; 404 `not_published` before, organizers get the preview instead). Public projections must never include judge identities, offsets, individual reviews or comments.
3. Exports in `src/interop/exports.py` (organizer/admin; formula-safe; stable column order; UTF-8): participants, teams, projects (all fields + one column per custom question), judges (tracks, assigned, submitted), assignments, reviews (existing), progress, results (rank, project, title, team, track, n, raw_mean, normalized, rank_raw, rank_normalized, derived_bt, flags), audit. `GET /events/{slug}/exports/event.json`: fixtures-shaped export (event, tracks, judges, teams with member emails, projects, scores) that `interop.importer.import_fixture` can re-import into a new event (round trip). `POST /api/v1/imports` (admin/host): upload that JSON → new event (slug suffix if taken), returns the import report.
4. Audit API `GET /events/{slug}/audit` (organizer; filters action prefix, actor public id, since; paginated).

## Tests
`tests/test_results.py`: preview matches engine on fixture; excluded/superseded never included; publish blocked while judging open (fixture) → close judging → publish ok; second publish requires note and supersedes; public results hide judge data; judge/participant cannot see preview (403); digest changes when a review changes.
`tests/test_exports.py`: every kind: organizer 200 + header row; participant/judge 403; formula escaping; event.json round trip: export fixture event → import as new event → same counts, same raw means.

## Files you own
src/results/{services,policy,api,api_urls}.py, src/interop/**, src/audit/{api,api_urls,policy}.py, tests/test_results.py, tests/test_exports.py.

## Hardening items (BUILD-SPEC 16) in this packet
- Result rows carry `status` (ranked | unranked_no_reviews | withdrawn | disqualified | superseded) + reason; unranked never shown as 0.
- Publish requires `acknowledge_unranked: true` when any eligible project is unranked (409 `unranked_projects` listing them otherwise).
- Store `inputs` (canonical input set) on ResultPublication; `POST /events/{slug}/results/publications/{pub}/verify` + `manage.py verify_publication` recompute from stored inputs and compare rows and the live-DB digest; return a clear verdict ("identical" / "differs: …").
- Decision record data endpoint `GET /events/{slug}/results/publications/{pub}` (organizer): rule in plain words, params, digest, included/excluded reviews with reasons, interventions from audit (window changes, exclusions, assignment batches), limitations.
Tests: unranked handling, acknowledge requirement, verify identical after publish and "differs" after tampering a stored score directly in the DB.

## Winning items (BUILD-SPEC 17) in this packet
- Feedback release: `POST|DELETE /events/{slug}/feedback-release` (organizer, only after a publication, audited); `GET /events/{slug}/projects/{id}/feedback` returns the official score, rank, per-criterion averages and de-attributed comments (shuffled, no judge ids) to that team's members after release and to organizers always; 403 for other teams, judges and visitors.
- Tests: team sees own feedback only after release; other team 403; judge 403; payload contains no judge id/name.

## Decision (BUILD-SPEC 19)
- Migration: `Event.shrinkage_lambda` nullable, null = auto (default); fixture event and demo event set to null. Results services call `engine.evaluate(..., lam='auto')` when null and store the chosen λ and CV table in publication params; the results page shows 'λ chosen by 5-fold cross-validation = X'.
