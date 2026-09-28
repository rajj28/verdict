"""Packet: Closure scenario engine tests. Pure stdlib unittest."""

import copy
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from results import closure as C  # noqa: E402
from results.engine import Criterion, ReviewInput, evaluate  # noqa: E402
from scripts import normalization_proof as proof  # noqa: E402

CRIT_1_5 = [{"key": k, "weight": 1.0, "min_score": 1, "max_score": 5}
            for k in ("functionality", "quality", "innovation")]


def base_snapshot(**over):
    snap = {
        "version": 1, "event": "evt-test", "method": "normalized", "lam": "auto",
        "target": 3, "roster_declared": True, "roster_kind": "hypothetical",
        "criteria": CRIT_1_5, "projects": [], "reviews": [], "pending": [],
        "context": {"rubric_rev": 1},
    }
    snap.update(over)
    return snap


def review(rid, j, p, v):
    return {"review_id": rid, "judge_id": j, "project_id": p, "values": v}


def fixture_snapshot(**over):
    criteria, reviews, *_ = proof.load_fixture()
    projects = sorted({r.project_id for r in reviews})
    snap = base_snapshot(
        criteria=[{"key": c.key, "weight": c.weight,
                   "min_score": c.min_score, "max_score": c.max_score} for c in criteria],
        projects=projects,
        reviews=[review(r.review_id, r.judge_id, r.project_id, dict(r.values)) for r in reviews],
    )
    snap.update(over)
    return snap


class RealFixtureTests(unittest.TestCase):
    def test_labelled_hypothetical_slot_flips_leader(self):
        snap = fixture_snapshot(pending=[
            {"slot_id": "s1", "review_id": "jdg_20__prj_10", "judge_id": "jdg_20", "project_id": "prj_10"},
        ])
        out = C.analyze(snap, max_scenarios=12)
        self.assertEqual(out["status"], "counterexample_found")
        self.assertEqual(out["witness"]["winners"], ["prj_10"])
        self.assertEqual(out["witness"]["lam"], 100.0)
        self.assertEqual(out["witness"]["pending"][0]["values"],
                          {"functionality": 5, "quality": 5, "innovation": 5})

    def test_witness_replays_through_the_real_engine(self):
        snap = fixture_snapshot(pending=[
            {"slot_id": "s1", "review_id": "jdg_20__prj_10", "judge_id": "jdg_20", "project_id": "prj_10"},
        ])
        out = C.analyze(snap, max_scenarios=12)
        replayed = C.replay(snap, out["witness"])
        self.assertEqual(replayed["winners"], out["witness"]["winners"])
        self.assertEqual(replayed["lam"], out["witness"]["lam"])
        self.assertEqual(replayed["scores"], out["witness"]["scores"])


class NoWitnessTests(unittest.TestCase):
    def test_dominant_leader_has_no_bounded_counterexample(self):
        snap = base_snapshot(
            method="raw", projects=["A", "B"],
            reviews=[
                review("r1", "j1", "A", {"functionality": 5, "quality": 5, "innovation": 5}),
                review("r2", "j2", "A", {"functionality": 5, "quality": 5, "innovation": 5}),
                review("r3", "j1", "B", {"functionality": 1, "quality": 1, "innovation": 1}),
            ],
            pending=[{"slot_id": "s1", "review_id": "r4", "judge_id": "j2", "project_id": "B"}],
        )
        out = C.analyze(snap)
        self.assertEqual(out["status"], "unknown")
        self.assertEqual(out["reason"], "no_counterexample_in_budget")
        self.assertIsNone(out["witness"])


class RawTieTests(unittest.TestCase):
    def test_one_pending_review_creates_a_rounded_tie(self):
        crit = [{"key": "s", "weight": 1.0, "min_score": 0, "max_score": 100}]
        # A: single review of 80.00. B: single review of 60.00, one pending
        # review whose maximum (100) averages to exactly 80.00 with it.
        snap = base_snapshot(
            method="raw", criteria=crit, projects=["A", "B"],
            reviews=[
                review("rA", "j1", "A", {"s": 80}),
                review("rB", "j1", "B", {"s": 60}),
            ],
            pending=[{"slot_id": "s1", "review_id": "rB2", "judge_id": "j2", "project_id": "B"}],
        )
        baseline = C.analyze({**snap, "pending": []})
        self.assertEqual(baseline["baseline"]["winners"], ["A"])
        out = C.analyze(snap)
        self.assertEqual(out["status"], "counterexample_found")
        self.assertEqual(out["witness"]["winners"], ["A", "B"])  # rounded tie


class HardenedValidationTests(unittest.TestCase):
    """Malformed input must fail closed with ValueError, never crash raw."""

    def test_list_project_id_rejected_not_a_typeerror(self):
        snap = base_snapshot(projects=["A"], reviews=[
            review("r1", "j1", ["A"], {"functionality": 3, "quality": 3, "innovation": 3}),
        ])
        with self.assertRaises(ValueError):
            C.analyze(snap)

    def test_huge_weight_rejected_not_an_overflow_error(self):
        crit = [{"key": "s", "weight": 10 ** 400, "min_score": 0, "max_score": 100}]
        snap = base_snapshot(criteria=crit)
        with self.assertRaises(ValueError):
            C.analyze(snap)

    def test_huge_lambda_rejected_not_an_overflow_error(self):
        snap = base_snapshot(lam=10 ** 400)
        with self.assertRaises(ValueError):
            C.analyze(snap)

    def test_huge_criterion_endpoint_rejected(self):
        crit = [{"key": "s", "weight": 1.0, "min_score": 0, "max_score": 10 ** 400}]
        snap = base_snapshot(criteria=crit)
        with self.assertRaises(ValueError):
            C.analyze(snap)

    def test_non_object_context_rejected(self):
        snap = base_snapshot(context=["not", "an", "object"])
        with self.assertRaises(ValueError):
            C.analyze(snap)

    def test_dict_slot_id_in_witness_rejected_not_a_typeerror(self):
        snap = fixture_snapshot(pending=[
            {"slot_id": "s1", "review_id": "jdg_20__prj_10", "judge_id": "jdg_20", "project_id": "prj_10"},
        ])
        witness = {"pending": [
            {"slot_id": {"nested": True}, "review_id": "jdg_20__prj_10", "judge_id": "jdg_20",
             "project_id": "prj_10", "values": {"functionality": 5, "quality": 5, "innovation": 5}},
        ]}
        with self.assertRaises(ValueError):
            C.replay(snap, witness)

    def test_undeclared_roster_replay_rejected(self):
        snap = base_snapshot(
            roster_declared=False, projects=["A"],
            reviews=[review("r1", "j1", "A", {"functionality": 3, "quality": 3, "innovation": 3})],
            pending=[],
        )
        with self.assertRaises(ValueError):
            C.replay(snap, {"pending": []})

    def test_replay_with_two_pending_slots_fully_completed(self):
        snap = base_snapshot(
            method="raw", criteria=[{"key": "s", "weight": 1.0, "min_score": 0, "max_score": 100}],
            projects=["A", "B"],
            reviews=[review("rA", "j1", "A", {"s": 50})],
            pending=[
                {"slot_id": "s1", "review_id": "rB1", "judge_id": "j1", "project_id": "B"},
                {"slot_id": "s2", "review_id": "rB2", "judge_id": "j2", "project_id": "B"},
            ],
        )
        witness = {"pending": [
            {"slot_id": "s1", "review_id": "rB1", "judge_id": "j1", "project_id": "B", "values": {"s": 100}},
            {"slot_id": "s2", "review_id": "rB2", "judge_id": "j2", "project_id": "B", "values": {"s": 100}},
        ]}
        replayed = C.replay(snap, witness)
        self.assertEqual(replayed["winners"], ["B"])


class ExhaustiveOracleTests(unittest.TestCase):
    """Proof obligation 1: every found witness is one specific admissible
    completion, independently reproducible and consistent with a manual
    exhaustive sweep of the same tiny domain."""

    def test_found_witness_belongs_to_the_exhaustive_domain(self):
        crit_obj = [{"key": "s", "weight": 1.0, "min_score": 1, "max_score": 3}]
        criteria = [Criterion("s", 1.0, 1, 3)]
        base_reviews = [
            ReviewInput("rA", "j1", "A", {"s": 2}),
            ReviewInput("rB", "j1", "B", {"s": 1}),
        ]
        snap = base_snapshot(
            method="raw", criteria=crit_obj, projects=["A", "B"],
            reviews=[review(r.review_id, r.judge_id, r.project_id, dict(r.values)) for r in base_reviews],
            pending=[{"slot_id": "s1", "review_id": "rB2", "judge_id": "j2", "project_id": "B"}],
        )
        out = C.analyze(snap)
        self.assertEqual(out["status"], "counterexample_found")

        changes = set()
        for v in (1, 2, 3):
            candidate = base_reviews + [ReviewInput("rB2", "j2", "B", {"s": v})]
            res = evaluate(candidate, criteria, lam=2.0, target=3, method="raw", projects=["A", "B"])
            winners = frozenset(p for p, r in res.rank.items() if r.lstrip("=") == "1")
            if winners != frozenset({"A"}):
                changes.add(v)
        self.assertTrue(changes, "exhaustive sweep must also find a flip for this to be a valid test")
        witness_value = out["witness"]["pending"][0]["values"]["s"]
        self.assertIn(witness_value, changes)
        replayed = C.replay(snap, out["witness"])
        self.assertEqual(replayed["winners"], out["witness"]["winners"])


class DomainValidationTests(unittest.TestCase):
    def test_pairwise_is_unsupported(self):
        snap = base_snapshot(method="pairwise")
        out = C.analyze(snap)
        self.assertEqual(out["status"], "unsupported")
        self.assertEqual(out["reason"], "pairwise_unsupported")

    def test_no_observed_reviews_is_unknown_not_a_crash(self):
        out = C.analyze(base_snapshot())
        self.assertEqual(out["status"], "unknown")
        self.assertEqual(out["reason"], "no_observed_reviews")

    def test_undeclared_roster_is_unknown_even_with_empty_pending(self):
        snap = base_snapshot(
            roster_declared=False, projects=["A"],
            reviews=[review("r1", "j1", "A", {"functionality": 3, "quality": 3, "innovation": 3})],
        )
        out = C.analyze(snap)
        self.assertEqual(out["status"], "unknown")
        self.assertEqual(out["reason"], "roster_not_declared")

    def test_zero_pending_is_unknown_not_stable(self):
        snap = base_snapshot(
            projects=["A"],
            reviews=[review("r1", "j1", "A", {"functionality": 3, "quality": 3, "innovation": 3})],
        )
        out = C.analyze(snap)
        self.assertEqual(out["status"], "unknown")
        self.assertEqual(out["reason"], "no_pending_slots")

    def test_duplicate_review_id_rejected(self):
        snap = base_snapshot(projects=["A"], reviews=[
            review("r1", "j1", "A", {"functionality": 3, "quality": 3, "innovation": 3}),
            review("r1", "j2", "A", {"functionality": 3, "quality": 3, "innovation": 3}),
        ])
        with self.assertRaises(ValueError):
            C.analyze(snap)

    def test_duplicate_judge_project_pair_rejected(self):
        snap = base_snapshot(projects=["A"], reviews=[
            review("r1", "j1", "A", {"functionality": 3, "quality": 3, "innovation": 3}),
            review("r2", "j1", "A", {"functionality": 4, "quality": 4, "innovation": 4}),
        ])
        with self.assertRaises(ValueError):
            C.analyze(snap)

    def test_out_of_range_value_rejected(self):
        snap = base_snapshot(projects=["A"], reviews=[
            review("r1", "j1", "A", {"functionality": 9, "quality": 3, "innovation": 3}),
        ])
        with self.assertRaises(ValueError):
            C.analyze(snap)

    def test_nan_context_rejected(self):
        snap = base_snapshot(context={"x": float("nan")})
        with self.assertRaises(ValueError):
            C.analyze(snap)

    def test_unknown_project_in_review_rejected(self):
        snap = base_snapshot(projects=["A"], reviews=[
            review("r1", "j1", "Z", {"functionality": 3, "quality": 3, "innovation": 3}),
        ])
        with self.assertRaises(ValueError):
            C.analyze(snap)


class ReplayTamperTests(unittest.TestCase):
    def _found_snapshot(self):
        return fixture_snapshot(pending=[
            {"slot_id": "s1", "review_id": "jdg_20__prj_10", "judge_id": "jdg_20", "project_id": "prj_10"},
        ])

    def test_missing_slot_rejected(self):
        snap = self._found_snapshot()
        with self.assertRaises(ValueError):
            C.replay(snap, {"pending": []})

    def test_extra_slot_rejected(self):
        snap = self._found_snapshot()
        witness = {"pending": [
            {"slot_id": "s1", "review_id": "jdg_20__prj_10", "judge_id": "jdg_20",
             "project_id": "prj_10", "values": {"functionality": 5, "quality": 5, "innovation": 5}},
            {"slot_id": "s2", "review_id": "x", "judge_id": "jdg_20",
             "project_id": "prj_10", "values": {"functionality": 5, "quality": 5, "innovation": 5}},
        ]}
        with self.assertRaises(ValueError):
            C.replay(snap, witness)

    def test_tampered_identity_rejected(self):
        snap = self._found_snapshot()
        witness = {"pending": [
            {"slot_id": "s1", "review_id": "jdg_20__prj_10", "judge_id": "jdg_99",
             "project_id": "prj_10", "values": {"functionality": 5, "quality": 5, "innovation": 5}},
        ]}
        with self.assertRaises(ValueError):
            C.replay(snap, witness)

    def test_all_slots_filled_round_trip(self):
        snap = self._found_snapshot()
        out = C.analyze(snap)
        replayed = C.replay(snap, out["witness"])
        self.assertEqual(replayed["winners"], ["prj_10"])


class NoMutationTests(unittest.TestCase):
    def test_analyze_does_not_mutate_snapshot(self):
        snap = fixture_snapshot(pending=[
            {"slot_id": "s1", "review_id": "jdg_20__prj_10", "judge_id": "jdg_20", "project_id": "prj_10"},
        ])
        before = copy.deepcopy(snap)
        out = C.analyze(snap)
        self.assertEqual(snap, before)
        replayed_before = copy.deepcopy(out["witness"])
        C.replay(snap, out["witness"])
        self.assertEqual(out["witness"], replayed_before)
        self.assertEqual(snap, before)


class DigestTests(unittest.TestCase):
    def test_digest_changes_with_context(self):
        snap_a = base_snapshot(context={"revision": 1})
        snap_b = base_snapshot(context={"revision": 2})
        self.assertNotEqual(C.analyze(snap_a)["digest"], C.analyze(snap_b)["digest"])

    def test_digest_changes_with_policy_field(self):
        snap_a = base_snapshot(lam=2.0)
        snap_b = base_snapshot(lam=5.0)
        self.assertNotEqual(C.analyze(snap_a)["digest"], C.analyze(snap_b)["digest"])


class BudgetCapTests(unittest.TestCase):
    def test_max_scenarios_is_capped_at_hard_max(self):
        snap = fixture_snapshot(pending=[
            {"slot_id": "s1", "review_id": "jdg_20__prj_10", "judge_id": "jdg_20", "project_id": "prj_10"},
        ])
        out = C.analyze(snap, max_scenarios=999)
        self.assertEqual(out["scenarios_limit"], C.HARD_MAX_SCENARIOS)

    def test_max_scenarios_rejects_bool_and_non_positive(self):
        snap = base_snapshot()
        with self.assertRaises(ValueError):
            C.analyze(snap, max_scenarios=True)
        with self.assertRaises(ValueError):
            C.analyze(snap, max_scenarios=0)


if __name__ == "__main__":
    unittest.main()
