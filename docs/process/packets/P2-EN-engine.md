# Packet P2-EN: judging engine (pure math) + normalization proof generator

**Runs at kickoff in parallel with P0a.** It needs no Django code: the engine is standard-library Python and the proof reads `fixtures.json` directly. Do not create or edit anything outside the files listed at the end (P0a is building the Django project at the same time). Run tests with `python -m unittest tests.test_engine -v` (pure `unittest.TestCase`, no Django settings; add `src/` to `sys.path` at the top of the test module).

Read AGENTS.md and BUILD-SPEC section 9 carefully. This is the heart of the "Judging Integrity" score and the Best Judging Engine prize: correctness, determinism and clear explanations matter more than anything else here.

## Do
1. `src/results/engine.py`: pure Python, standard library only, no Django imports. Plain dataclasses in/out:
   - `Criterion(key, weight, min_score, max_score)`, `ReviewInput(review_id, judge_id, project_id, values: dict[str,int])`, `Comparison(winner, loser, weight=1.0)`.
   - `review_score(values, criteria) -> float` (0–100, BUILD-SPEC 9).
   - `raw_scores(reviews, criteria) -> dict[project, (mean, n)]`.
   - `fit_additive(reviews_scored, lam) -> Fit(mu: dict, offset: dict, iterations, converged)` block coordinate descent exactly as specified, sorted-id iteration order, recentre when lam == 0.
   - `components(reviews) -> list[set]` of the judge–project graph.
   - `diagnostics(...)`: under-reviewed projects (target given), single-review judges, constant scorers (≥2 reviews with identical criterion values everywhere), component count.
   - `judge_table(...)`: n, mean given, offset, label (harsh < −5, generous > +5, typical), spread.
   - `explain(project)`: per review judge, score, offset, adjusted.
   - `rank(values: dict[project, float]) -> dict[project, str]` with shared ranks for ties at 2 dp ("=3").
   - `bradley_terry(comparisons, items) -> dict[item, float|None]` MM algorithm with the virtual-opponent prior (1 win + 1 loss each), log-strength output, None for items without comparisons; convergence 1e−10, cap 10 000.
   - `derived_comparisons(reviews_scored) -> list[Comparison]` (within-judge pairs; ties = 0.5 each way).
   - `evaluate(reviews, criteria, lam, target, method) -> Result` that bundles everything above for a preview.
2. `scripts/normalization_proof.py` (standalone, stdlib + `src/results/engine.py`): reads `fixtures.json`, applies the import rules from BUILD-SPEC 8 (duplicate prj_07 superseded by prj_41 and excluded; equal-weight rubric functionality/quality/innovation on 1–5), and writes `docs/NORMALIZATION-PROOF.md` deterministically (fixed seeds). The portal's results page uses the same engine, so numbers must match:
   - Fixture section: method in one paragraph; table of all ranked projects (raw rank, normalized rank, Δ, n reviews, raw and normalized scores); judge offset table (highlight jdg_07 constant scorer and jdg_01 single review); duplicate handling note; λ sensitivity (λ = 0, 1, 2, 5: top-5 and rank correlation vs λ = 2).
   - Simulation section (pure Python, `random.Random(seed)`): keep the fixture's exact judge↔project review design; draw true project quality ~ N(0,1), judge offsets ~ N(0, σ_b) with σ_b in {0.3, 0.6, 1.0} score points on the 1–5 scale, noise ~ N(0, 0.5), convert to integer 1–5 criteria by rounding and clipping; include one constant judge like jdg_07. 500 replications each. Compare raw mean, per-judge z-score (skip judges with zero variance and report how often that happens), additive λ=0, additive λ=2, derived Bradley–Terry. Report mean Spearman ρ with truth, top-5 recall, and how often the true best project is ranked first. Report whatever the numbers are; do not tune the text to a conclusion.
   - Properties section: shift invariance demonstration (add +1 to every score of one judge: raw ranking changes, additive λ=0 ranking unchanged) and the constant-judge case.
   - Limitations: offset-only model (no scale/nonlinear habits), few reviews per judge, clipping at scale ends, correlated or strategic bias not addressed.

## Tests
`tests/test_engine.py`: review_score endpoints and weights; additive fit on a hand-built 3-judge 4-project example matches a closed-form least-squares answer to 1e−6; shift invariance at λ=0 (exact ranking equality); λ>0 shrinks a single-review judge's offset more than a 5-review judge's; constant judge does not crash and yields finite values; two disconnected components reported; ties share rank; BT recovers a strict order from consistent comparisons and keeps an undefeated item finite; derived comparisons cancel a pure judge offset; determinism (same input twice → identical output).

## Files you own
src/results/engine.py (create `src/results/` only if missing, with an empty `__init__.py`; never touch other files there), scripts/normalization_proof.py, docs/NORMALIZATION-PROOF.md (generated), tests/test_engine.py (create `tests/__init__.py` only if missing).

## Winning items (BUILD-SPEC 17) in this packet
- `spread(...)`: σ of per-judge mean scores raw vs after offset removal; include in `evaluate` and in the proof.
- `outliers(...)`: residual r = s − μ_p − b_j; flag |r| > 2.5 × residual SD where the project has ≥ 3 reviews; include judge, project, residual, human sentence.
- Proof adds: rank-movement list (▲/▼), spread before/after, "the judge who marks everything the same" section (what the model does with jdg_07 and the rank changes if excluded), outlier list.
- Tests: spread shrinks on a synthetic offset-only dataset; an injected +40 review is flagged as an outlier; excluding a constant judge changes nothing for projects they did not review.
