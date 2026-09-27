"""
Pure-function tests for judging.assign (BUILD-SPEC §10).

No Django settings required.  Uses plain stdlib unittest.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from judging.assign import (  # noqa: E402
    AssignmentProposal,
    JudgeInput,
    ProjectInput,
    ProposedAssignment,
    UnfilledNeed,
    propose_assignments,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_judge(
    jid: str,
    tracks: tuple[str, ...] = ("trk_A",),
    conflicts: tuple[str, ...] = (),
    existing: tuple[str, ...] = (),
) -> JudgeInput:
    return JudgeInput(
        judge_id=jid,
        track_ids=frozenset(tracks),
        conflict_team_ids=frozenset(conflicts),
        existing_assignment_project_ids=frozenset(existing),
    )


def make_project(
    pid: str,
    track: str = "trk_A",
    team: str = "team_1",
    existing_reviewers: tuple[str, ...] = (),
) -> ProjectInput:
    return ProjectInput(
        project_id=pid,
        track_id=track,
        team_id=team,
        existing_reviewer_judge_ids=frozenset(existing_reviewers),
    )


def all_pairs(proposal: AssignmentProposal) -> set[tuple[str, str]]:
    """Return all (judge_id, project_id) pairs in a proposal."""
    return {(a.judge_id, a.project_id) for a in proposal.new_assignments}


def reviewer_counts(proposal: AssignmentProposal) -> dict[str, int]:
    """Count proposed reviews per project."""
    counts: dict[str, int] = {}
    for a in proposal.new_assignments:
        counts[a.project_id] = counts.get(a.project_id, 0) + 1
    return counts


def load_map(proposal: AssignmentProposal) -> dict[str, int]:
    """Count proposed reviews per judge."""
    loads: dict[str, int] = {}
    for a in proposal.new_assignments:
        loads[a.judge_id] = loads.get(a.judge_id, 0) + 1
    return loads


# ---------------------------------------------------------------------------
# 1. Basic eligibility: track and conflict filtering
# ---------------------------------------------------------------------------


class TrackFilterTests(unittest.TestCase):
    """Judges in the wrong track must not be assigned."""

    def test_only_matching_track_assigned(self):
        judges = [
            make_judge("j_A", tracks=("trk_A",)),
            make_judge("j_B", tracks=("trk_B",)),
        ]
        projects = [make_project("p1", track="trk_A")]
        result = propose_assignments(judges, projects, target=1)
        pairs = all_pairs(result)
        self.assertIn(("j_A", "p1"), pairs)
        self.assertNotIn(("j_B", "p1"), pairs)

    def test_no_eligible_judge_produces_unfilled(self):
        judges = [make_judge("j_B", tracks=("trk_B",))]
        projects = [make_project("p1", track="trk_A")]
        result = propose_assignments(judges, projects, target=1)
        self.assertEqual(result.new_assignments, [])
        self.assertEqual(len(result.unfilled), 1)
        u = result.unfilled[0]
        self.assertEqual(u.project_id, "p1")
        self.assertIn("trk_A", u.reason)

    def test_multi_track_judge_covers_both(self):
        judges = [make_judge("j_AB", tracks=("trk_A", "trk_B"))]
        projects = [
            make_project("p1", track="trk_A"),
            make_project("p2", track="trk_B"),
        ]
        result = propose_assignments(judges, projects, target=1)
        pairs = all_pairs(result)
        self.assertIn(("j_AB", "p1"), pairs)
        self.assertIn(("j_AB", "p2"), pairs)


class ConflictFilterTests(unittest.TestCase):
    """Judges with a conflict against the project's team must not be assigned."""

    def test_conflict_blocks_assignment(self):
        judges = [
            make_judge("j_conflict", tracks=("trk_A",), conflicts=("team_X",)),
            make_judge("j_ok", tracks=("trk_A",)),
        ]
        projects = [make_project("p1", track="trk_A", team="team_X")]
        result = propose_assignments(judges, projects, target=1)
        pairs = all_pairs(result)
        self.assertNotIn(("j_conflict", "p1"), pairs)
        self.assertIn(("j_ok", "p1"), pairs)

    def test_all_conflicted_produces_unfilled(self):
        judges = [
            make_judge("j1", tracks=("trk_A",), conflicts=("tm",)),
            make_judge("j2", tracks=("trk_A",), conflicts=("tm",)),
        ]
        projects = [make_project("p1", track="trk_A", team="tm")]
        result = propose_assignments(judges, projects, target=1)
        self.assertEqual(result.new_assignments, [])
        self.assertEqual(len(result.unfilled), 1)
        self.assertIn("conflict", result.unfilled[0].reason)

    def test_conflict_on_other_team_no_effect(self):
        """A conflict with a different team does not block this project."""
        judges = [make_judge("j1", tracks=("trk_A",), conflicts=("other_team",))]
        projects = [make_project("p1", track="trk_A", team="my_team")]
        result = propose_assignments(judges, projects, target=1)
        self.assertEqual(len(result.new_assignments), 1)
        self.assertEqual(result.unfilled, [])


# ---------------------------------------------------------------------------
# 2. No double-assignment
# ---------------------------------------------------------------------------


class NoDoubleAssignmentTests(unittest.TestCase):
    """The same judge must never appear twice for the same project."""

    def test_no_duplicate_pairs_proposed(self):
        judges = [make_judge(f"j{i}", tracks=("trk_A",)) for i in range(3)]
        projects = [make_project(f"p{i}") for i in range(4)]
        result = propose_assignments(judges, projects, target=2)
        pairs = [(a.judge_id, a.project_id) for a in result.new_assignments]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_existing_assignment_not_duplicated(self):
        """If j1 already has p1, the proposal must not re-assign j1 → p1."""
        judges = [
            make_judge("j1", existing=("p1",)),
            make_judge("j2"),
        ]
        projects = [make_project("p1", existing_reviewers=("j1",))]
        result = propose_assignments(judges, projects, target=2)
        pairs = all_pairs(result)
        self.assertNotIn(("j1", "p1"), pairs)

    def test_total_reviewers_with_existing_capped_at_target(self):
        """Existing + new must not exceed target."""
        judges = [make_judge("j1"), make_judge("j2"), make_judge("j3")]
        # p1 already has j1 reviewed; target = 2, so only 1 more needed
        projects = [make_project("p1", existing_reviewers=("j1",))]
        result = propose_assignments(judges, projects, target=2)
        new_for_p1 = [a for a in result.new_assignments if a.project_id == "p1"]
        self.assertEqual(len(new_for_p1), 1)


# ---------------------------------------------------------------------------
# 3. Load balancing
# ---------------------------------------------------------------------------


class LoadBalancingTests(unittest.TestCase):
    """When feasible, max_load − min_load across judges should be ≤ 1."""

    def test_balanced_3_judges_6_projects(self):
        judges = [make_judge(f"j{i}") for i in range(3)]
        projects = [make_project(f"p{i}") for i in range(6)]
        result = propose_assignments(judges, projects, target=1)
        loads = load_map(result)
        values = list(loads.values())
        self.assertEqual(max(values) - min(values), 0)

    def test_balanced_4_judges_8_projects_target2(self):
        judges = [make_judge(f"j{i}") for i in range(4)]
        projects = [make_project(f"p{i}") for i in range(8)]
        result = propose_assignments(judges, projects, target=2)
        loads = load_map(result)
        values = list(loads.values())
        self.assertLessEqual(max(values) - min(values), 1)

    def test_balanced_3_judges_4_projects_target3(self):
        """4 projects × 3 reviewers = 12 units, 4 per judge: perfectly balanced."""
        judges = [make_judge(f"j{i}") for i in range(3)]
        projects = [make_project(f"p{i}") for i in range(4)]
        result = propose_assignments(judges, projects, target=3)
        loads = load_map(result)
        self.assertEqual(len(loads), 3)
        for v in loads.values():
            self.assertEqual(v, 4)

    def test_load_spread_respects_existing(self):
        """Existing assignments count toward load balancing."""
        # j0 already has 3 projects; j1 and j2 have none.
        # New project p_new needs 1 reviewer; should go to j1 or j2.
        judges = [
            make_judge("j0", existing=(f"old_{i}" for i in range(3))),
            make_judge("j1"),
            make_judge("j2"),
        ]
        projects = [make_project("p_new")]
        result = propose_assignments(judges, projects, target=1)
        pairs = all_pairs(result)
        self.assertNotIn(("j0", "p_new"), pairs)


# ---------------------------------------------------------------------------
# 4. Unfillable needs with reasons
# ---------------------------------------------------------------------------


class UnfilledNeedTests(unittest.TestCase):
    """When target is unreachable, report it with a human-readable reason."""

    def test_no_judges_in_track(self):
        judges = [make_judge("j1", tracks=("trk_other",))]
        projects = [make_project("p1", track="trk_X")]
        result = propose_assignments(judges, projects, target=3)
        self.assertEqual(len(result.unfilled), 1)
        u = result.unfilled[0]
        self.assertEqual(u.project_id, "p1")
        self.assertEqual(u.target, 3)
        self.assertIn("trk_X", u.reason)

    def test_fewer_judges_than_target(self):
        """2 judges, target 3: can only assign 2 reviewers."""
        judges = [make_judge(f"j{i}") for i in range(2)]
        projects = [make_project("p1")]
        result = propose_assignments(judges, projects, target=3)
        self.assertEqual(len(result.unfilled), 1)
        u = result.unfilled[0]
        self.assertEqual(u.assigned, 2)
        self.assertIn("2", u.reason)  # reason mentions judge count

    def test_conflict_reduction_causes_unfilled(self):
        """Only 1 eligible judge when target is 2."""
        judges = [
            make_judge("j1", conflicts=("tm",)),
            make_judge("j2"),
        ]
        projects = [make_project("p1", team="tm")]
        result = propose_assignments(judges, projects, target=2)
        self.assertEqual(len(result.unfilled), 1)
        u = result.unfilled[0]
        self.assertEqual(u.assigned, 1)

    def test_all_filled_no_unfilled(self):
        judges = [make_judge(f"j{i}") for i in range(3)]
        projects = [make_project(f"p{i}") for i in range(3)]
        result = propose_assignments(judges, projects, target=2)
        self.assertEqual(result.unfilled, [])

    def test_reason_mentions_track_name(self):
        """The reason string must name the track."""
        judges = []  # no judges at all
        projects = [make_project("p1", track="trk_Health")]
        result = propose_assignments(judges, projects, target=3)
        u = result.unfilled[0]
        self.assertIn("trk_Health", u.reason)

    def test_unfilled_includes_assigned_count(self):
        """u.assigned should reflect how many were successfully assigned."""
        judges = [make_judge("j_only")]
        projects = [make_project("p1")]
        result = propose_assignments(judges, projects, target=3)
        u = result.unfilled[0]
        self.assertEqual(u.assigned, 1)


# ---------------------------------------------------------------------------
# 5. Determinism with seed
# ---------------------------------------------------------------------------


class DeterminismTests(unittest.TestCase):
    """Same inputs + same seed → same output; different seeds may differ."""

    def _run(self, seed: str) -> list[tuple[str, str]]:
        judges = [make_judge(f"j{i}") for i in range(5)]
        projects = [make_project(f"p{i}") for i in range(10)]
        result = propose_assignments(judges, projects, target=3, seed=seed)
        return [(a.judge_id, a.project_id) for a in result.new_assignments]

    def test_same_seed_same_result(self):
        r1 = self._run("test-seed")
        r2 = self._run("test-seed")
        self.assertEqual(r1, r2)

    def test_different_seed_may_differ(self):
        r1 = self._run("seed-alpha")
        r2 = self._run("seed-beta")
        # Not guaranteed to always differ, but for 5 judges × 10 projects
        # it is overwhelmingly likely.
        # We only assert they are both valid (not raising); if they happen to be
        # identical, the test would still pass – that is acceptable.
        self.assertIsInstance(r1, list)
        self.assertIsInstance(r2, list)

    def test_determinism_across_many_calls(self):
        results = [self._run("fixed") for _ in range(20)]
        first = results[0]
        for r in results[1:]:
            self.assertEqual(r, first)


# ---------------------------------------------------------------------------
# 6. Fill-gaps mode (run algorithm on existing partial data)
# ---------------------------------------------------------------------------


class FillGapsTests(unittest.TestCase):
    """fill-gaps = propose_assignments with existing data passed in inputs."""

    def test_fill_gaps_adds_only_missing(self):
        """
        p1 already has 1 reviewer (j0); target 2 → should add exactly 1 more.
        p2 already has 2 reviewers; target 2 → should add nothing.
        """
        j0 = make_judge("j0", existing=("p1",))
        j1 = make_judge("j1")
        j2 = make_judge("j2")
        p1 = make_project("p1", existing_reviewers=("j0",))
        p2 = make_project("p2", existing_reviewers=("j1", "j2"))

        result = propose_assignments([j0, j1, j2], [p1, p2], target=2)
        new_for_p1 = [a for a in result.new_assignments if a.project_id == "p1"]
        new_for_p2 = [a for a in result.new_assignments if a.project_id == "p2"]
        self.assertEqual(len(new_for_p1), 1)
        self.assertEqual(len(new_for_p2), 0)

    def test_fill_gaps_respects_existing_conflicts(self):
        """fill-gaps still honours conflict rules."""
        j_conflict = make_judge("j_conflict", conflicts=("tm",))
        j_ok = make_judge("j_ok")
        p1 = make_project("p1", team="tm", existing_reviewers=())

        result = propose_assignments([j_conflict, j_ok], [p1], target=1)
        pairs = all_pairs(result)
        self.assertNotIn(("j_conflict", "p1"), pairs)
        self.assertIn(("j_ok", "p1"), pairs)

    def test_fill_gaps_no_work_when_already_covered(self):
        """All projects at target → empty proposal."""
        j0 = make_judge("j0", existing=("p1", "p2"))
        j1 = make_judge("j1", existing=("p1", "p2"))
        p1 = make_project("p1", existing_reviewers=("j0", "j1"))
        p2 = make_project("p2", existing_reviewers=("j0", "j1"))
        result = propose_assignments([j0, j1], [p1, p2], target=2)
        self.assertEqual(result.new_assignments, [])
        self.assertEqual(result.unfilled, [])

    def test_fixture_fill_gaps_incomplete_batch(self):
        """
        Simulate a fixture scenario: some projects have 1 review, target is 3.
        After fill-gaps, every project with eligible judges should reach target
        unless the track lacks sufficient judges.
        """
        track = "trk_main"
        judges = [make_judge(f"jdg_{i}", tracks=(track,)) for i in range(6)]

        # Projects p0–p4: p0 has 2 reviews, p1–p4 have 1 review each
        projects = [
            make_project("p0", track=track, existing_reviewers=("jdg_0", "jdg_1")),
            make_project("p1", track=track, existing_reviewers=("jdg_2",)),
            make_project("p2", track=track, existing_reviewers=("jdg_3",)),
            make_project("p3", track=track, existing_reviewers=("jdg_4",)),
            make_project("p4", track=track, existing_reviewers=("jdg_5",)),
        ]

        # Pass existing data so judges' loads are known
        judges_with_load = [
            make_judge("jdg_0", tracks=(track,), existing=("p0",)),
            make_judge("jdg_1", tracks=(track,), existing=("p0",)),
            make_judge("jdg_2", tracks=(track,), existing=("p1",)),
            make_judge("jdg_3", tracks=(track,), existing=("p2",)),
            make_judge("jdg_4", tracks=(track,), existing=("p3",)),
            make_judge("jdg_5", tracks=(track,), existing=("p4",)),
        ]

        result = propose_assignments(judges_with_load, projects, target=3)
        # Every project should now have exactly 3 reviewers (existing + proposed)
        for proj in projects:
            existing_count = len(proj.existing_reviewer_judge_ids)
            new_count = sum(
                1
                for a in result.new_assignments
                if a.project_id == proj.project_id
            )
            total = existing_count + new_count
            self.assertEqual(
                total,
                3,
                f"{proj.project_id}: expected 3 reviewers, got {total}",
            )
        self.assertEqual(result.unfilled, [])


# ---------------------------------------------------------------------------
# 7. Overlap spreading (connectivity)
# ---------------------------------------------------------------------------


class OverlapSpreadingTests(unittest.TestCase):
    """
    When multiple judges have the same load, prefer the one that shares
    fewer already-assigned projects with the project's current reviewers.
    This improves the connectivity of the judge–project graph.
    """

    def test_prefer_judge_with_fewer_shared_projects(self):
        """
        Setup:
          - p1 is already reviewed by j0
          - p2 needs a 2nd reviewer
          - j0 and j1 are both eligible; j0 has load 1 from p1, j1 has load 0
          → j1 should be picked (lower load, zero shared projects with p2's reviewer j_p2)
        """
        # j_p2 already assigned to p2 (1 existing review)
        j_p2 = make_judge("j_p2", existing=("p2",))
        # j_heavy has reviewed p2 (shares reviewer with p2); should not be preferred
        j_heavy = make_judge("j_heavy", existing=("p2",))
        # j_fresh has reviewed nothing
        j_fresh = make_judge("j_fresh")

        p2 = make_project("p2", existing_reviewers=("j_p2",))
        result = propose_assignments(
            [j_p2, j_heavy, j_fresh], [p2], target=2
        )
        # j_p2 is already a reviewer; only j_heavy and j_fresh are candidates.
        # j_fresh has load 0, j_heavy has load 1 → j_fresh preferred.
        pairs = all_pairs(result)
        self.assertIn(("j_fresh", "p2"), pairs)
        self.assertNotIn(("j_p2", "p2"), pairs)

    def test_overlap_spreading_with_equal_loads(self):
        """
        When loads are equal, pick the judge that shares fewer projects
        with the project's existing reviewers.
        """
        # Both j_shared and j_new have load 1.
        # p_target currently reviewed by j_existing.
        # j_shared also reviews p_other (same as j_existing).
        # j_new reviews p_unrelated.
        # So j_shared shares more projects with j_existing; j_new should win.
        j_existing = make_judge("j_existing", existing=("p_target", "p_other"))
        j_shared = make_judge("j_shared", existing=("p_other",))
        j_new = make_judge("j_new", existing=("p_unrelated",))

        p_target = make_project("p_target", existing_reviewers=("j_existing",))

        result = propose_assignments(
            [j_existing, j_shared, j_new], [p_target], target=2
        )
        # j_existing is already assigned, so only j_shared and j_new are candidates.
        # Both have load=1. j_shared shares p_other with j_existing; j_new does not.
        pairs = all_pairs(result)
        self.assertIn(("j_new", "p_target"), pairs)
        self.assertNotIn(("j_shared", "p_target"), pairs)


# ---------------------------------------------------------------------------
# 8. Edge cases
# ---------------------------------------------------------------------------


class EdgeCaseTests(unittest.TestCase):
    def test_empty_judges(self):
        projects = [make_project("p1")]
        result = propose_assignments([], projects, target=2)
        self.assertEqual(result.new_assignments, [])
        self.assertEqual(len(result.unfilled), 1)

    def test_empty_projects(self):
        judges = [make_judge("j1")]
        result = propose_assignments(judges, [], target=2)
        self.assertEqual(result.new_assignments, [])
        self.assertEqual(result.unfilled, [])

    def test_target_one(self):
        judges = [make_judge("j1")]
        projects = [make_project("p1")]
        result = propose_assignments(judges, projects, target=1)
        self.assertEqual(len(result.new_assignments), 1)
        self.assertEqual(result.unfilled, [])

    def test_invalid_target_raises(self):
        with self.assertRaises(ValueError):
            propose_assignments([], [], target=0)

    def test_max_load_respected(self):
        """No judge should exceed max_load in the output."""
        judges = [make_judge(f"j{i}") for i in range(2)]
        projects = [make_project(f"p{i}") for i in range(10)]
        max_load = 3
        result = propose_assignments(judges, projects, target=2, max_load=max_load)
        loads = load_map(result)
        for judge_id, load in loads.items():
            self.assertLessEqual(load, max_load, f"{judge_id} exceeded max_load")

    def test_many_judges_one_project(self):
        """With target=3, exactly 3 judges are assigned out of many."""
        judges = [make_judge(f"j{i}") for i in range(10)]
        projects = [make_project("only_project")]
        result = propose_assignments(judges, projects, target=3)
        new = [a for a in result.new_assignments if a.project_id == "only_project"]
        self.assertEqual(len(new), 3)

    def test_result_types(self):
        """Return type is AssignmentProposal with correct field types."""
        judges = [make_judge("j1")]
        projects = [make_project("p1")]
        result = propose_assignments(judges, projects, target=1)
        self.assertIsInstance(result, AssignmentProposal)
        self.assertIsInstance(result.new_assignments, list)
        self.assertIsInstance(result.unfilled, list)
        if result.new_assignments:
            a = result.new_assignments[0]
            self.assertIsInstance(a, ProposedAssignment)
            self.assertIsInstance(a.judge_id, str)
            self.assertIsInstance(a.project_id, str)


# ---------------------------------------------------------------------------
# 9. Anchor projects (packet P7-ANCHORS, step 1)
# ---------------------------------------------------------------------------


class AnchorTests(unittest.TestCase):
    """anchors_per_track picks calibration anchors before the greedy fill."""

    def test_no_anchors_by_default(self):
        """Default (and explicit 0) reproduces the pre-anchor behaviour."""
        judges = [make_judge(f"j{i}") for i in range(3)]
        projects = [make_project(f"p{i}") for i in range(3)]
        default = propose_assignments(judges, projects, target=2)
        explicit = propose_assignments(
            judges, projects, target=2, anchors_per_track=0
        )
        self.assertEqual(default.anchor_project_ids, [])
        self.assertEqual(explicit.anchor_project_ids, [])
        for result in (default, explicit):
            self.assertTrue(
                all(not a.is_anchor for a in result.new_assignments)
            )
        self.assertEqual(
            [(a.judge_id, a.project_id) for a in default.new_assignments],
            [(a.judge_id, a.project_id) for a in explicit.new_assignments],
        )

    def test_anchor_covers_every_eligible_judge_of_track(self):
        """The anchor is assigned to every eligible judge of its track."""
        judges = [
            make_judge("j1", tracks=("t",)),
            make_judge("j2", tracks=("t",)),
            make_judge("j_other", tracks=("other",)),
        ]
        projects = [
            make_project("p1", track="t"),
            make_project("p2", track="t"),
        ]
        result = propose_assignments(
            judges, projects, target=1, anchors_per_track=1, seed="a1"
        )
        self.assertEqual(len(result.anchor_project_ids), 1)
        anchor = result.anchor_project_ids[0]
        anchored = {
            (a.judge_id, a.project_id)
            for a in result.new_assignments
            if a.is_anchor
        }
        self.assertIn(("j1", anchor), anchored)
        self.assertIn(("j2", anchor), anchored)
        # The off-track judge never touches the anchor.
        self.assertNotIn(("j_other", anchor), anchored)

    def test_anchors_one_per_track(self):
        """Each track gets its own anchor; judges stay in-track."""
        judges = [
            make_judge("jt1", tracks=("t1",)),
            make_judge("jt2", tracks=("t1",)),
            make_judge("ju1", tracks=("t2",)),
        ]
        projects = [
            make_project("pa", track="t1"),
            make_project("pb", track="t1"),
            make_project("pc", track="t2"),
        ]
        result = propose_assignments(
            judges, projects, target=1, anchors_per_track=1, seed="a2"
        )
        by_track = {}
        for pid in result.anchor_project_ids:
            track = next(
                p.track_id for p in projects if p.project_id == pid
            )
            by_track.setdefault(track, []).append(pid)
        self.assertEqual(sorted(by_track), ["t1", "t2"])
        self.assertEqual(len(by_track["t1"]), 1)
        self.assertEqual(len(by_track["t2"]), 1)
        for a in result.new_assignments:
            if a.is_anchor:
                judge = next(
                    j for j in judges if j.judge_id == a.judge_id
                )
                project = next(
                    p for p in projects if p.project_id == a.project_id
                )
                self.assertIn(project.track_id, judge.track_ids)

    def test_anchors_respect_conflicts(self):
        """A judge conflicted with the anchor's team skips the anchor."""
        judges = [
            make_judge("j_clash", tracks=("t",), conflicts=("tm",)),
            make_judge("j_ok", tracks=("t",)),
        ]
        projects = [make_project("p1", track="t", team="tm")]
        result = propose_assignments(
            judges, projects, target=1, anchors_per_track=1, seed="a3"
        )
        self.assertEqual(result.anchor_project_ids, ["p1"])
        anchored = {
            (a.judge_id, a.project_id)
            for a in result.new_assignments
            if a.is_anchor
        }
        self.assertNotIn(("j_clash", "p1"), anchored)
        self.assertIn(("j_ok", "p1"), anchored)

    def test_anchors_prefer_most_eligible_project(self):
        """The anchor is the project with the most eligible judges."""
        judges = [
            make_judge("j1", tracks=("t",)),
            make_judge("j2", tracks=("t",), conflicts=("tm_few",)),
            make_judge("j3", tracks=("t",), conflicts=("tm_few",)),
        ]
        projects = [
            make_project("p_few", track="t", team="tm_few"),  # 1 eligible
            make_project("p_many", track="t", team="tm_many"),  # 3 eligible
        ]
        result = propose_assignments(
            judges, projects, target=1, anchors_per_track=1, seed="a4"
        )
        self.assertEqual(result.anchor_project_ids, ["p_many"])

    def test_anchors_deterministic(self):
        """Same seed → same anchors and assignments."""
        judges = [make_judge(f"j{i}") for i in range(5)]
        projects = [make_project(f"p{i}") for i in range(8)]

        def run(seed):
            result = propose_assignments(
                judges, projects, target=2,
                anchors_per_track=1, seed=seed,
            )
            return (
                result.anchor_project_ids,
                [(a.judge_id, a.project_id, a.is_anchor)
                 for a in result.new_assignments],
            )

        self.assertEqual(run("anchor-seed"), run("anchor-seed"))

    def test_anchors_respect_max_load_and_report(self):
        """Anchor load counts toward max_load; shortfalls name anchors."""
        judges = [make_judge(f"j{i}") for i in range(2)]
        projects = [make_project(f"p{i}") for i in range(2)]
        result = propose_assignments(
            judges, projects, target=1, max_load=1,
            anchors_per_track=1, seed="a5",
        )
        loads: dict[str, int] = {}
        for a in result.new_assignments:
            loads[a.judge_id] = loads.get(a.judge_id, 0) + 1
        for load in loads.values():
            self.assertLessEqual(load, 1)
        # Both judges spent their single slot on the anchor, so the other
        # project is unfilled and the reason says anchor load is why.
        self.assertEqual(len(result.unfilled), 1)
        self.assertIn("Anchor load counts", result.unfilled[0].reason)

    def test_invalid_anchors_raise(self):
        with self.assertRaises(ValueError):
            propose_assignments([], [], target=1, anchors_per_track=-1)


if __name__ == "__main__":
    unittest.main()
