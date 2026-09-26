"""Packet P2-EN engine tests: pure stdlib unittest, no Django settings."""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from results.engine import (  # noqa: E402
    Comparison,
    Criterion,
    Fit,
    ReviewInput,
    ScoredReview,
    bradley_terry,
    components,
    derived_comparisons,
    diagnostics,
    evaluate,
    explain,
    fit_additive,
    judge_table,
    outliers,
    rank,
    raw_scores,
    review_score,
    score_reviews,
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


if __name__ == "__main__":
    unittest.main()
