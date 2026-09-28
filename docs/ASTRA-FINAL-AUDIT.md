# Astra final audit

Audit started 2026-09-28 on branch `astra-final`, baseline `43f341e`.
Scope: this worktree only; isolated app database `verdict_w4app` and gate database
`verdict_w4`. No Docker startup, push, or access to the sibling worktree.
Evidence below is recorded as it is collected; pending checks are not passes.

## 1. Scorecard

| Official criterion | Score (0-5) | Evidence | Highest-value remaining change |
| --- | --- | --- | --- |
| Tier completion and correctness (40%) | Pending | Judge's path pending | Pending |
| Judging integrity (25%) | Pending | Integrity diff audit pending | Pending |
| Adoptability and operability (20%) | Pending | Startup and documentation audit pending | Pending |
| Code quality and innovation (15%) | Pending | Focused tests and full gate pending | Pending |

## 2. Findings

| Severity | Area | File:line | Reproduction | Status / exact next step |
| --- | --- | --- | --- | --- |
| Medium | Reviewer documentation | `README.md:126` | README says there is no historical roster snapshot and strict schema has 306 errors; task and newer migrations claim both were repaired | Verify live behavior, then correct stale present-tense claims |
| Low | Evidence navigation | `docs/BUILD-SPEC.md:248` | Referenced `docs/TIER-EVIDENCE.md` is absent in this build | Use existing tier evaluation and record new HTTP evidence; incoming evidence worker is outside scope |
| High | Verify policy consistency | `src/results/services.py:1329` | Change a publication's separate `params` or `method` without changing canonical inputs; public policy reads those columns but Verify compared only canonical inputs | Fixed: `FinalPublicationTests.test_verify_rejects_policy_columns_that_disagree_with_hashed_inputs`; red 3 failures, green 11 tests including policy suite; fix commit recorded below |
| High | Verify robustness | `src/results/services.py:968` | Stored `inputs.excluded = [null]` passed input validation and later called `row.get` | Fixed: `FinalPublicationTests.test_verify_malformed_exclusions_fail_closed_without_a_500`; red 3 errors/1 failure; validated excluded-review shape |
| Medium | Verify current state | `src/results/services.py:1233` | Keep a publication instance, change event coverage policy, verify the old instance; cached `pub.event` hid the update | Fixed: `FinalPublicationTests.test_verify_refreshes_cached_event_policy`; red false unchanged verdict; live projection now reads current Event under the shared lock |
| Medium | Certificate verification | `src/interop/views.py:84` | Verify a valid winner code after a later version replaces that award: response contains only `valid` and `kind` | Add version/supersession regression and return authenticated certificate status |
| Medium | Uncertainty claims | `docs/UNCERTAINTY.md:6` | Calls Monte Carlo uncertainty exact; treats 95% order threshold as an exact interval equivalence; does not account for estimated offsets in df | Correct wording; reproduce fixture numbers with a named command |
| Medium | Uncertainty determinism | `src/results/engine.py:1938` | `scripts/uncertainty_evidence.py` prints `reversed_input_identical: false` on the real fixture | Add regression and sort review input before the initial fit |
| Medium | Consequence dialog lifecycle | `src/static/js/consequences.js:169` | Delay the preview response, dismiss the modal, then release it: callbacks dereference cleared `state`; reopening another action lets an old response update new state | Open: capture a request generation/state identity and ignore stale completions; verify with a real browser |

## 3. Statistics verdict

The prescribed quantile indices (10, 189), `df = n - P`, strict unrounded replicate
ranks, half-credit exact ties and fixed-lambda seeded reruns match the code. The
noise divisor accounts for project means but not the fitted/shrunk judge offsets;
it is not an unbiased residual variance correction. A 95% order-share cutoff is
a descriptive stability rule, not an exact finite-sample interval equivalence or
proof of equal quality. Groups chain adjacent pairs, not all-pairs equivalence.
The fixture command reproduces sigma 15.431882, df 81, first-place share 0.22,
rank interval [1,16], adjacent order share 0.465 and one group of 40 in 0.227 s
(Python 3.11.9 on this host). It also exposes input-order dependence in unrounded
floating-point outputs. Wording and determinism repairs are queued after this audit.

## 4. Submission-day risks

Pending evidence. Other workers' in-flight changes and the sibling worktree are
outside this audit; this report does not certify their integration.

## 5. Verified strengths

- Fresh migrations and bootstrap succeeded on `verdict_w4app`, using local Python
  3.11 and the unchanged pinned requirements.
- `.venv/Scripts/python.exe run.py .data/astra/dogfood.toml`: **7/7 PASS**, verified
  T1/T2. `.venv/Scripts/python.exe scripts/verify_tiers.py .data/astra/dogfood.toml`:
  **25/25 PASS**. Both printed totals inspected, not just exit codes.
- Initial attacks: 14/14 direct HTTP, 27/28 probe refusals with the explicit
  `WEBHOOKS_ALLOW_PRIVATE=1` local-receiver test setting. The sole difference was
  expected loopback acceptance; rerun with the production default before sign-off.
- Rerun with `WEBHOOKS_ALLOW_PRIVATE=0`: **28/28 attacks refused and 14/14 direct
  HTTP checks passed**. No 500/traceback in either server log.
- `scripts/astra_final_http.py .data/astra/dogfood.toml`: **191/191 checks passed**.
  Four seeded-role session logins plus anonymous pages; organizer pages; disposable
  weighted-rubric event through two judge submissions, close, stale/fresh publish,
  historical replay; private/public certificate access and genuine/bad codes;
  Ed25519 verification/tamper/revocation; embed HTTP; export/import review counts;
  real local webhook delivery with exact-byte HMAC; all three voting access modes,
  duplicates, hidden/open tallies, quadratic budgets, ballot IDOR, escaped comments
  and moderation. Repeated rehearsal also hit the five-comments/ten-minute 429.
  Harness uses fresh commenters to avoid that intentional cross-event throttle.
  HTTP/template evidence does not certify visual layout or JavaScript interaction.

## Execution record

1. Report skeleton: complete.
2. Judge's path: in progress. Read README and existing evidence; local virtualenv
   created because the task's shared-interpreter path conflicts with the explicit
   instruction never to touch the sibling worktree. Python 3.11; same pinned
   requirements, no added dependencies.
3. Integrity diff audit: reviewed `c190d12..43f341e`, focusing on publication,
   consequences, certificates, locking, authorization and engine changes. Writes
   share Event-first locking even without `expected_digest` (`_lock_project`
   locks Event). Omitting that digest is intentionally compatible per F1, not an
   authorization bypass. Cross-event/role access is covered by the probe and
   existing consequences tests. Read-only Verify currently lacks a coherent
   locked current-event view; regression queued. A consequence read can also
   straddle commits; the write digest guards execution, but a fully coherent
   display snapshot is not yet guaranteed.
4. Statistician audit: completed source/document comparison; proof regeneration
   running. Correct overclaims in the generator as well as its generated report.
5. Red-before-green fixes: fix 1 complete (Verify policy copies); command
   `.venv/Scripts/python.exe manage.py test tests.test_astra_final tests.test_publication_policy -v 1 --noinput`
   on `verdict_w4`: 11 tests, OK. Commit `ece782f`.
   Fix 2: malformed exclusions, regression plus `tests.test_adversarial_t2`:
   18 tests, OK; commit `0a7ac4a`.
   Fix 3: cached event policy, plus publication and consequences regression suites.

The original normalization proof regenerated byte-for-byte with no diff. Its
numbers are reproduced, not freshly invented. A second regeneration incorporates
wording corrections in the generator; numerical methods are unchanged.
6. Full gate, strict schema validation, final report and commit: pending.
