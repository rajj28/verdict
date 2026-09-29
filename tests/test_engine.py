"""Packet P2-EN engine tests: pure stdlib unittest, no Django settings."""

import json
import math
import os
import random
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from results.engine import (  # noqa: E402
    DEFAULT_LAMBDA_GRID,
    FLIP_CAP,
    BudgetCurve,
    BudgetRow,
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
    _nearest_rank_interval,
    offset_variance,
    outliers,
    permutation_test,
    pooled_residual_sd,
    rank,
    rank_agreement,
    rank_uncertainty,
    raw_scores,
    review_budget_curve,
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
        # its reviews to the midpoint leaves it ahead in this named scenario.
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
        self.assertEqual(r.flip_status, "no_change_possible")
        self.assertEqual(r.flip_evaluations, 7)
        self.assertIn("No first-place change", r.flip_summary)
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


class SelectLambdaNoPredictionTests(unittest.TestCase):
    def test_single_judge_returns_largest_lambda_with_note(self):
        s = score_reviews(
            [scored("J", "A", 50), scored("J", "B", 60)], CRIT_0_100
        )
        choice = select_lambda(s)
        self.assertEqual(choice.value, max(DEFAULT_LAMBDA_GRID))
        self.assertEqual(choice.n, 0)
        self.assertEqual(
            choice.note,
            "cross-validation not possible; most conservative lambda used",
        )
        for v in list(choice.cv_rmse.values()) + [choice.baseline_rmse]:
            self.assertTrue(math.isfinite(v))
        # Valid strict JSON (NaN/Infinity would fail with allow_nan=False).
        json.dumps(
            {
                "value": choice.value,
                "cv_rmse": choice.cv_rmse,
                "baseline_rmse": choice.baseline_rmse,
                "note": choice.note,
            },
            allow_nan=False,
        )

    def test_all_held_out_skipped_returns_note(self):
        # Every review carries a unique judge and project, so each
        # held-out review lacks training data on both sides.
        s = score_reviews(
            [
                scored("J1", "P1", 50),
                scored("J2", "P2", 60),
                scored("J3", "P3", 70),
            ],
            CRIT_0_100,
        )
        choice = select_lambda(s)
        self.assertEqual(choice.n, 0)
        self.assertEqual(choice.value, max(DEFAULT_LAMBDA_GRID))
        self.assertEqual(
            choice.note,
            "cross-validation not possible; most conservative lambda used",
        )

    def test_normal_path_has_empty_note(self):
        s = score_reviews(
            [
                scored("A", f"P{i}", 60)
                for i in range(8)
            ]
            + [
                scored("B", f"P{i}", 40)
                for i in range(8)
            ],
            CRIT_0_100,
        )
        choice = select_lambda(s)
        self.assertGreater(choice.n, 0)
        self.assertEqual(choice.note, "")


class ReviewBudgetCurveTests(unittest.TestCase):
    def test_deterministic_and_shaped(self):
        first = review_budget_curve(
            6, 8, reviews_per_judge_grid=(3, 4), sigma_noise=11.0,
            reps=20, seed="verdict-test",
        )
        second = review_budget_curve(
            6, 8, reviews_per_judge_grid=(3, 4), sigma_noise=11.0,
            reps=20, seed="verdict-test",
        )
        self.assertIsInstance(first, BudgetCurve)
        self.assertEqual(first, second)
        self.assertEqual([r.reviews_per_judge for r in first.rows], [3, 4])
        for row in first.rows:
            self.assertIsInstance(row, BudgetRow)
            self.assertEqual(row.n_pairs, 6 * row.reviews_per_judge)
            self.assertTrue(math.isfinite(row.median_se))
            self.assertGreater(row.median_se, 0)
            self.assertEqual(sorted(row.power), [8.0, 12.0])
            for p in row.power.values():
                self.assertGreaterEqual(p, 0.0)
                self.assertLessEqual(p, 1.0)
        # Each judge reviews exactly k distinct projects; pairs unique.
        self.assertFalse(first.sigma_estimated)

    def test_each_judge_gets_k_distinct_projects(self):
        curve = review_budget_curve(
            6, 8, reviews_per_judge_grid=(4,), sigma_noise=11.0,
            reps=5, seed="verdict-test",
        )
        self.assertEqual(curve.rows[0].n_pairs, 24)

    def test_explicit_noise_is_separate_from_residual_dispersion(self):
        reviews = [
            scored("A", f"P{i}", 60) for i in range(4)
        ] + [
            scored("B", f"P{i}", 40) for i in range(4)
        ]
        s = score_reviews(reviews, CRIT_0_100)
        curve = review_budget_curve(
            4, 4, reviews_per_judge_grid=(2,), sigma_noise=15.0,
            scored=s, reps=5, seed="verdict-test",
        )
        self.assertFalse(curve.sigma_estimated)
        self.assertEqual(curve.sigma_noise, 15.0)
        self.assertTrue(curve.experimental)
        self.assertEqual(curve.noise_source, "explicit_simulation_assumption")
        self.assertAlmostEqual(
            curve.residual_dispersion, pooled_residual_sd(s), delta=1e-12
        )
        with self.assertRaises(ValueError):
            review_budget_curve(
                4, 4, reviews_per_judge_grid=(2,), sigma_noise=None,
                scored=s, reps=5, seed="verdict-test",
            )

    def test_reviews_needed_logic(self):
        curve = review_budget_curve(
            6, 8, reviews_per_judge_grid=(3, 4), sigma_noise=11.0,
            reps=20, seed="verdict-test",
        )
        needed = curve.reviews_needed(power=0.8)
        self.assertTrue(needed in (3, 4) or needed == "not reached on tested grid")
        # Hand-made curve: threshold logic without simulation noise.
        made = BudgetCurve(
            n_judges=2, n_projects=2, grid=(3, 4), bias=8.0,
            biases=(8.0, 12.0), sigma_noise=10.0, sigma_estimated=False,
            reps=1, seed="x", lam=2.0,
            rows=(
                BudgetRow(3, 4.0, {8.0: 0.5, 12.0: 0.9}, 6),
                BudgetRow(4, 3.0, {8.0: 0.85, 12.0: 0.95}, 8),
            ),
        )
        self.assertEqual(made.reviews_needed(power=0.8), 4)
        self.assertEqual(made.reviews_needed(power=0.9), "not reached on tested grid")

    def test_bad_input_raises(self):
        with self.assertRaises(ValueError):
            review_budget_curve(
                0, 8, reviews_per_judge_grid=(3,), sigma_noise=1.0)
        with self.assertRaises(ValueError):
            review_budget_curve(
                6, 0, reviews_per_judge_grid=(3,), sigma_noise=1.0)
        with self.assertRaises(ValueError):
            review_budget_curve(
                6, 8, reviews_per_judge_grid=(), sigma_noise=1.0)
        with self.assertRaises(ValueError):
            review_budget_curve(
                6, 8, reviews_per_judge_grid=(3,), sigma_noise=-1.0)
        with self.assertRaises(ValueError):
            review_budget_curve(
                6, 8, reviews_per_judge_grid=(3,), sigma_noise=1.0, reps=0)


class LivePairwiseEngineTests(unittest.TestCase):
    def test_live_official_ranking_preserves_derived_crosscheck(self):
        data = [scored("J", "A", 90), scored("J", "B", 60)]
        result = evaluate(data, CRIT_0_100, method="pairwise",
                          comparisons=[Comparison("B", "A")])
        self.assertEqual(result.rank["B"], "1")
        self.assertEqual(result.rank_live, result.rank)
        self.assertEqual(result.rank_bt["A"], "1")
        self.assertGreater(result.live_strengths["B"], result.live_strengths["A"])
        self.assertGreater(result.strengths["A"], result.strengths["B"])
        self.assertFalse(result.robustness.available)

    def test_explicit_empty_live_source_never_substitutes_derived_votes(self):
        data = [scored("J", "A", 90), scored("J", "B", 60)]
        empty = evaluate(data, CRIT_0_100, method="pairwise", comparisons=[])
        self.assertEqual(empty.rank, {"A": "unranked", "B": "unranked"})
        self.assertEqual(empty.live_strengths, {"A": None, "B": None})
        self.assertEqual(empty.pairwise_components, [])
        legacy = evaluate(data, CRIT_0_100, method="pairwise")
        self.assertEqual(legacy.rank, legacy.rank_bt)
        self.assertIsNone(legacy.live_strengths)

    def test_live_components_exclude_unplayed_projects_and_prior_edges(self):
        result = evaluate([], [], method="pairwise", projects=["A", "B", "C", "D", "E"],
                          comparisons=[Comparison("B", "A"), Comparison("D", "C")])
        self.assertEqual(result.pairwise_components, [["A", "B"], ["C", "D"]])
        self.assertEqual(result.rank_live["E"], "unranked")
        self.assertIsNone(result.live_strengths["E"])
        self.assertEqual(result.strengths, dict.fromkeys(["A", "B", "C", "D", "E"]))

    def test_live_outcomes_do_not_change_official_rubric_ranking(self):
        data = [scored("J", "A", 90), scored("J", "B", 60)]
        result = evaluate(data, CRIT_0_100, method="raw", comparisons=[Comparison("B", "A")])
        self.assertEqual(result.rank["A"], "1")
        self.assertEqual(result.rank_live["B"], "1")
        self.assertEqual(result.robustness.winners, ("A",))


class RankUncertaintyTests(unittest.TestCase):
    def _noisy_pair_design(self):
        # X and Y get identical reviews; Z sits far below with jitter so
        # the fit is non-degenerate (sigma > 0) while X/Y stay symmetric.
        reviews = []
        for ji, j in enumerate(("J1", "J2", "J3", "J4")):
            reviews.append(scored(j, "X", 50.0))
            reviews.append(scored(j, "Y", 50.0))
            reviews.append(scored(j, "Z", (10.0, 12.0, 8.0, 10.0)[ji]))
        return score_reviews(reviews, CRIT_0_100)

    def test_determinism_and_seed_sensitivity(self):
        s = self._noisy_pair_design()
        first = rank_uncertainty(s, 2.0)
        self.assertEqual(first, rank_uncertainty(s, 2.0))
        # Draws run in review_id order, so input order must not matter.
        self.assertEqual(first, rank_uncertainty(list(reversed(s)), 2.0))

        def shares(u):
            return tuple(
                (
                    p,
                    u.projects[p].p_first,
                    u.projects[p].p_top,
                    u.projects[p].p_above_next,
                )
                for p in sorted(u.projects)
            )

        base = shares(first)
        self.assertTrue(
            any(
                shares(rank_uncertainty(s, 2.0, seed=sd)) != base
                for sd in (1, 2, 3)
            )
        )

    def test_well_separated_projects_are_firm(self):
        # True levels 2/5/8 with tiny noise: gaps dwarf the residual SD.
        levels = {"P_LO": 2.0, "P_MID": 5.0, "P_HI": 8.0}
        reviews = []
        for ji, j in enumerate(("J1", "J2", "J3", "J4")):
            for pi, p in enumerate(sorted(levels)):
                reviews.append(
                    scored(j, p, levels[p] + ((ji + pi) % 2 * 0.1 - 0.05))
                )
        u = rank_uncertainty(score_reviews(reviews, CRIT_0_100), 2.0)
        self.assertTrue(u.available)
        self.assertEqual(u.order, ("P_HI", "P_MID", "P_LO"))
        for p, pos in (("P_HI", 1), ("P_MID", 2), ("P_LO", 3)):
            self.assertEqual(
                (u.projects[p].rank_low, u.projects[p].rank_high), (pos, pos)
            )
        self.assertEqual(u.projects["P_HI"].p_above_next, 1.0)
        self.assertEqual(u.projects["P_MID"].p_above_next, 1.0)
        self.assertIsNone(u.projects["P_LO"].p_above_next)
        self.assertEqual(u.tied_pairs, ())
        self.assertIn("clear", u.summary)

    def test_identical_pair_is_tied(self):
        u = rank_uncertainty(self._noisy_pair_design(), 2.0)
        self.assertEqual(u.order, ("X", "Y", "Z"))
        held = u.projects["X"].p_above_next
        self.assertGreaterEqual(held, 0.3)
        self.assertLessEqual(held, 0.7)
        self.assertIn(("X", "Y"), u.tied_pairs)

    def test_degenerate_sigma_zero(self):
        # Constant scores per project fit exactly: no residual noise.
        reviews = []
        for j in ("J1", "J2"):
            for p, v in (("A", 60.0), ("B", 60.0), ("C", 40.0)):
                reviews.append(scored(j, p, v))
        u = rank_uncertainty(score_reviews(reviews, CRIT_0_100), 2.0)
        self.assertEqual(u.sigma, 0.0)
        self.assertEqual(u.order, ("A", "B", "C"))
        for p in ("A", "B", "C"):
            proj = u.projects[p]
            self.assertEqual(proj.rank_low, proj.rank_high)
            self.assertEqual(proj.score_low, proj.score_high)
        self.assertEqual(u.projects["A"].p_above_next, 0.5)
        self.assertEqual(u.projects["B"].p_above_next, 1.0)

    def test_too_few_reviews_unavailable(self):
        s = score_reviews(
            [scored("J1", "A", 50.0), scored("J2", "B", 60.0)], CRIT_0_100
        )
        u = rank_uncertainty(s, 2.0)
        self.assertFalse(u.available)
        self.assertTrue(u.reason)
        single = score_reviews(
            [scored("J1", "A", 50.0), scored("J2", "A", 60.0)], CRIT_0_100
        )
        solo = rank_uncertainty(single, 2.0)
        self.assertFalse(solo.available)
        self.assertTrue(solo.reason)

    def test_nearest_rank_interval_indices(self):
        self.assertEqual(
            _nearest_rank_interval(list(range(200)), 0.9), (10, 189)
        )
        self.assertEqual(_nearest_rank_interval([3, 1, 2], 0.9), (1, 3))

    def test_fixture_uncertainty_is_exactly_independent_of_review_order(self):
        path = os.path.join(os.path.dirname(__file__), "..", "fixtures.json")
        with open(path, encoding="utf-8") as handle:
            fixture = json.load(handle)
        reviews = [
            ReviewInput(f"{row['judge']}__{row['project']}", row["judge"],
                        row["project"], dict(row["criteria"]))
            for row in fixture["scores"] if row["project"] != "prj_07"
        ]
        scored_reviews = score_reviews(reviews, CRIT_1_5)
        original = rank_uncertainty(scored_reviews, 100.0)
        self.assertEqual(original, rank_uncertainty(list(reversed(scored_reviews)), 100.0))
        shuffled = list(scored_reviews)
        random.Random(142).shuffle(shuffled)
        self.assertEqual(original, rank_uncertainty(shuffled, 100.0))

    def test_fixture_uncertainty(self):
        # Organizers' fixture, scored like the proof: prj_07 excluded,
        # equal-weight 1-5 rubric, official lambda 100.
        path = os.path.join(os.path.dirname(__file__), "..", "fixtures.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        reviews = [
            ReviewInput(
                f"{s['judge']}__{s['project']}",
                s["judge"],
                s["project"],
                dict(s["criteria"]),
            )
            for s in data["scores"]
            if s["project"] != "prj_07"
        ]
        s = score_reviews(reviews, CRIT_1_5)
        start = time.perf_counter()
        u = rank_uncertainty(s, 100.0)
        elapsed = time.perf_counter() - start
        self.assertTrue(u.available)
        # Groups partition the projects ...
        flat = [p for g in u.groups for p in g]
        self.assertEqual(sorted(flat), sorted(u.order))
        self.assertEqual(len(flat), len(u.order))
        # ... into maximal runs joined by tied pairs.
        rebuilt = [[u.order[0]]]
        tied = set(u.tied_pairs)
        for i in range(len(u.order) - 1):
            if (u.order[i], u.order[i + 1]) in tied:
                rebuilt[-1].append(u.order[i + 1])
            else:
                rebuilt.append([u.order[i + 1]])
        self.assertEqual([tuple(g) for g in rebuilt], list(u.groups))
        inside = 0
        for i, p in enumerate(u.order):
            proj = u.projects[p]
            for share in (proj.p_first, proj.p_top):
                self.assertGreaterEqual(share, 0.0)
                self.assertLessEqual(share, 1.0)
            if proj.p_above_next is not None:
                self.assertGreaterEqual(proj.p_above_next, 0.0)
                self.assertLessEqual(proj.p_above_next, 1.0)
            self.assertLessEqual(proj.rank_low, proj.rank_high)
            if proj.rank_low <= i + 1 <= proj.rank_high:
                inside += 1
        self.assertIsNone(u.projects[u.order[-1]].p_above_next)
        self.assertGreaterEqual(inside / len(u.order), 0.9)
        self.assertLess(elapsed, 10.0)


if __name__ == "__main__":
    unittest.main()


class InstallIndependenceTests(unittest.TestCase):
    """Review ids are generated per install; results must not depend on them."""

    def _reviews(self, rename):
        from results import engine as E
        rng = random.Random(11)
        reviews = []
        for j in range(8):
            for p in range(12):
                if (j + p) % 3 == 0:
                    continue
                values = {"q": max(1, min(5, round(3 + (p % 5) * 0.4 + (j % 3 - 1) * 0.6 + rng.gauss(0, .7))))}
                reviews.append(E.ReviewInput(rename(f"jdg_{j:02d}", f"prj_{p:02d}"), f"jdg_{j:02d}", f"prj_{p:02d}", values))
        return E.score_reviews(reviews, [E.Criterion("q", 1.0, 1, 5)])

    def test_uncertainty_lambda_and_robustness_ignore_review_ids(self):
        from results import engine as E
        ids = random.Random(3)
        first = self._reviews(lambda j, p: f"{j}__{p}")
        second = self._reviews(lambda j, p: "rev_" + "".join(ids.choice("abcdefgh234567") for _ in range(10)))
        self.assertEqual(E.select_lambda(first).value, E.select_lambda(second).value)
        a, b = E.rank_uncertainty(first, 2.0), E.rank_uncertainty(second, 2.0)
        self.assertEqual(a.summary, b.summary)
        self.assertEqual({k: v.p_first for k, v in a.projects.items()},
                         {k: v.p_first for k, v in b.projects.items()})
        self.assertEqual(E.fit_additive(first, 2.0).mu, E.fit_additive(second, 2.0).mu)
