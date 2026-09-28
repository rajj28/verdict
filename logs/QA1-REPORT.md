# QA1 Docs Sweep — findings log

Scope: `README.md`, `ARCHITECTURE.md`, `DATA-MODEL.md`, `JUDGING.md`,
`THREAT-MODEL.md`, `docs/*.md` (not `docs/process/`).
Checks per doc: (1) relative MD links resolve incl. anchors, (2) backtick repo
paths exist, (3) cited test modules/names exist, (4) `manage.py <cmd>` is real,
(5) `python scripts/<x>.py` exists + `--help`/runs, (6) cross-doc numbers agree.

Progress is appended document by document.

## 1. README.md — VERIFIED, no fix

Links: every Documentation-table target resolves (`ARCHITECTURE.md`,
`DATA-MODEL.md`, `JUDGING.md`, `THREAT-MODEL.md`, `docs/API.md`,
`docs/openapi.yaml`, `docs/TIER-EVIDENCE.md`, `docs/NORMALIZATION-PROOF.md`,
`docs/UNCERTAINTY.md`, `docs/PUBLICATION-POLICY.md`, `docs/BACKUP-RESTORE.md`,
`docs/SHOWCASE.md`, `docs/TOUR.md`, `docs/REAL-WORLD-JUDGING.md`,
`docs/ASTRA-FINAL-AUDIT.md`, `docs/ADVERSARIAL-*.md` glob matches 3 files,
`docs/process/`, `docs/process/README.md`); inline `docs/TOUR.md:45` OK.
Paths: `vendor/wheels` exists with exactly 31 wheels as claimed;
`src/static/vendor/`, both font LICENSE files, `requirements.txt`,
`.env.example`, `fixtures.json`, `.dogfood.toml`, `docker-compose.yml`,
`docker-compose.offline.yml` all exist.
Commands: `manage.py runportal`, `scripts/backup.py create/restore`,
`scripts/normalization_proof.py`, `scripts/uncertainty_evidence.py`,
`scripts/gate.py`, `scripts/verify_tiers.py`, `scripts/attack.py`,
`scripts/offline_images.py save|load` (has save/load/check) all real.
Tokens: API.md demo tokens match `.dogfood.toml` and `bootstrap.py`
(admin token `vd_demo_admin_c0ffee5eed01` in bootstrap+API.md, correctly
absent from `.dogfood.toml`).
Numbers: 137 ops verified (schema 137 = table 137, both directions);
28/28+14/14 match `attack-report.txt`; 1921 rows/audit-head-7/1-of-1
matches BACKUP-RESTORE recorded run; 15.43/22%/tau 0.669->0.877 match
evidence docs. Two REPORTED (not fixed, evidence-backed) mismatches:
(a) claims table says verify_tiers 59/59+2 skipped but committed
`tier-evidence-report.txt` still shows 25/25 (stale artifact; TIER-EVIDENCE
says the committed file "is that output"); (b) claims-table suite size
1,124+65 vs dated 986/987+57 gate runs in ADVERSARIAL-INTEGRATION
(historical snapshot, superseded).

## 2. ARCHITECTURE.md — VERIFIED, no fix

All ~70 backtick repo paths exist (settings/urls/bootstrap/clock/errors/
middleware, all app services+policy files, engine/closure/consequences/
prizes, showcase/calibration/tour, backup+`backup_fingerprint.py`,
`offline_images.py`, all 7 check scripts, every cited template under
`src/templates/`, `api-forms.js` + 7 page scripts, `tests/test_engine.py`,
`test_deadlines.py`, `test_adversarial_t1/t2.py`, `test_consequences.py`,
`test_uncertainty_integration.py`, `test_astra_final.py`, `test_tour.py`,
`docs/ADVERSARIAL-T1-RACES.md`, `docs/NORMALIZATION-PROOF.md`,
`docs/ADVERSARIAL-T2-PRIVACY.md`, `docs/ADVERSARIAL-INTEGRATION-20260927.md`).
Commands: `manage.py deliver_webhooks --workers 2` real (flag exists,
default 2). `attack-report.txt` confirms 28/28 + 14/14 cited in decision 1.

## 3. DATA-MODEL.md — VERIFIED, no fix

All model/service/template paths exist; spot-checked constraint names exist
in code (`event_submissions_window_ordered`, `role_unique_per_event_user`,
`member_unique_team_per_event`, `project_one_active_per_team`,
`comparison_canonical_pair`, `criterion_weight_positive`,
`audit_chain_sequence_uniq`, `community_voter_event_user_uniq`,
`interop_record_one_per_judge_event`). Commands `manage.py
backup_fingerprint` and `manage.py prune_tour_sandboxes` real;
`tests/test_import.py`, `tests/test_adversarial_t1/t2.py` exist;
`scripts/backup.py`, `scripts/normalization_proof.py` exist;
`docs/BACKUP-RESTORE.md` resolves. Windows-style
`.venv\Scripts\python.exe` matches repo host convention, not a typo.

## 4. JUDGING.md — VERIFIED, no fix

Only relative MD link (`docs/UNCERTAINTY.md:271`) resolves. All code paths
exist (`judging/assign.py`, `forecast.py`, `results/engine.py`,
`results/services.py`, `results/prizes.py`, `results/closure.py`);
test modules `test_engine.py`, `test_robustness_contract.py`,
`test_closure_engine.py`, `test_adversarial_t2.py` exist; `manage.py
verify_publication <pub_id>` and `scripts/verify_record.py` real.
Every fixture number cross-checked against docs/NORMALIZATION-PROOF.md and
matches: lambda=100, CV RMSE 19.52 vs 19.52 baseline, 19 of 40 moves
(prj_28 24->28; prj_34/prj_11 83.33 raw -> 83.35/83.26), sigma chain
0.4198->0.3292->0.3201 (lam0 0.6145, lam2 0.1908), LOO 19.86 vs 19.28
(best lam10 19.17), permutation 24.40/p=0.381, adaptive lambdas
44.9->1.3->0.6->0.5, LOJO 24/29 + top-3 22/29, LORO 115/121, 1-review
midpoint flip, BT rho 0.8493/tau 0.6667 with 15 projects >5 apart.

## 5. THREAT-MODEL.md — VERIFIED, no fix

All 65 cited test names verified present in their cited modules
(community/judging/deadlines/results/consequences/engine/isolation/
assignment/pairwise/release_security/accounts_api/tokens/core/audit_chain/
publication_policy/t4/probe/exports/projects/events/teams/astra_final).
Every `file:function` reference verified in code (community services+policy,
judging services+policy, results services+engine, audit services, interop
webhooks/signing/certificates, accounts tokens/throttles/api, core
clock/middleware, projects services). Probe source `src/core/probe.py`
contains exactly 28 `Attack(` entries, matching "28 named attacks" and the
28/28 claim. Section-6 commands all real (`test` modules exist;
`migrate`, `integrity_probe`, `integrity_probe --write` flag exists).

## 6. docs/ADVERSARIAL-INTEGRATION-20260927.md — VERIFIED, one REPORTED path

Scripts `verify_tiers.py`/`attack.py`, module `test_adversarial_t2.py`,
`src/results/services.py` all exist; gate commands cited are historical
evidence. REPORTED (not fixed — text already discloses it): line 145 cites
`docs/evaluation-20260927-hardening/`, which does not exist; the doc itself
says stdout was "inspected in the terminal only (no copies saved under
`docs/evaluation-20260927-hardening/`)", so the absence is intentional and
documented. Dated counts (25/25 tiers, 986/987+57 gate) are historical
snapshots superseded by current claims (59/59, 1,124+65) — reported, not
edited, per packet rule on evidence-backed numbers.

## 7. docs/ADVERSARIAL-T1-RACES.md — VERIFIED, no fix

`src/teams/services.py` exists and contains `_locked_team` (the documented
fix); `tests/test_adversarial_t1.py`, `tests/test_teams.py`,
`tests/test_deadlines.py` exist; cited `manage.py test` invocations are
 well-formed; DATABASE_URL/test-DB/DATA_DIR values are historical run env,
not repo paths. No links, paths, or commands broken.

## 8. docs/ADVERSARIAL-T2-PRIVACY.md — VERIFIED, no fix

`src/results/services.py`, `tests/test_adversarial_t2.py`,
`tests/test_results.py`, `tests/test_decision_room.py` exist;
`src/templates/judge/review.html` exists; forward ref to
`docs/ADVERSARIAL-INTEGRATION-20260927.md` (2x) resolves. RED/GREEN
transcripts are historical evidence — not edited per packet rule.

## 9. docs/API.md — VERIFIED empirically, no fix

Machine-checked: `docs/openapi.yaml` holds 137 operationIds and the doc
table holds 137 rows with zero drift in either direction; section counts
(13+22+7+11+26+4+17+10+4+5+4+7+2+1+4) sum to 137, matching README.
`manage.py test tests.test_api_first` run 2026-09-28 against isolated PG:
5 tests OK, confirming the doc's own "how we keep this true" section.
`manage.py spectacular` (with `--file/--validate/--fail-on-warn` flags via
drf-spectacular), `manage.py deliver_webhooks`, `manage.py runserver` real;
routes `/api/schema/`, `/api/docs/` exist in `src/verdict/urls.py`;
`src/static/js/api-forms.js`, `src/templates/`, `src/static/js/` exist;
demo tokens match `.dogfood.toml`+bootstrap. Curl walkthrough not
re-executed (needs live server); its internal tokens/ids are consistent.

## 10. docs/ASTRA-FINAL-AUDIT.md — VERIFIED, one REPORTED path

Historical audit record; baseline-relative file:line refs are intentionally
frozen, not repaired. REPORTED (not fixed): line 69 cites
`scripts/astra_final_http.py .data/astra/dogfood.toml`, but
`scripts/astra_final_http.py` does not exist in the tree (likely an
untracked helper, never committed; 191/191 claim therefore not
re-runnable from this checkout). All other cited modules exist
(`test_astra_final`, `test_publication_policy`, `test_adversarial_t2`,
`results/services.py`, `results/engine.py`, `interop/views.py`,
`static/js/consequences.js`, `scripts/uncertainty_evidence.py`,
`docs/UNCERTAINTY.md`). Uncertainty stats quoted (sigma 15.431882, df 81,
0.22, [1,16], 0.465, 0.227 s) match docs/UNCERTAINTY.md.

## 11. docs/AUDIT-INTEGRITY.md — VERIFIED, no fix

`tests.test_audit_chain` exists. API routes cited
(`events/{slug}/audit/verify`, `.../audit/checkpoint`,
`admin/audit/verify`, `.../checkpoint`, `admin/audit/{chain_id}/...`)
confirmed as operationIds in `src/audit/api.py`
(`event_audit_verify`, `admin_audit_verify`, `event_audit_checkpoint`,
`admin_audit_checkpoint` + chain variants); page `/manage/{slug}/audit`
route exists in `src/audit/urls.py`. Embedded Python verifier is
illustrative pseudocode for an offline checkpoint, not a cited repo path.

## 12. docs/BACKUP-RESTORE.md — VERIFIED, no fix

`scripts/backup.py --help` shows create/verify/restore with the exact flags
cited (`--out`, `--project`, `--dry-run`, `--yes`, `--timeout`);
`manage.py backup_fingerprint` / `manage.py verify_publication` real;
`scripts/verify_record.py` exists. Recorded-run output (1921 rows, audit
head 7, 1/1 publications verified) matches README's claims table exactly.
The `manifest.json` example (2093 rows, head 412, 2 pubs) is illustrative
example JSON, not a run claim — no conflict. `/healthz` route exists.

## 13. docs/NORMALIZATION-PROOF.md — VERIFIED (source of truth), no fix

`fixtures.json` and `src/results/engine.py` exist. Numbers internally
consistent and the reference for JUDGING.md (all matched, see §4).
Note: `scripts/normalization_proof.py` has no `--help` (runs the full
proof incl. simulations unconditionally; a `--help` probe ran >120 s),
which satisfies the packet's "--help OR runs to completion" clause, but
byte-for-byte regeneration was not re-run in this sweep for time reasons;
code is untouched since the last recorded regeneration, so prior evidence
stands. No links/paths/typos broken.

## 14. docs/PUBLICATION-POLICY.md — VERIFIED, no fix

Only cited command sequence is `manage.py test
tests.test_publication_policy` (module exists) and `manage.py
verify_publication <pub_id>` (command exists). No repo paths or MD links
to check. Content is the normative contract; no typos found.

## 15. docs/REAL-WORLD-JUDGING.md — VERIFIED, no fix

In-scope refs all resolve: `JUDGING.md` sections, `docs/NORMALIZATION-PROOF.md`,
`docs/SHOWCASE.md`, `docs/UNCERTAINTY.md`, `docs/PUBLICATION-POLICY.md`,
`docs/BACKUP-RESTORE.md`, `docs/TIER-EVIDENCE.md` (T3),
`docs/ADVERSARIAL-T1-RACES.md`, `scripts/normalization_proof.py`,
`src/judging/assign.py`, `src/judging/models.py` (`Conflict`),
`src/community/services.py`, `src/judging/policy.py`
(`order_judge_queue` exists), `tests/test_uncertainty_integration.py`,
`tests/test_consequences.py`, `tests/test_adversarial_t1.py`,
`PerJudgeQueueOrderTests` in `tests/test_judge_pages.py`,
`/events/<slug>/results`, `manage.py verify_publication`.
Numbers (lambda=100, 22%, 46%) match evidence docs. External http(s)
sources (Devpost/MLH/Gavel/NeurIPS/SSRN) out of packet scope (no network);
not checked, not flagged.

## 16. docs/SHOWCASE.md — VERIFIED, one GAPS item (not a link/path fix)

`src/core/showcase.py` contains `SHOWCASE_SEED`; `tests/test_showcase.py`
exists; `manage.py seed_showcase` accepts only `--slug showcase` (others
refused) plus `--reset`; `import_fixture`, `create_prize`,
`close_judging` all exist; routes `/manage/showcase/results`,
`/manage/showcase/calibration` (route `manage/<slug>/calibration` exists),
certificates/results pages exist; `fixtures.json`,
`docs/NORMALIZATION-PROOF.md`, `scripts/normalization_proof.py` resolve;
tau 0.669->0.877 matches README. GAPS (needs live portal, not editable as
typo): step 5 "Known issue" says a fresh publish of any imported event
verifies as `differs` on last-digit floats, while ARCHITECTURE.md decision
10 and ASTRA-FINAL-AUDIT (fix `b8de114`, bit-for-bit reproducible) say this
was repaired — one of the two statements is stale; left for a
seed→publish→verify run to decide.

## 17. docs/TIER-EVIDENCE.md — VERIFIED, one REPORTED staleness

All implementing-file refs exist (projects/events/teams/judging/community/
audit/interop services, views, policy, exports, webhooks, signing,
`src/verdict/urls.py`, `src/core/middleware.py`, `src/core/mail.py`
`OutboxBackend`, `src/core/outbox_api.py`, `scripts/webhook_receiver.py`);
`run.py`, `scripts/verify_tiers.py`, `.dogfood.toml` exist. Manual-check
routes spot-verified: `/projects`, `/api/v1/events` (+slug/register/
tracks/teams/projects/rubric/judges/assignments/progress/exports/results/
votes/webhooks/judge-records/imports paths via api modules),
`/events/{slug}/submission` pages, `/invite?token=`, `/api/schema/`,
`/api/docs/`, `/embed/{slug}` (+framing headers in middleware),
`/events/{slug}/certificates/participation/{prj}` + `/records/{id}` +
`/api/v1/records/verify`, voting email/outbox/results/`votes.csv` routes.
REPORTED (evidence, not edited): line 24 says the committed
`tier-evidence-report.txt` "is that output", but the committed file shows
`summary 25/25 checks passed` while README claims the current script gives
59/59 + 2 skipped — the report artifact needs regeneration against a live
portal (same staleness as README §1 item a).

## 18. docs/TOUR.md — VERIFIED, no fix

Only cited command is `manage.py prune_tour_sandboxes` with
`--older-than-hours` — both real (flag exists, default 6). No repo paths
or MD links cited. DEMO_MODE gating statements consistent with
ARCHITECTURE/DATA-MODEL tour-sandbox descriptions.

## 19. docs/UNCERTAINTY.md — VERIFIED, no fix

`src/results/engine.py` (`rank_uncertainty`) and
`scripts/uncertainty_evidence.py` exist. Fixture numbers (sigma 15.43,
df 81, 22% first-place, rank interval 1–16, 46%/0.465 adjacent order,
39 tied pairs / one group of 40, seed 20260929) match README's claims
table and ASTRA-FINAL-AUDIT §3 exactly. No links/paths/commands broken.

## Cross-document numbers verdict

Agree everywhere checked: 28/28 + 14/14 (README/ARCHITECTURE/THREAT-MODEL/
attack-report.txt/ADVERSARIAL-INTEGRATION/ASTRA); lambda=100 (JUDGING/
NORMALIZATION-PROOF/REAL-WORLD); 19-of-40 + full JUDGING↔PROOF numeric
parity (§4); 22%/46%/15.43/df-81 (README/UNCERTAINTY/REAL-WORLD/ASTRA);
tau 0.669→0.877 (README/SHOWCASE); 137 ops (README/API.md/schema);
1921/head-7/1-of-1 (README/BACKUP-RESTORE); 7/7 run.py (README/
acceptance-report's 7 checks; note acceptance-report's "claimed T1 T2"
line predates current dogfood claimed T1–T4 — stale line in an evidence
artifact, reported). Only live-count claims (59/59 tiers, 1,124+65 suite)
differ from older dated snapshots (25/25, 986/987+57) — reported as
staleness, not edited.

## Doc edits made: NONE

No broken relative links (only one in scope, valid), no wrong backtick
paths (all ~200 resolve; two cited-but-absent paths are disclosed in the
docs themselves or historical), no typos found in sweep. Per packet, only
GAPS/reports above; no doc file other than this log was touched.
