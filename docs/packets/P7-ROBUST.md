# Packet P7-ROBUST: winner robustness certificate (engine, pure)

In `src/results/engine.py` add `robustness(reviews_scored, criteria/params, lam_rule, top_k=3) -> Robustness` and include it in `evaluate`:
- **Leave-one-judge-out:** for each judge with included reviews, refit without all their reviews (same λ procedure as the official result, but reuse the λ already chosen for speed and say so) and record the new winner and top-k. Report: number of removals where 1st place holds, top-k set stability, the judges whose removal changes the winner.
- **Leave-one-review-out:** same per review (121 refits on the fixture; warm start), report how many single-review removals change the winner.
- **Flip margin:** smallest number of individual reviews of the winner that, if each moved to the rubric midpoint, would drop it from 1st place (greedy over the winner's reviews sorted by influence; report the value and which reviews); cap at 5 and report "> 5" beyond.
- Plain-language summary strings, e.g. "1st place holds in 29 of 30 single-judge removals; changing 2 reviews could flip it."
- Deterministic; runtime on the fixture < 5 s.
Proof: add a "Robustness of the fixture result" section to scripts/normalization_proof.py output (for the auto-λ ranking).
Tests (tests/test_engine.py): a dominant winner is robust (holds in all removals); a tie-edge winner flips when its most favourable judge is removed; flip margin counts correctly on a hand-built case; determinism.
Files: src/results/engine.py, scripts/normalization_proof.py, docs/NORMALIZATION-PROOF.md, tests/test_engine.py.
