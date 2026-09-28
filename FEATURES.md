# VERDICT Closure: optional judging research

Current master roadmap: [FUTURE-UPDATES.md](FUTURE-UPDATES.md). It puts the Judging Readiness Planner + Event Runbook first, with whole-product completion, prioritized fixes, test obligations and submission gates. This file remains optional Closure research, not the release work queue.

Product-direction update, 2026-09-27: the user explicitly rejected a Closure-first strategy. The project priority is the complete participant, judge and organizer experience, dependable deployment, portability, security, documentation and demonstration. The design below is retained as optional technical research, not VERDICT's identity or the next implementation mandate. Its earlier priority language is superseded by the [whole-product research and delivery plan](docs/HACKATHON-WINNER-RESEARCH.md).

Decision date: 2026-09-27. Status: **partial implementation, not a shipped feature or a tier claim**. The pure counterexample/replay kernel and its tests now exist in the working tree (`src/results/closure.py`, `tests/test_closure_engine.py`); their focused PostgreSQL-backed combined run passed. The organizer UI/API adapter, private packet workflow and formal stability certifier remain unimplemented. PostgreSQL-only migration takes priority before further UI integration.

## The bet: VERDICT Closure

**Before publishing, show whether unfinished judging can still change the winner. If it can, show a concrete allowed completion that changes it. If we cannot establish either answer, say so.**

This is a publication-integrity tool, not an early-winner predictor. It answers a question an organizer actually has to decide: "What could the reviews we have not received still do to this result?"

Our existing robustness analysis removes or perturbs received reviews. Closure examines **not-yet-received reviews**, under an explicitly declared assignment roster, and reruns the actual scoring policy. It must not quietly reuse the existing fixed-lambda sensitivity result as an adaptive-policy guarantee.

The product is one small workflow: an organizer-only decision card, a replayable counterexample or justified bound, and a private evidence packet. An impressive chart without those semantics is not this feature.

### Why this is our strongest next differentiator

- It addresses a deliberate fixture problem: missing reviews, alongside the constant scorer and duplicate submission. The organizers explicitly describe those cases in the [specification](https://dogfoodhack.com/spec/).
- It connects our existing normalization, assignment, audit and publication work instead of adding an unrelated subsystem.
- It can be demonstrated with the organizers' actual data and our actual engine, not an invented fairness score.
- It creates a falsifiable engineering claim: another implementation can replay the witness, or a tiny exhaustive test can disprove our certificate.
- Its value is an explained decision and an auditable limitation. It is not a claim that the scoring model identifies the objectively best project.

No feature guarantees a win. My recommendation is to make this the memorable demonstration **after** the outstanding correctness and deployment gates are green, rather than expanding the general feature inventory further.

## Evidence: one hypothetical review changes the fixture leader

Executed 2026-09-27 using `scripts.normalization_proof.load_fixture()` and `results.engine.evaluate()`, without changing the fixture, database or event dates.

Execution-source SHA-256: `src/results/engine.py` = `d70b581975752ae121417deb6a4bcd174ea04675d1cf9945f61788e66c386b64`; `scripts/normalization_proof.py` = `909ad7c3e312fc4b7ab1bcd65d33fc1a3f2c317192b2212534159e1541544f18`. These identify the current uncommitted working-tree inputs, not a released build. Rerun the experiment after engine changes.

Our documented latest-submission duplicate policy supersedes `prj_07` with `prj_41`. It leaves 40 projects and 121 included reviews from the original 41 records and 126 scores. Included coverage is: 8 projects with 2 reviews, 26 with 3, 3 with 4 and 3 with 5.

Crucial limitation: **the fixture contains scores, not an outstanding-assignment roster**. A target of three implies eight hypothetical shortfall slots, not eight known missing assignments. We must not fabricate their identities.

For a labelled one-slot scenario, add `jdg_20 -> prj_10`, with all three criterion scores equal to 5. This judge covers the project's track and has not already scored that project in the included data. The fixture does not establish actual assignment or conflict-of-interest clearance. All other reviews stay fixed; this is not a completion of an inferred eight-slot roster.

| Project | Current normalized score | After the hypothetical review | Rank change |
|---|---:|---:|---|
| Iron Switch (`prj_34`) | 83.348772 | 83.348667 | 1 to 2 |
| Salt Ledger (`prj_11`) | 83.261686 | 83.261686 | 2 to 3 |
| Still Beacon (`prj_10`) | 78.953322 | 86.126445 | 3 to 1 |

The full adaptive procedure reselected lambda in both runs; it chose 100 both times. These values are engine output, not a prediction of the absent judge's opinion. The result demonstrates a reversal for a roster containing that particular hypothetical slot. It does not prove any real judge owes that review or would give those scores.

Reproduce from the repository root in PowerShell:

```powershell
.venv\Scripts\python.exe -c "from scripts import normalization_proof as p; from results import engine as e; import json; c,r,t,n,x=p.load_fixture(); ids=sorted({v.project_id for v in r}); b=e.evaluate(r,c,lam='auto',target=3,method='normalized',projects=ids); a=e.ReviewInput('jdg_20__prj_10','jdg_20','prj_10',{v.key:5 for v in c}); z=e.evaluate(r+[a],c,lam='auto',target=3,method='normalized',projects=ids); print(json.dumps({'baseline_lambda':b.lam,'baseline_top3':sorted(b.normalized.items(),key=lambda q:-q[1])[:3],'scenario_lambda':z.lam,'scenario_top3':sorted(z.normalized.items(),key=lambda q:-q[1])[:3],'hypothetical_review':a.__dict__},indent=2))"
```

Preserve canonical review IDs. CV sorts/shuffles review IDs; replacing them with arbitrary row numbers can change the selected lambda. The replay packet must carry IDs, ordering rules, seed, fold procedure and engine version, not just a score matrix.

## What an organizer would see

One card with the current leader, analysis method, snapshot time, declared pending roster and assumptions. Its outcome has one of these meanings:

| Outcome | Exact meaning |
|---|---|
| `PROVEN STABLE UNDER SNAPSHOT ASSUMPTIONS` | Every admissible completion preserves the specified first-place outcome, with validated bounds and the same scoring policy. |
| `COUNTEREXAMPLE FOUND` | At least one admissible completion changes that outcome, and the complete scenario replays through the actual policy. |
| `UNKNOWN` | Supported analysis did not settle the question within its mathematical or computational bounds. No witness found is not stability. |
| `UNSUPPORTED` | The configured policy or completion domain is outside the verified implementation. |

Every scenario-only run gets an equally prominent `HYPOTHETICAL ROSTER` label. Missing an assignment roster cannot silently become an empty roster and a green result. A genuine, explicitly complete zero-pending roster is different.

The card says what was held fixed: current submitted-score revisions, eligibility, duplicate policy, rubric, judge identities and assignments. Submitted reviews remain editable under existing rules; a snapshot certificate is therefore conditional on those revisions remaining unchanged, not permission to lock judges out. An edit invalidates its applicability to live data. A genuinely locked-score analysis requires a real, documented lock, not just `scoring_locked_at` (which locks scoring configuration, not necessarily submitted ballots).

The analysis never publishes, excludes a score, changes an assignment, closes judging, extends a deadline or withholds a promised review. Stability of first place does not establish fairness, eligibility or completion of all review obligations.

## Only two supporting capabilities

### 1. Blind review-completion workflow

Reuse the existing Command Center for authorized reminders and assignment/rebalance previews. Judges see only their own assigned projects and ordinary instructions, never standings, other judges' scores, reversal thresholds or a "pivotal review" badge.

Even task ordering can leak which projects matter. Therefore reminders and queue order follow a coverage/load/age policy committed **before** inspecting Closure outcomes, not a ranking of winner-changing reviews. Reassignment still requires permission, track eligibility and conflict checks, and creates a new roster snapshot. There is no automatic score-shopping loop or stopping when a favored project leads.

Selective review attrition is a real evaluation problem, not safely assumed random; [Saveski et al., NeurIPS 2023](https://proceedings.neurips.cc/paper_files/paper/2023/file/b7d795e655c1463d7299688d489e8ef4-Paper-Conference.pdf) motivates conservative treatment of missing evaluations. Assignment research also explicitly considers the worst-served submission rather than only average quality: [PeerReview4All, JMLR 2021](https://jmlr.org/papers/v22/20-190.html). These motivate our safeguards; neither paper proves our proposed certifier or queue fair.

### 2. Private, independently replayable closure packet

Export canonical inputs, included/excluded IDs and reasons, score revisions, judge-identified pending slots, allowable criterion domains, duplicate and eligibility policy, ranking and tie semantics, adaptive grid/fallbacks/seed, engine source digest and result. Include the complete counterexample or the bound certificate, plus a documented local verification command when implemented.

The verifier must recompute with the scoring engine, not merely compare hashes. It must validate all pending-slot values and identities before replay. A witness fills every slot in its declared completion domain, not just the convenient one that helps the challenger.

Private scores and witness details stay organizer/admin scoped, including downloads, caches and error responses. A public projection may disclose a policy/input commitment and limitations only when release rules permit. **A public hash is not public verification of hidden arithmetic; a signature authenticates bytes, not their truth.** A fully independent bound verifier is a separate, testable deliverable, not a label to attach to the existing server verifier.

## Mathematical shipping boundary

### A. Raw weighted means: exact first-place bounds

For project p, let S be the sum of its observed per-review weighted scores on [0,100], n the number received and m the fixed number pending. If all independent criterion endpoints are admissible and weights are positive:

```text
L(p) = S / (n + m)
U(p) = (S + 100*m) / (n + m)
```

These endpoints are attainable by assigning every pending criterion its minimum or maximum. A zero denominator is unranked, not zero. Changes in score-dependent eligibility, clipping, trimming, optional completion or tie policy require their own analysis; do not silently apply these formulas to a different rule.

Compare bounds using the **actual** two-decimal shared-rank semantics. Neither project ID nor title ordering may silently turn a shared rank into a unique winner. V1 can certify a unique first-place winner when its worst rounded score strictly exceeds every challenger's best rounded score. Becoming tied counts as changing a unique-winner outcome. Initially return UNKNOWN for more complicated preservation of an existing multi-project tie.

Use exact rational reference calculations and conservative handling of rounding boundaries. If the production floating-point evaluator could cross a tie boundary and the implementation has no valid error bound, return UNKNOWN. A raw-only certificate must never be displayed as certifying a normalized result.

### B. Normalized scores: witnesses first, certified bounds later

For a fixed positive lambda, a fixed completed assignment graph and our additive objective:

```text
minimize sum_r (y_r - mu_project(r) - b_judge(r))^2
         + lambda * sum_j b_j^2
```

The fitted project scores are linear in the review scores, for the ranked projects with observations. With fixed judge identities, a leader-challenger difference has the form `D = c + a^T z`, where z holds pending scores. Its box extrema use the minimum or maximum admissible value according to each coefficient's sign. Endpoint-attainable criterion domains make those extrema feasible for that fixed graph and lambda.

This does **not** yet certify our production implementation. It uses iterative fitting with tolerance and an iteration cap. Exact-arithmetic solutions or validated residual/error bounds must bridge mathematical bounds to production output and its rounded ranks. Basis-probing floats without an error certificate is insufficient. Unreviewed projects, nonconvergence, overflow/nonfinite data, lambda zero and unmodelled post-processing return UNKNOWN/UNSUPPORTED as appropriate.

An assignment described as "any eligible judge" is not a fixed graph. No-shows, abstention/decline options, recusals and reassignments change the completion domain. They are unsupported in normalized v1; a changed roster invalidates the prior live certificate. A missing review is never converted into an observed zero score.

### C. Adaptive lambda is part of the rule

Our policy selects from `0.5, 1, 2, 5, 10, 20, 50, 100` using seeded five-fold CV, including its documented tie and no-prediction fallback. Checking only today's selected lambda is not a full-policy guarantee.

A validated stability bound for **every** grid member and every applicable fallback is sufficient to cover whichever member the final CV selects. This may be conservative. Failure at one grid member does not show that the actual adaptive rule can reverse: the extreme score scenario might select a different member. It must be replayed through the full adaptive procedure before calling it a counterexample.

V1 normalized behavior should therefore be: bounded counterexample search with exact replay, otherwise UNKNOWN. Add PROVEN STABLE only after the all-policy bound and numerical-validation tests pass. Do not change the official scoring rule merely to obtain a stronger-looking certificate.

### Explicit non-goals for v1

- Bradley-Terry/pairwise closure, track awards and interacting multi-prize allocation.
- A complete normalized "possible winners" set. "Not ruled out by our bounds" does not mean "witnessed possible"; different pairwise extremes need not be jointly realizable.
- Probabilities that a project is objectively best, or claims that normalization removes all bias.
- Automatic early stopping, judge reliability penalties, suspicion-based exclusions or an AI judge.

Possible/necessary winners under incomplete preferences are established research, not our invention: [Konczak and Lang, 2005](https://www.lamsade.dauphine.fr/~lang/papers/konlan05a.pdf). General incomplete-preference problems can be computationally difficult: [Pini et al., IJCAI 2007](https://www.ijcai.org/Proceedings/07/Papers/236.pdf). Our narrow cardinal-score design needs its own proof and tests; those papers are conceptual foundations, not a correctness certificate for this code.

## Competitor research: what is and is not distinctive

Public README claims checked September 27. This is not an implementation audit and not an exhaustive competition survey. No competitor code was copied.

| Project | Advertised strengths relevant to this decision |
|---|---|
| [Fairground](https://github.com/keirsalterego/fairground) | Calibration, immutable result runs, audit, pairwise cross-check and portability. |
| [Manak](https://github.com/ewwhardik/manak) | Confidence diagnostics, active-learning pairwise selection, audit chains and offline signed-record verification. |
| [Glassbox repository](https://github.com/hatif03/dogfood-prehac-setup) | Broad tier coverage, judging progress/outliers, signed records and portable archives. |
| [Podium](https://github.com/Nikhils-G/podium) | Broad lifecycle coverage, pairwise judging, normalization, webhooks and signed records. |

A universal remaining-review completion certificate was not explicitly advertised in these four READMEs. That is the extent of the novelty finding. Do not say "world first", "nobody thought of this" or that competitors lack an unadvertised implementation.

Consequently, another confidence chart, audit-chain badge, basic pairwise screen or signed participation certificate is useful product completeness but not our headline innovation. The differentiation here is the **specific organizer decision + admissible witness + explicit unknown state + private replay**, integrated without compromising blind judging.

## Proof obligations before calling it shipped

1. **Exhaustive oracle:** tiny instances with 3-5 projects, 2-4 judges, 1-3 pending slots and a 2-3-level integer rubric. Enumerate all completions through the production policy. Any false STABLE or non-replaying witness fails the gate.
2. **Policy traps:** CV-selection changes, no-prediction fallback, exact rounded ties, already-tied leaders, no observations, excluded duplicates and a reversing grid member that CV never selects.
3. **Domain validation:** unknown roster versus explicitly empty roster; wrong track, wrong event, conflict, repeated judge/project, missing criterion and out-of-range witness values are refused.
4. **Snapshot races:** edits, exclusions, eligibility changes, new slots and reassignment during analysis cannot attach a stale result to current data. Capture one consistent snapshot; recheck its digest before persistence/release.
5. **No side effects:** analysis cannot write a review, reopen the historical fixture, bypass publication guards or suppress another participant's review entitlement.
6. **Privacy:** peer judge, participant, other-event organizer and anonymous requests cannot read the card or packet. Public pages and logs reveal no private witness.
7. **Numerical soundness:** rational reference cases, iteration-cap/nonconvergence cases and outward error bounds. Resource timeout returns UNKNOWN, never a certificate.
8. **Replay tampering:** changing any bound input, policy parameter, review revision, slot identity or reported outcome is detected. Hash equality alone is not sufficient.
9. **Performance evidence:** measure wall time, memory and work count on the real 40-project fixture and declared larger synthetic cases. No invented latency target or "instant" claim. Run bounded analysis explicitly, not on every page refresh.
10. **Deployment evidence:** UI and documented API agree, all artifacts verify locally without cloud services, and the unchanged organizer runner plus current regression/deployment gates pass.

## Delivery decision and demonstration

Implementation order is a correctness dependency, not an excuse to avoid the ambitious feature:

1. Finish the current repair integration, schema/API documentation, migration and fresh Compose/offline gates. Existing pairwise/audit work is still pending final integration; old acceptance output cannot certify the current tree.
2. Add the consistent analysis snapshot, declared roster, private UI/API and replayable normalized counterexample search. Ship UNKNOWN honestly when no witness is found.
3. Add the raw unique-winner bound with exhaustive tests. Keep it explicitly tied to raw-mode events.
4. Add rigorous normalized stability only if validated numeric bounds and the whole adaptive policy are covered. Otherwise retain the useful witness-first release.
5. Add the private export/verifier and connect the predeclared blind completion workflow. No new platform-wide roles or ranking policy are needed for v1.

Demo segment, inside the required full event lifecycle:

- Open the actual fixture ranking: Iron Switch leads. Show the uneven coverage and state that the fixture supplies no pending roster.
- Enter the labelled one-slot scenario above. Replay it: Still Beacon becomes first. Show exactly which assumption made the scenario admissible, not an invented real assignment.
- Open a separate, clearly synthetic raw-mode example: leader has three scores of 90; challenger has two scores of 60 and one required review left. Challenger's maximum is 73.33, so the leader is stable under that declared domain. These are constructed numbers, not organizer fixture results.
- Change a bound input and demonstrate stale-certificate rejection; try viewing the private packet as a judge and show refusal.
- Finish through the normal close/publish/verify path. The diagnostic does not override it.

If the certificate cannot pass its falsification tests, keep the scenario explorer and remove the proof claim. A reliable counterexample is useful; an unreliable green badge is not.

## Research and organizer-file status

All five published downloads were checked against our saved copies. Current downloadable dates confirm **September 29, 2026 at 18:00 UTC** as the freeze. The checker, fixture and example config are unchanged; the spec and context have date-text updates. See [organizer download audit](docs/ORGANIZER-DOWNLOAD-AUDIT.md) for exact links, hashes and access limits.

An actual Claude CLI Opus/high review independently stress-tested this proposal, not the implementation. Its useful objections are incorporated above: frozen judge identities, editable scores, numerical proof gaps, adaptive-policy replay, queue-order leakage and conservative product framing. Existing-model agreement is not test evidence.

The user subsequently supplied `fromchat.txt`, a saved Discord exchange in which organizer souvlakee permits labelled synthetic validation **provided the proof also runs on the real fixture**. This supports the existing two-part normalization proof; it does not make synthetic truths the fixture's true ranking. The same exchange permits local static assets conditional on offline operation. This is a user-provided transcript, not a newly accessed Discord session.
