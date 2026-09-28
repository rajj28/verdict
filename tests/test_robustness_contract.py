"""Regression contracts for official-method sensitivity and planner assumptions."""

from dataclasses import asdict
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from results import engine as E

CRITERIA = [E.Criterion("score", 1.0, 0, 100)]


def reviews(*rows):
    return [E.ReviewInput(f"{j}:{p}", j, p, {"score": v}) for j, p, v in rows]


class OfficialRobustnessTests(TestCase):
    def test_raw_counterexample_follows_official_winner(self):
        data = reviews(("j1", "A", 73), ("j1", "B", 60), ("j2", "B", 90))
        raw = E.evaluate(data, CRITERIA, method="raw")
        norm = E.evaluate(data, CRITERIA, method="normalized")
        self.assertEqual(raw.rank["B"], "1")
        self.assertEqual(raw.robustness.winners, ("B",))
        self.assertEqual(norm.robustness.winners, ("A",))
        self.assertEqual(raw.robustness.method, "raw")
        for judge, winners in raw.robustness.judge_winners.items():
            remaining = [r for r in data if r.judge_id != judge]
            ranking = E.evaluate(remaining, CRITERIA, method="raw").rank
            self.assertEqual(winners, tuple(sorted(p for p, r in ranking.items() if r == "1")))

    def test_equal_and_rounded_ties_have_no_unique_winner(self):
        for values in ((75, 75), (75.001, 75.004)):
            for method in ("raw", "normalized"):
                with self.subTest(values=values, method=method):
                    data = [E.ScoredReview("rA", "j1", "A", values[0]),
                            E.ScoredReview("rB", "j1", "B", values[1])]
                    cert = E.robustness(data, None, 2, method=method, top_k=1)
                    self.assertIsNone(cert.winner)
                    self.assertEqual(cert.winners, ("A", "B"))
                    self.assertEqual(cert.top_k, ("A", "B"))
                    self.assertEqual(cert.flip_status, "not_applicable")
                    self.assertIn("no unique winner", cert.summary)

    def test_removal_that_creates_tie_changes_first_place_set(self):
        data = reviews(("j1", "A", 80), ("j1", "B", 70),
                       ("j2", "A", 70), ("j2", "B", 70))
        cert = E.robustness(data, CRITERIA, 2, method="raw")
        self.assertEqual(cert.judge_winners["j1"], ("A", "B"))
        self.assertIsNone(cert.judge_winner["j1"])
        self.assertIn("j1", cert.judges_flip)

    def test_connected_midpoint_counterexample_finds_non_greedy_singleton(self):
        data = reviews(("j1", "A", 76), ("j1", "B", 68), ("j1", "C", 79),
                       ("j2", "A", 78), ("j2", "C", 51), ("j3", "C", 86),
                       ("j4", "A", 62), ("j4", "C", 22))
        cert = E.robustness(data, CRITERIA, 2)
        self.assertEqual(cert.winner, "A")
        self.assertEqual(cert.flip_margin, 1)
        self.assertEqual(cert.flip_reviews, ("j1:A",))
        self.assertEqual(cert.flip_status, "found")
        altered = [E.ScoredReview(r.review_id, r.judge_id, r.project_id,
                                 50 if r.review_id == "j1:A" else r.score)
                   for r in E.score_reviews(data, CRITERIA)]
        self.assertNotEqual(E.robustness(altered, None, 2).winners, ("A",))

    def test_search_cap_is_unknown_not_claimed_minimum(self):
        data = reviews(*[(f"j{i}", p, score) for i in range(8)
                         for p, score in (("A", 100), ("B", 10))])
        cert = E.robustness(data, CRITERIA, 2, flip_max_evaluations=2)
        self.assertEqual(cert.flip_evaluations, 2)
        self.assertEqual(cert.flip_searched_through, 0)
        self.assertEqual(cert.flip_status, "search_capped")
        self.assertIsNone(cert.flip_margin)
        self.assertIn("unknown", cert.flip_summary)
        self.assertNotIn("flip margin >", cert.flip_summary)

    def test_size_cap_states_completed_search_depth(self):
        data = reviews(*[(f"j{i}", p, score) for i in range(3)
                         for p, score in (("A", 100), ("B", 10))])
        cert = E.robustness(data, CRITERIA, 2, flip_cap=1)
        self.assertEqual(cert.flip_status, "size_capped")
        self.assertEqual(cert.flip_evaluations, 3)
        self.assertEqual(cert.flip_searched_through, 1)
        self.assertIn("unknown", cert.flip_summary)

    def test_pairwise_unavailable_in_engine_and_view(self):
        data = reviews(("j1", "A", 80), ("j1", "B", 70))
        cert = E.evaluate(data, CRITERIA, method="pairwise").robustness
        self.assertFalse(cert.available)
        self.assertEqual(cert.method, "pairwise")
        self.assertIsNone(cert.winner)
        from results.views import _certificate
        event = SimpleNamespace(ranking_method="pairwise")
        value = _certificate(event, {"method": "pairwise", "lam": None}, {"included": []})
        self.assertFalse(value["available"])
        self.assertIn("unavailable", value["summary"])

    def test_view_recomputes_old_or_wrong_method_certificate(self):
        from results.views import _certificate
        data = reviews(("j1", "A", 73), ("j1", "B", 60), ("j2", "B", 90))
        included = [{"review_id": r.review_id, "judge_id": r.judge_id,
                     "project_id": r.project_id, "criteria": r.values} for r in data]
        stale = asdict(E.robustness(data, CRITERIA, 2))
        with patch("results.views.judging_policy.engine_criteria", return_value=CRITERIA):
            value = _certificate(SimpleNamespace(ranking_method="raw"),
                                 {"method": "raw", "lam": 2, "robustness": stale},
                                 {"included": included})
        self.assertEqual(value["winners"], ("B",))
        self.assertEqual(value["method"], "raw")

    def test_normalized_refits_disclose_fixed_lambda(self):
        data = reviews(("j1", "A", 80), ("j1", "B", 70))
        cert = E.robustness(data, CRITERIA, 100)
        self.assertIn("lambda = 100", cert.assumption)
        self.assertIn("not rerun", cert.assumption)

    def test_zero_lambda_removal_uses_official_component_convention(self):
        data = reviews(("j1", "A", 60), ("j1", "B", 80),
                       ("j2", "A", 70), ("j3", "B", 75))
        cert = E.robustness(data, CRITERIA, 0)
        for judge, winners in cert.judge_winners.items():
            remaining = E.score_reviews([r for r in data if r.judge_id != judge], CRITERIA)
            ranks = E.rank(E.fit_additive(remaining, 0).mu)
            expected = tuple(sorted(p for p, r in ranks.items() if r.lstrip("=") == "1"))
            self.assertEqual(winners, expected)


class PlannerAssumptionTests(TestCase):
    def test_more_assumed_noise_changes_conditional_detection(self):
        low = E.review_budget_curve(6, 8, (4,), sigma_noise=8, reps=50, seed="sensitivity")
        high = E.review_budget_curve(6, 8, (4,), sigma_noise=24, reps=50, seed="sensitivity")
        self.assertGreater(low.rows[0].power[8.0], high.rows[0].power[8.0])
        self.assertTrue(low.experimental)
        self.assertIn("track-agnostic", " ".join(low.assumptions))
        self.assertIn("Fixed lambda", " ".join(low.assumptions))

    def test_noise_is_required_even_when_residuals_are_available(self):
        data = [E.ScoredReview("r", "j", "p", 50)]
        with self.assertRaisesRegex(ValueError, "explicit"):
            E.review_budget_curve(2, 2, (1,), scored=data)

    def test_budget_cannot_exceed_number_of_projects(self):
        with self.assertRaisesRegex(ValueError, "distinct projects"):
            E.review_budget_curve(2, 2, (3,), sigma_noise=10)
