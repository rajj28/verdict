"""Packet P2-EN engine tests: pure stdlib unittest, no Django settings."""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from results.engine import (  # noqa: E402
    DEFAULT_LAMBDA_GRID,
    FLIP_CAP,
    Comparison,
    Criterion,
    EstimabilityJudge,
    EstimabilitySummary,
    Fit,
    LambdaChoice,
    ReviewInput,
    Robustness,
    ScoredReview,
    bradley_terry,
    bradley_terry_raw,
    components,
    derived_comparisons,
    diagnostics,
    estimability,
    evaluate,
    explain,
    fit_additive,
    judge_table,
    kendall_tau,
    leave_one_out,
    offset_variance,
    outliers,
    permutation_test,
    rank,
    rank_agreement,
    raw_scores,
    review_score,
    robustness,
    score_reviews,
    select_lambda,
    spearman,
    spread,
)

CRIT_1_5 = [
    Criterion("functionality", 1.0, 1, 5),
    Criterion("quality", 1.0, 1, 5),
    Criterion("innovation", 1.0, 1, 5),
]
CRIT_0_100 = [Criterion("s", 1.0, 0, 100)]


def scored(judge, project, value, rid=None):
    """Single-criterion 0-100 review (value used directly as the score)."""
    return ReviewInput(rid or f"{judge}:{project}", judge, project, {"s": value})


class ReviewScoreTests(unittest.TestCase):
    def test_endpoints(self):
        lo = {c.key: c.min_score for c in CRIT_1_5}
        hi = {c.key: c.max_score for c in CRIT_1_5}
        self.assertEqual(review_score(lo, CRIT_1_5), 0.0)
        self.assertEqual(review_score(hi, CRIT_1_5), 100.0)

    def test_weights(self):
        crit = [Criterion("a", 2.0, 0, 10), Criterion("b", 1.0, 0, 10)]
        # a at midpoint (0.5), b at max (1.0): 100*(2*0.5+1*1)/3
        self.assertAlmostEqual(review_score({"a": 5, "b": 10}, crit), 200 / 3)

    def test_bad_input_raises(self):
        with self.assertRaises(KeyError):
            review_score({"quality": 3}, CRIT_1_5)
        with self.assertRaises(ValueError):
            review_score(
                {"functionality": 6, "quality": 3, "innovation": 3}, CRIT_1_5
            )
        with self.assertRaises(ValueError):
            review_score({"s": 1}, [])


def complete_design():
    """3 judges x 4 projects, noiseless: s = mu_p + b_j, sum b = 0.

    Closed form: project means are mu, judge means minus grand mean are b.
    """
    mu = {"A": 70, "B": 60, "C": 50, "D": 40}
    b = {"j1": 6, "j2": -2, "j3": -4}
    reviews = [
        scored(j, p, mu[p] + b[j]) for j in sorted(b) for p in sorted(mu)
    ]
    return reviews, mu, b


class AdditiveFitTests(unittest.TestCase):
    def test_closed_form(self):
        reviews, mu, b = complete_design()
        fit = fit_additive(score_reviews(reviews, CRIT_0_100), 0.0)
        self.assertTrue(fit.converged)
        for p, v in mu.items():
            self.assertAlmostEqual(fit.mu[p], v, delta=1e-6)
        for j, v in b.items():
            self.assertAlmostEqual(fit.offset[j], v, delta=1e-6)

    def test_shift_invariance_lambda0(self):
        reviews, _, _ = complete_design()
        base = evaluate(reviews, CRIT_0_100, lam=0.0)
        shifted = [
            scored(r.judge_id, r.project_id, r.values["s"] + (10 if r.judge_id == "j1" else 0))
            for r in reviews
        ]
        moved = evaluate(shifted, CRIT_0_100, lam=0.0)
        self.assertEqual(moved.rank_norm, base.rank_norm)
        # lam=0 recentring (sum n_j b_j = 0) turns a single-judge shift
        # into a uniform level shift: every mu moves by the same constant.
        gaps = {
            round(moved.normalized[p] - base.normalized[p], 9)
            for p in base.normalized
        }
        self.assertEqual(len(gaps), 1)

    def test_lambda_shrinks_single_review_judge_more(self):
        reviews = [
            scored("J1", "P1", 50),
            scored("J1", "P2", 52),
            scored("J1", "P3", 48),
            scored("J1", "P4", 50),
            scored("J2", "P1", 48),
            scored("J2", "P2", 50),
            scored("J2", "P3", 52),
            scored("J2", "P4", 50),
            scored("S", "P1", 90),  # single-review judge
            scored("M", "P1", 50),
            scored("M", "P2", 50),
            scored("M", "P3", 50),
            scored("M", "P4", 50),
            scored("M", "P5", 50),  # five-review judge
        ]
        s = score_reviews(reviews, CRIT_0_100)
        unshrunk = fit_additive(s, 0.0)
        shrunk = fit_additive(s, 2.0)
        ratio_s = abs(shrunk.offset["S"]) / abs(unshrunk.offset["S"])
        ratio_m = abs(shrunk.offset["M"]) / abs(unshrunk.offset["M"])
        self.assertGreater(abs(unshrunk.offset["S"]), 0)
        self.assertGreater(abs(unshrunk.offset["M"]), 0)
        self.assertLess(ratio_s, ratio_m)

    def test_constant_judge_finite(self):
        reviews = [
            scored("J1", "A", 60),
            scored("J1", "B", 40),
            scored("J2", "A", 70),
            scored("J2", "B", 50),
            scored("K", "A", 75),
            scored("K", "B", 75),
            scored("K", "C", 75),
        ]
        fit = fit_additive(score_reviews(reviews, CRIT_0_100), 2.0)
        for v in list(fit.mu.values()) + list(fit.offset.values()):
            self.assertTrue(math.isfinite(v))

    def test_excluding_constant_judge_leaves_others_unchanged(self):
        # K reviews only C and D (no shared reviewers): decoupled subgraph,
        # so dropping K cannot move A/B at all.
        reviews = [
            scored("J1", "A", 60),
            scored("J1", "B", 40),
            scored("J2", "A", 70),
            scored("J2", "B", 50),
            scored("K", "C", 75),
            scored("K", "D", 75),
        ]
        full = fit_additive(score_reviews(reviews, CRIT_0_100), 2.0)
        kept = fit_additive(
            score_reviews([r for r in reviews if r.judge_id != "K"], CRIT_0_100),
            2.0,
        )
        self.assertEqual(full.mu["A"], kept.mu["A"])
        self.assertEqual(full.mu["B"], kept.mu["B"])


class GraphTests(unittest.TestCase):
    def test_two_components(self):
        reviews = [
            scored("J1", "A", 60),
            scored("J1", "B", 50),
            scored("J2", "C", 60),
            scored("J2", "D", 50),
        ]
        comps = components(reviews)
        self.assertEqual(len(comps), 2)

    def test_single_component(self):
        reviews, _, _ = complete_design()
        self.assertEqual(len(components(reviews)), 1)


class RankTests(unittest.TestCase):
    def test_ties_share_rank(self):
        out = rank({"a": 90.001, "b": 90.004, "c": 80.0})
        self.assertEqual(out["a"], "=1")
        self.assertEqual(out["b"], "=1")
        self.assertEqual(out["c"], "3")

    def test_order_and_skip(self):
        out = rank({"a": 10.0, "b": 30.0, "c": 20.0})
        self.assertEqual(out, {"a": "3", "b": "1", "c": "2"})


class BradleyTerryTests(unittest.TestCase):
    def test_recovers_strict_order_and_undefeated_finite(self):
        comps = [
            Comparison("A", "B"),
            Comparison("A", "B"),
            Comparison("B", "C"),
            Comparison("A", "C"),
        ]
        strengths = bradley_terry(comps, ["A", "B", "C", "D"])
        self.assertIsNone(strengths["D"])  # no comparisons: unranked
        for item in ("A", "B", "C"):
            self.assertTrue(math.isfinite(strengths[item]))
        self.assertGreater(strengths["A"], strengths["B"])
        self.assertGreater(strengths["B"], strengths["C"])

    def test_derived_comparisons_cancel_pure_offset(self):
        # Harsh H and generous G agree within-judge that Y beats X,
        # 75 points apart in level: derived pairs ignore the level gap.
        reviews = [
            scored("H", "X", 0),
            scored("H", "Y", 25),
            scored("G", "X", 75),
            scored("G", "Y", 100),
        ]
        s = score_reviews(reviews, CRIT_0_100)
        fit = fit_additive(s, 0.0)  # unshrunk: the level gap is exactly 75
        self.assertGreater(fit.offset["G"] - fit.offset["H"], 50)
        strengths = bradley_terry(derived_comparisons(s), ["X", "Y"])
        self.assertGreater(strengths["Y"], strengths["X"])

    def test_ties_split_half_each_way(self):
        reviews = [scored("J", "X", 50, "r1"), scored("J", "Y", 50, "r2")]
        comps = derived_comparisons(score_reviews(reviews, CRIT_0_100))
        self.assertEqual(len(comps), 2)
        self.assertAlmostEqual(sum(c.weight for c in comps), 1.0)

    def test_raw_fixed_point_satisfies_mm_equations(self):
        # The plain MM iteration (no in-loop rescaling) converges to the
        # exact MAP: raw strengths satisfy s_i * denom_i == W_i to 1e-9.
        comps = [
            Comparison("A", "B"),
            Comparison("A", "B"),
            Comparison("B", "C"),
            Comparison("A", "C"),
            Comparison("C", "B"),
        ]
        raw = bradley_terry_raw(comps, ["A", "B", "C"])
        wins: dict[str, float] = {"A": 0.0, "B": 0.0, "C": 0.0}
        games: dict[tuple[str, str], float] = {}
        for c in comps:
            wins[c.winner] += c.weight
            key = tuple(sorted((c.winner, c.loser)))
            games[key] = games.get(key, 0.0) + c.weight
        for i in ("A", "B", "C"):
            denom = 2.0 / (raw[i] + 1.0)
            for (a, b), n in games.items():
                o = b if a == i else (a if b == i else None)
                if o is not None:
                    denom += n / (raw[i] + raw[o])
            self.assertAlmostEqual(raw[i] * denom, wins[i] + 1.0, delta=1e-9)

    def test_log_output_normalizes_raw(self):
        comps = [Comparison("A", "B"), Comparison("B", "C")]
        raw = bradley_terry_raw(comps, ["A", "B", "C"])
        out = bradley_terry(comps, ["A", "B", "C"])
        total = sum(raw.values())
        for i in ("A", "B", "C"):
            self.assertAlmostEqual(out[i], math.log(raw[i] / total))


class DiagnosticsTests(unittest.TestCase):
    def test_flags(self):
        reviews = [
            ReviewInput("r1", "solo", "A", {"s": 60}),
            ReviewInput("r2", "K", "A", {"s": 75}),
            ReviewInput("r3", "K", "B", {"s": 75}),
            ReviewInput("r4", "J1", "B", {"s": 40}),
            ReviewInput("r5", "J1", "C", {"s": 50}),
        ]
        diag = diagnostics(reviews, target=2, all_projects=["A", "B", "C", "Z"])
        self.assertIn("Z", diag.under_reviewed)  # zero reviews
        self.assertIn("C", diag.under_reviewed)  # 1 < 2
        self.assertEqual(diag.single_review_judges, ["solo"])
        self.assertEqual(diag.constant_scorers, ["K"])
        self.assertEqual(diag.n_components, 1)


class SpreadOutlierTests(unittest.TestCase):
    def test_spread_shrinks_on_offset_only_data(self):
        reviews = []
        for j, b in (("H", 20), ("M", 0), ("L", -20)):
            for i, p in enumerate(("A", "B", "C")):
                reviews.append(scored(j, p, 60 + b + (i - 1)))
        s = score_reviews(reviews, CRIT_0_100)
        sp = spread(s, fit_additive(s, 2.0))
        self.assertGreater(sp.before, 10.0)
        self.assertLess(sp.after, sp.before)

    def test_injected_outlier_flagged(self):
        reviews = [
            scored("J1", "X", 50),
            scored("J1", "Y", 50),
            scored("J1", "V1", 50),
            scored("J2", "X", 50),
            scored("J2", "Z", 50),
            scored("J2", "V2", 50),
            scored("J3", "X", 50),
            scored("J3", "W", 50),
            scored("J3", "V3", 50),
            scored("J4", "X", 90),  # +40 above the rest
            scored("J4", "Y", 50),
            scored("J4", "V4", 50),
        ]
        s = score_reviews(reviews, CRIT_0_100)
        flagged = outliers(s, fit_additive(s, 2.0))
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].judge_id, "J4")
        self.assertEqual(flagged[0].project_id, "X")
        self.assertIn("consensus", flagged[0].sentence)


class EvaluateTests(unittest.TestCase):
    def test_determinism(self):
        reviews, _, _ = complete_design()
        first = evaluate(reviews, CRIT_0_100, lam=2.0, target=3)
        second = evaluate(reviews, CRIT_0_100, lam=2.0, target=3)
        self.assertEqual(first.normalized, second.normalized)
        self.assertEqual(first.fit.offset, second.fit.offset)
        self.assertEqual(first.rank, second.rank)
        self.assertEqual(first.strengths, second.strengths)

    def test_bundles_everything(self):
        reviews, _, _ = complete_design()
        res = evaluate(reviews, CRIT_0_100, lam=2.0, target=2)
        self.assertEqual(set(res.raw), {"A", "B", "C", "D"})
        self.assertEqual(res.raw["A"][1], 3)
        self.assertEqual(
            raw_scores(reviews, CRIT_0_100)["A"][1], res.raw["A"][1]
        )
        self.assertEqual(set(res.judges), {"j1", "j2", "j3"})
        row = res.judges["j1"]
        self.assertEqual(row.n, 4)
        # lam=2 shrinks j1's +6 offset below the +/-5 label cut; unshrunk:
        unshrunk = judge_table(
            score_reviews(reviews, CRIT_0_100), fit_additive(res.scored, 0.0)
        )
        self.assertEqual(unshrunk["j1"].label, "generous")
        self.assertEqual(unshrunk["j2"].label, "typical")
        # Label boundaries read straight off a hand-made fit.
        edge = judge_table(
            score_reviews(reviews[:1], CRIT_0_100),
            Fit(mu={"A": 70.0}, offset={"j1": -5.0}),
        )
        self.assertEqual(edge["j1"].label, "typical")
        edge = judge_table(
            score_reviews(reviews[:1], CRIT_0_100),
            Fit(mu={"A": 70.0}, offset={"j1": -5.01}),
        )
        self.assertEqual(edge["j1"].label, "harsh")
        table = judge_table(score_reviews(reviews, CRIT_0_100), res.fit)
        self.assertEqual(table["j1"], row)
        exp = explain("A", res.scored, res.fit)
        self.assertEqual(res.explanations["A"], exp)
        self.assertEqual(len(exp), 3)
        self.assertAlmostEqual(exp[0].adjusted, exp[0].score - exp[0].offset)
        self.assertGreaterEqual(res.spread_before, res.spread_after)
        self.assertEqual(res.rank, res.rank_norm)
        self.assertEqual(res.method, "normalized")
        with self.assertRaises(ValueError):
            evaluate(reviews, CRIT_0_100, method="zscore")


class LeaveOneOutTests(unittest.TestCase):
    def test_additive_beats_project_mean_on_offset_only_data(self):
        # Noiseless s = mu_p + b_j on a complete design: the additive fit
        # predicts a held-out review almost exactly, the project mean errs
        # by the held-out judge's offset.
        mu = {"A": 70, "B": 60, "C": 50, "D": 40}
        b = {"j1": 8, "j2": -3, "j3": -5}
        reviews = [
            scored(j, p, mu[p] + b[j]) for j in sorted(b) for p in sorted(mu)
        ]
        s = score_reviews(reviews, CRIT_0_100)
        report = leave_one_out(s, (0.0, 2.0))
        self.assertEqual(report.skipped, 0)
        self.assertEqual(report.mean_only.n, len(s))
        self.assertGreater(report.mean_only.rmse, 1.0)
        self.assertLess(report.additive[0.0].rmse, 1e-6)
        self.assertLess(report.additive[0.0].rmse, report.mean_only.rmse)
        self.assertLess(report.additive[2.0].rmse, report.mean_only.rmse)

    def test_skips_reviews_that_carry_their_only_level_signal(self):
        # Holding out "solo"'s only review leaves that judge with no data;
        # holding out J1->B leaves project B with no mate. Only J1->A is
        # predictable (project mate via "solo", judge kept via B).
        reviews = [
            scored("solo", "A", 60),
            scored("J1", "A", 50),
            scored("J1", "B", 40),
        ]
        report = leave_one_out(score_reviews(reviews, CRIT_0_100), (2.0,))
        self.assertEqual(report.skipped, 2)
        self.assertEqual(report.mean_only.n, 1)


class PermutationTests(unittest.TestCase):
    def _two_judge_design(self, bump):
        # Both judges review the same 8 projects; `bump` is judge A's offset.
        reviews = []
        for i in range(8):
            reviews.append(scored("A", f"P{i}", 50 + bump))
            reviews.append(scored("B", f"P{i}", 50 - bump))
        groups = {f"P{i}": "trk" for i in range(8)}
        return score_reviews(reviews, CRIT_0_100), groups

    def test_small_p_value_when_offsets_injected(self):
        s, groups = self._two_judge_design(10)
        self.assertGreater(offset_variance(s, 2.0), 1.0)
        res = permutation_test(s, groups, n_perm=200, seed=97531, lam=2.0)
        self.assertEqual(res.n_perm, 200)
        self.assertLess(res.p_value, 0.05)

    def test_large_p_value_without_judge_effects(self):
        s, groups = self._two_judge_design(0)
        res = permutation_test(s, groups, n_perm=200, seed=97531, lam=2.0)
        self.assertEqual(res.observed, 0.0)
        self.assertGreater(res.p_value, 0.5)


class SelectLambdaTests(unittest.TestCase):
    def _two_judge_design(self, bump):
        # Both judges review the same 8 projects; `bump` is judge A's offset.
        reviews = []
        for i in range(8):
            reviews.append(scored("A", f"P{i}", 50 + bump))
            reviews.append(scored("B", f"P{i}", 50 - bump))
        return score_reviews(reviews, CRIT_0_100)

    def test_grid_default(self):
        self.assertEqual(
            tuple(DEFAULT_LAMBDA_GRID),
            (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0),
        )

    def test_deterministic_and_order_invariant(self):
        s = self._two_judge_design(10)
        first = select_lambda(s)
        second = select_lambda(list(reversed(s)))
        self.assertEqual(first.value, second.value)
        self.assertEqual(first.cv_rmse, second.cv_rmse)
        self.assertEqual(first.baseline_rmse, second.baseline_rmse)
        self.assertEqual(first.folds, 5)
        self.assertIsInstance(first, LambdaChoice)

    def test_picks_large_lambda_without_judge_effects(self):
        # No judge offsets: every review of a project is identical, so all
        # lambdas (and the baseline) predict perfectly; the tie rule then
        # picks the largest, most conservative grid value.
        s = self._two_judge_design(0)
        choice = select_lambda(s)
        self.assertEqual(choice.value, max(DEFAULT_LAMBDA_GRID))
        self.assertLessEqual(choice.cv_rmse[choice.value], choice.baseline_rmse)

    def test_picks_small_lambda_with_strong_bias(self):
        # Strong systematic offsets: the near-unshrunk fit predicts held-out
        # reviews far better than heavy shrinkage or the project mean.
        s = self._two_judge_design(10)
        choice = select_lambda(s)
        self.assertEqual(choice.value, min(DEFAULT_LAMBDA_GRID))
        self.assertLess(
            choice.cv_rmse[choice.value], choice.cv_rmse[max(DEFAULT_LAMBDA_GRID)]
        )
        self.assertLess(choice.cv_rmse[choice.value], choice.baseline_rmse)

    def test_ties_go_to_larger_lambda(self):
        s = [
            ScoredReview(f"r{i:03d}", "J0" if i % 2 == 0 else "J1", f"P{i % 3}", 50.0)
            for i in range(12)
        ]
        choice = select_lambda(s)
        self.assertEqual(choice.value, max(DEFAULT_LAMBDA_GRID))

    def test_warm_start_matches_cold_fit(self):
        s = self._two_judge_design(10)
        cold = fit_additive(s, 2.0)
        warm = fit_additive(s, 2.0, init=fit_additive(s, 5.0))
        for p in cold.mu:
            self.assertAlmostEqual(warm.mu[p], cold.mu[p], delta=1e-6)
        for j in cold.offset:
            self.assertAlmostEqual(warm.offset[j], cold.offset[j], delta=1e-6)

    def test_evaluate_auto_reports_choice(self):
        reviews = []
        for i in range(8):
            reviews.append(scored("A", f"P{i}", 60))
            reviews.append(scored("B", f"P{i}", 40))
        res = evaluate(reviews, CRIT_0_100, lam="auto")
        self.assertIsNotNone(res.lambda_choice)
        self.assertEqual(res.lam, res.lambda_choice.value)
        self.assertIn(res.lam, tuple(float(g) for g in DEFAULT_LAMBDA_GRID))
        self.assertEqual(
            res.lambda_choice.cv_rmse,
            select_lambda(score_reviews(reviews, CRIT_0_100)).cv_rmse,
        )
        # A fixed lambda reports no choice.
        fixed = evaluate(reviews, CRIT_0_100, lam=2.0)
        self.assertIsNone(fixed.lambda_choice)
        self.assertEqual(fixed.lam, 2.0)
        with self.assertRaises(ValueError):
            evaluate(reviews, CRIT_0_100, lam="2")


class AgreementTests(unittest.TestCase):
    def test_identical_rankings_agree_perfectly(self):
        norm = {"a": 3.0, "b": 2.0, "c": 1.0}
        strengths = {"a": 30.0, "b": 20.0, "c": 10.0}
        agr = rank_agreement(norm, strengths)
        self.assertEqual(agr.n, 3)
        self.assertAlmostEqual(agr.rho, 1.0)
        self.assertAlmostEqual(agr.tau, 1.0)
        self.assertEqual(agr.movers, ())

    def test_reversed_rankings_disagree(self):
        agr = rank_agreement(
            {"a": 3.0, "b": 2.0, "c": 1.0}, {"a": 10.0, "b": 20.0, "c": 30.0}
        )
        self.assertAlmostEqual(agr.rho, -1.0)
        self.assertAlmostEqual(agr.tau, -1.0)

    def test_stats_helpers_reject_bad_input(self):
        with self.assertRaises(ValueError):
            spearman([1.0], [1.0, 2.0])
        with self.assertRaises(ValueError):
            kendall_tau([], [])


class RobustnessTests(unittest.TestCase):
    def test_dominant_winner_is_robust(self):
        # W leads by 60 points on a complete 3x3 design: no single judge
        # or review removal can touch it, and even moving all three of
        # its reviews to the midpoint leaves it ahead (margin beyond cap).
        reviews = [
            scored(j, p, v)
            for j in ("J1", "J2", "J3")
            for p, v in (("W", 100), ("M", 40), ("L", 30))
        ]
        r = robustness(reviews, CRIT_0_100, 2.0)
        self.assertIsInstance(r, Robustness)
        self.assertEqual(r.winner, "W")
        self.assertEqual(r.top_k, ("W", "M", "L"))
        self.assertEqual(r.n_judges, 3)
        self.assertEqual(r.judge_holds, 3)
        self.assertEqual(r.judges_flip, ())
        self.assertEqual(r.topk_holds, 3)
        self.assertEqual(r.n_reviews, 9)
        self.assertEqual(r.review_holds, 9)
        self.assertEqual(r.reviews_flip, ())
        self.assertIsNone(r.flip_margin)
        self.assertEqual(r.flip_reviews, ())
        self.assertIn("> 5", r.flip_summary)
        self.assertIn("holds in 3 of 3 single-judge removals", r.judge_summary)
        self.assertIn("holds in 9 of 9 single-review removals", r.review_summary)

    def test_tie_edge_winner_flips_without_best_judge(self):
        # A only leads because generous G scores it 90; without G, B wins.
        reviews = [
            scored("G", "A", 90),
            scored("G", "B", 50),
            scored("H", "A", 55),
            scored("H", "B", 60),
        ]
        r = robustness(reviews, CRIT_0_100, 2.0)
        self.assertEqual(r.winner, "A")
        self.assertEqual(r.judges_flip, ("G",))
        self.assertEqual(r.judge_winner, {"G": "B", "H": "A"})
        self.assertEqual(r.judge_holds, 1)
        self.assertIn("without G 1st goes to B", r.judge_summary)

    def test_flip_margin_counts(self):
        # W (100, 100, 70, 70) vs R (70 x 4): moving the single best
        # review to the midpoint is not enough, moving the best two is.
        reviews = [
            scored("J1", "W", 100),
            scored("J2", "W", 100),
            scored("J3", "W", 70),
            scored("J4", "W", 70),
        ] + [scored(j, "R", 70) for j in ("J1", "J2", "J3", "J4")]
        r = robustness(reviews, CRIT_0_100, 2.0)
        self.assertEqual(r.winner, "W")
        self.assertEqual(r.flip_margin, 2)
        self.assertEqual(r.flip_reviews, ("J1:W", "J2:W"))
        self.assertIn("reviews: J1:W, J2:W", r.flip_summary)
        # The margin is real: verify both prefixes directly against the fit.
        s = score_reviews(reviews, CRIT_0_100)

        def winner_with_moved(*rids):
            moved = set(rids)
            altered = [
                ScoredReview(
                    x.review_id, x.judge_id, x.project_id,
                    50.0 if x.review_id in moved else x.score,
                )
                for x in s
            ]
            mu = fit_additive(altered, 2.0).mu
            return min(mu, key=lambda p: (-mu[p], p))

        self.assertEqual(winner_with_moved("J1:W"), "W")
        self.assertEqual(winner_with_moved("J1:W", "J2:W"), "R")

    def test_deterministic_and_included_in_evaluate(self):
        reviews, _, _ = complete_design()
        first = robustness(reviews, CRIT_0_100, 2.0)
        # Reversed input order must give the identical certificate.
        second = robustness(list(reversed(reviews)), CRIT_0_100, 2.0)
        self.assertEqual(first, second)
        # Already-scored input (no criteria) agrees on the winner/summary.
        third = robustness(score_reviews(reviews, CRIT_0_100), None, 2.0)
        self.assertEqual(third.winner, first.winner)
        self.assertEqual(third.summary, first.summary)
        # evaluate bundles the certificate at the official lambda.
        res = evaluate(reviews, CRIT_0_100, lam=2.0)
        self.assertIsNotNone(res.robustness)
        self.assertEqual(res.robustness.winner, "A")
        self.assertEqual(res.robustness.lam, 2.0)
        # Empty input is vacuous but well-formed.
        empty = robustness([], None, 2.0)
        self.assertIsNone(empty.winner)
        self.assertIsNone(empty.flip_margin)
        # Bad inputs fail loudly.
        with self.assertRaises(ValueError):
            robustness(reviews, CRIT_0_100, 2.0, top_k=0)
        with self.assertRaises(ValueError):
            robustness(reviews, None, 2.0)
        self.assertEqual(FLIP_CAP, 5)


class EstimabilityTests(unittest.TestCase):
    # 6 judges x 8 projects, 3 reviews per project (fixed synthetic design).
    BASE = [
        ("J0", "P1"), ("J0", "P2"), ("J0", "P3"), ("J0", "P4"),
        ("J0", "P5"), ("J0", "P6"), ("J0", "P7"),
        ("J1", "P0"), ("J1", "P3"), ("J1", "P5"),
        ("J2", "P0"), ("J2", "P2"),
        ("J3", "P0"), ("J3", "P4"), ("J3", "P5"), ("J3", "P6"),
        ("J4", "P1"), ("J4", "P2"), ("J4", "P3"), ("J4", "P6"),
        ("J4", "P7"),
        ("J5", "P1"), ("J5", "P4"), ("J5", "P7"),
    ]

    def anchored(self):
        judges = sorted({j for j, _ in self.BASE})
        return sorted(set(self.BASE) | {(j, "PA") for j in judges})

    def test_deterministic(self):
        first = estimability(self.BASE, reps=50, seed="cal-test")
        second = estimability(self.BASE, reps=50, seed="cal-test")
        self.assertIsInstance(first, EstimabilitySummary)
        self.assertEqual(first, second)
        self.assertEqual(first.n_pairs, len(self.BASE))
        self.assertEqual(
            sorted(first.judges), ["J0", "J1", "J2", "J3", "J4", "J5"]
        )
        row = first.judges["J0"]
        self.assertIsInstance(row, EstimabilityJudge)
        self.assertEqual(row.n, 7)
        self.assertEqual(sorted(row.power), [4.0, 8.0, 12.0])

    def test_se_shrinks_as_overlap_grows(self):
        # Adding a shared anchor project (every judge reviews PA) grows
        # judge overlap; the expected offset SE must shrink.
        base = estimability(self.BASE, reps=200, seed="cal-test")
        anchored = estimability(self.anchored(), reps=200, seed="cal-test")
        self.assertLess(anchored.median_se, base.median_se)

    def test_power_increases_with_anchors(self):
        # The same added overlap must raise the mean power to detect an
        # 8-point judge bias.
        base = estimability(self.BASE, reps=200, seed="cal-test")
        anchored = estimability(self.anchored(), reps=200, seed="cal-test")
        self.assertGreater(anchored.power_at_8, base.power_at_8)
        self.assertEqual(anchored.power_bias, 8.0)

    def test_bad_input_raises(self):
        with self.assertRaises(ValueError):
            estimability(self.BASE, reps=0)
        with self.assertRaises(ValueError):
            estimability(self.BASE, bias_grid=())
        with self.assertRaises(ValueError):
            estimability(self.BASE, bias_grid=(0.0,))
        with self.assertRaises(ValueError):
            estimability(self.BASE, sigma_noise=-1.0)
        with self.assertRaises(ValueError):
            estimability(self.BASE, lam=-1.0)

    def test_empty_design(self):
        res = estimability([], reps=10, seed="cal-test")
        self.assertEqual(res.judges, {})
        self.assertEqual(res.n_pairs, 0)
        self.assertTrue(math.isnan(res.median_se))
        self.assertTrue(math.isnan(res.power_at_8))


if __name__ == "__main__":
    unittest.main()
