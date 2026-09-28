# VERDICT: T1-T4 and prize-readiness evaluation

Evaluated 2026-09-27, approximately 15:30-15:44 UTC. This is a review of the current dirty working tree, not a final submission certification. No tier claims, application policies, repository visibility or commits were changed during this evaluation.

Later hardening update (approximately 18:53 UTC): the private-note disclosure and multiple T1 race/deadline defects below were repaired and tested. The current source gate passed 987 Django tests on isolated PostgreSQL plus 57 pure engine tests. The rebuilt evaluation image passed official7/7, extended25/25 and attacks28/28 plus14direct. See [current hardening evidence](ADVERSARIAL-INTEGRATION-20260927.md), [T1 races](ADVERSARIAL-T1-RACES.md), and [T2 privacy](ADVERSARIAL-T2-PRIVACY.md). The full suite was run from the host virtualenv, not inside the image; the image checks are separate. This later preview is Internet-capable, not a new offline-run proof. API-schema/docs/browser-manual gaps remain. The original evaluation below is retained as a historical snapshot.

## Outcome

| Area | Current assessment | Evidence boundary |
|---|---|---|
| T1 | Official checks PASS; deeper lifecycle checks PASS | Login UX, accessibility and every edge case are not certified by seven requests |
| T2 | Official checks PASS; strong additional isolation/export evidence | Full manual judging experience, documentation and all assignment/scoring semantics still need release review |
| T3 | Substantial implementation and targeted security evidence; manual sign-off pending | Access modes, comments, quadratic budget, hidden tallies and ordering are supported in code; a full browser lifecycle was not completed in this evaluation |
| T4 | Partial; not ready for a complete claim | OpenAPI validation fails; webhook action coverage, full portability and visual embed behavior need explicit evidence |
| Overall prize readiness | Promising functionality, not yet a finished top-place submission | Core documents, safe feedback semantics, UI review and a complete recorded lifecycle are outstanding |

Keep `.dogfood.toml` at its existing T1/T2 claim until the manual evidence supports a higher claim. The [official seven checks](https://dogfoodhack.com/spec/) do not exercise most lifecycle features. Saved organizer clarification distinguishes manual T3/T4 review; see [download audit](ORGANIZER-DOWNLOAD-AUDIT.md).

## Runtime evidence from a fresh build

Built the current Dockerfile into `verdict-eval:20260927`, using the real Compose services, fresh project-scoped volumes and PostgreSQL 16. The evaluated web and worker image ID was:

```text
sha256:40b67aa270c1036f0f37e6b4b28e6a5d247dcb711b65aedd76215c8bd85f61e1
```

The original `verdict-web-1` / `verdict-db-1` deployment and its data were not modified. The evaluated fixture lives in separate `verdict-eval-20260927_*` volumes. The extended check created a disposable evidence event only in this isolated deployment.

| Check | Observed result | Saved output |
|---|---|---|
| Fresh Compose build/start | Database and web healthy; webhook worker running | Command and image identity recorded here |
| Official `run.py .dogfood.toml` | 7/7 PASS, verified T1/T2 | [Fresh official output](evaluation-20260927/acceptance-report.txt) |
| `scripts/verify_tiers.py .dogfood.toml` | 25/25 PASS | [Extended report](../tier-evidence-report.txt) |
| `scripts/attack.py .dogfood.toml` | 28/28 server probes and 14/14 direct HTTP checks PASS | [Attack report](../attack-report.txt) |
| OpenAPI strict generation | FAIL: 306 errors (62 unique), 5 warnings | [Schema validation](evaluation-20260927/schema-validation.txt) |
| Earlier full isolated-PostgreSQL regression on this application source | 963 Django tests and 57 pure engine tests PASS; no migration changes | Host Python 3.11 run, not a claim that the full suite was rerun inside Python 3.12 Docker |
| Gallery and embed HTML | HTTP 200 | Included in [latency samples](evaluation-20260927/latency-samples.json) |

The root `acceptance-report.txt` already has the same seven-pass content as the fresh output. The timestamped copy establishes this run's provenance; no official runner or fixture edits were made. Both the official and extended scripts can exit zero despite failed checks: the actual PASS lines and totals were inspected.

The attack report has a cosmetic formatter issue: several non-CSV cases append `formula neutralised`. Do not interpret that suffix as evidence for those controls; use the actual named assertion and status.

### Offline boundary and reproduction

The initial evaluation used only a Docker network with `Internal=true`, confirmed by `docker network inspect`. Acceptance and API tests ran **inside** the web container against its local port. Build preparation downloaded Python packages; this was not an offline clean-cache image build.

```powershell
docker compose --env-file ../planning/EVAL-DEMO.env -p verdict-eval-20260927 -f docker-compose.yml -f ../planning/compose-evaluation.override.yml up -d --build --wait
docker compose --env-file ../planning/EVAL-DEMO.env -p verdict-eval-20260927 -f docker-compose.yml -f ../planning/compose-evaluation.override.yml exec -T web python run.py .dogfood.toml
```

These evaluation configuration files are in the shared workspace's `planning` directory, not submission deliverables. They change image tag, test credentials, host port and networking, not application code or fixture dates. Final release evidence must also exercise the distributable Compose setup documented for reviewers.

Docker did not publish the host port while web had only the internal network. For a subsequent host HTTP check, `compose-host-preview.override.yml` added a second, internet-capable network to web. The host then received HTTP 200 at `http://127.0.0.1:18080/projects`. **The current preview configuration is not offline evidence.** The isolated database and worker remain on the internal network. No existing portal volumes were touched.

Headless Edge attempts produced no screenshot despite returning exit zero. Therefore no visual-quality, keyboard-accessibility or complete browser-workflow PASS is claimed. This is a remaining evidence task, not proof of an application rendering defect.

### Small performance sample

Three sequential requests per endpoint, inside the Docker web container, all returned 200:

| Endpoint | Observed elapsed range |
|---|---|
| Fixture results preview | 0.7540-0.8053 seconds |
| Fixture results CSV | 0.7967-0.8394 seconds |
| Public gallery | 0.0173-0.0189 seconds |
| Embed HTML | 0.0149-0.0182 seconds |

This is a smoke sample on this development laptop, not a load benchmark, percentile estimate or SLA. `engine.evaluate()` computes robustness on every call (`src/results/engine.py:1395`); some consumers do not use it. That is an optimization candidate, but the measured fixture requests did not time out. Any optimization must preserve ranking outputs and be benchmarked at realistic concurrency.

## What a manual reviewer still needs

### T1/T2 depth

The official checker does not test registration/session behavior, invite acceptance, edit conflicts, the full rubric, assignment/COI logic, normalization quality, progress UX or most export contents. The 25-check extension covers a meaningful subset, not all of them. The full regression suite supplies additional evidence, but its test count is not a tier score.

Demonstrate the participant and judge journeys end to end; show permission failures through direct requests, not just hidden buttons. Demonstrate weighted criteria, infeasible-assignment explanations, rubric locking and CSV semantics with actual data. The judging documents must explain the selected method, not merely link an engine module.

### T3

Source review found all three access modes, single/quadratic voting, per-identity duplicate constraints, rate limits, seeded ballot order, comments/moderation and policy-gated tally exports. The live attack run verified duplicate rejection, hidden tally denial and excessive quadratic-budget rejection. Excessive budget returns **400**, not the 409 suggested in the initial worker checklist.

Still demonstrate each access mode in a browser, closed/open-window transitions, repeat visits, comment moderation, abuse-flag handling and every public/embedded/export projection. Device cookies and normalized email addresses do not establish one-human-one-vote. Test and document shared-NAT false positives and easy Sybil bypasses.

### T4

- REST actions exist broadly, but the schema generator ignores views without sufficient serializer metadata. A route existing does not make API First complete. Publish a validated schema and a UI-action-to-endpoint map.
- The webhook worker has durable delivery, retries, leasing and replay; complete coverage of every relevant UI action needs an emitter/event catalog and tests. A wildcard subscription alone proves no coverage.
- Certificates and signed judge records exist, but their trust and key lifecycles differ. Demonstrate Ed25519 record issuance, public verification, tampering and revocation.
- Embed HTML returns 200; browser embedding behavior, frame/CSP interactions and presentation remain to be exercised.
- JSON/CSV import/export exists. This is not evidence of full disaster recovery including media, historical signatures, audit provenance and secrets. State the exact portability boundary and test it.

## Highest-value corrective decisions

1. **Resolve feedback privacy before release.** `src/templates/judge/review.html:218` promises organizer-private comments never reach teams, while `src/results/services.py:1023` onward collects those same comments for released team feedback. The internal build spec expects de-attributed feedback, but that does not make the collection notice truthful. Separate explicitly shareable feedback from organizer-private notes; do not retroactively infer consent from old comments or equate shuffling with anonymity.
2. **Finish the required explanations.** README, ARCHITECTURE and DATA-MODEL remain outlines. JUDGING has a substantive pairwise section but major empty sections. The normalization-proof document is more substantial. Explain the actual implementation and reproduce its current numbers; older proof output is not automatically current after engine changes.
3. **Fix OpenAPI and demonstrate parity.** The fresh strict schema failure is concrete. Add missing schemas, stable operation IDs, generated specification, examples and coverage checks. Do not claim the bonus from API-first architecture alone.
4. **Document and test backup/key handling.** `src/interop/certificates.py:11` uses `SECRET_KEY` for HMAC-derived certificate codes. Losing/rotating it affects those links, not just sessions. Ed25519 keys have a separate persisted lifecycle. Restore instructions must account for both. Versioning certificate keys is an option requiring a compatibility design, not a blind schema change.
5. **Settle post-publication correction policy.** The internal BUILD-SPEC says review exclusions occur only before publication, but exclusion/inclusion services allow later actions and explicitly audit `publication_exists`. Immutable publication records and mutable current inputs are different things; the worker's stronger claim that this necessarily mutates a stored publication was not accepted. Define whether corrections require a new publication, then align guards, UI, verifier semantics, docs and tests. Also repair the decision-record `n_components` placeholder and harden malformed stored-publication validation.
6. **Expand the threat model and manual demonstration.** THREAT-MODEL currently concentrates on voting. Cover submission scraping, deadline gaming, collusion, IDOR/media, CSRF, token leakage, denial of service, webhook SSRF/signature handling, private feedback and archive/key risks. Detection heuristics are not proof of cheating or prevention of collusion.

These are current evidence-backed priorities. No fixes to these policies were silently implemented during this evaluation.

## Prize and bonus readiness

The current official site lists **USD 800** for first place, **USD 100** for Best Judging Engine, and four **USD 100** write-up awards. The write-up deadline is October 5, 2026, 18:00 UTC. Whether one team can combine cash awards was not established here. [Official prizes](https://dogfoodhack.com/)

Crucially, the updated spec says the four bonus challenges **break ties and inform Best Judging Engine; they do not add to the weighted base score**. [Official bonus clarification](https://dogfoodhack.com/spec/)

| Opportunity | Readiness | Next useful proof |
|---|---|---|
| Best Judging Engine | Substantial engine and integrity foundations; presentation incomplete | Explain assignment, bias/coverage assumptions, raw/normalized results, pairwise limits and audit boundaries; finish JUDGING and resolve privacy/correction semantics |
| Normalization Proof | Substantial fixture/synthetic material, release rerun pending | Regenerate from current engine; include negative findings. Fixture CV choosing heavy shrinkage does not prove bias removal or an objectively correct ranking |
| Pairwise Mode | Implemented and regression-tested; manual journey pending | Invitation/queue/comparison/abstention/retraction/export/publication demo, including disconnected and sparse graphs |
| Threat Model | Incomplete for the full requested scope | Expand beyond community voting with controls, test references and admitted residual risks |
| API First | BLOCKED by current schema failure and missing coverage artifacts | Valid schema plus every UI action mapped and exercised |
| Write Up Quest | Good source material, no completed write-up verified | Explain the PostgreSQL lock failure, privacy-contract discovery, negative statistical findings and why some features were cut; publish only with user authorization |

## Worker review provenance

Claude Sonnet/medium performed a read-only T3/T4/adoption audit (reported USD 0.6338098). OpenCode `space-bunny-free`/high performed a read-only T1/T2/engine audit. Their findings were checked and narrowed by the orchestrator; source review is not a runtime PASS. Corrections include the quadratic-budget status code, incomplete webhook-coverage evidence, immutable-publication versus mutable-input semantics, and unsupported claims that outlier detection prevents collusion.

For the broader product improvements, see [winner research and whole-product plan](HACKATHON-WINNER-RESEARCH.md). Priority remains a complete participant/judge/organizer product, not a Closure-first feature strategy.
