"""Judging command center: pace forecast, at-risk detection and rebalance.

Three layers, each proving its own contract:

* ``ForecastPaceTests`` and ``RebalanceProposalTests`` drive the pure functions
  in ``judging.forecast`` from synthetic timestamps and plain dataclasses.
* ``CommandCenterServiceTests`` proves the service reads through policy, keeps
  imported reviews out of the pace, never moves drafted or submitted work, and
  applies a rebalance atomically and audited.
* ``CommandCenterApiTests`` proves the permission matrix, the page, and that the
  forecast costs a fixed number of queries.
"""
import json
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest import mock

from accounts.models import User
from audit.models import AuditEvent
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from core.errors import ApiError
from events.models import Event, EventRole, Role, Track
from judging import forecast, services
from judging.models import (Assignment, AssignmentBatch, AssignmentMethod, Conflict, Review,
                            ReviewSource, ReviewStatus)
from projects.models import Project, ProjectStatus
from teams.models import Team

T0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime_timezone.utc)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def pace_input(judge_id: str, **kwargs) -> forecast.JudgePaceInput:
    fields = {
        "judge_id": judge_id,
        "name": judge_id.upper(),
        "tracks": ("Alpha",),
        "assigned": 1,
        "first_assigned_at": T0 - timedelta(hours=1),
    }
    fields.update(kwargs)
    return forecast.JudgePaceInput(**fields)


def receiver(judge_id: str, **kwargs) -> forecast.RebalanceJudge:
    fields = {
        "judge_id": judge_id,
        "name": judge_id.upper(),
        "track_ids": frozenset({"trk_alpha"}),
        "assigned": 1,
        "assigned_project_ids": frozenset(),
    }
    fields.update(kwargs)
    return forecast.RebalanceJudge(**fields)


def movable(assignment_id: str, judge_id: str, project_id: str,
            **kwargs) -> forecast.MovableAssignment:
    fields = {
        "assignment_id": assignment_id,
        "judge_id": judge_id,
        "project_id": project_id,
        "track_id": "trk_alpha",
        "team_id": "team_a",
    }
    fields.update(kwargs)
    return forecast.MovableAssignment(**fields)


class ForecastPaceTests(TestCase):
    """Pace is the median of the sub-hour gaps between a judge's own submits."""

    def test_median_of_gaps_ignores_long_breaks(self):
        stamps = [at(0), at(10), at(25), at(40), at(220)]
        self.assertEqual(forecast.minutes_per_review(stamps), 15.0)

    def test_order_of_timestamps_does_not_matter(self):
        self.assertEqual(
            forecast.minutes_per_review([at(40), at(0), at(10)]),
            forecast.minutes_per_review([at(0), at(10), at(40)]),
        )

    def test_no_pace_without_two_usable_gaps(self):
        self.assertIsNone(forecast.minutes_per_review([]))
        self.assertIsNone(forecast.minutes_per_review([at(0)]))
        # Two submits an hour and a half apart is a break, not a pace.
        self.assertIsNone(forecast.minutes_per_review([at(0), at(90)]))

    def test_remaining_times_pace_projects_the_finish(self):
        judge = pace_input("jdg_a", assigned=5, submitted=2,
                           live_submitted_at=(at(0), at(15)))
        row = forecast.judge_forecast(judge, T0, T0 + timedelta(hours=6))
        self.assertEqual(row.minutes_left, 45.0)
        self.assertEqual(row.projected_finish, at(45))
        self.assertEqual(row.status, "in progress")
        self.assertFalse(row.at_risk)

    def test_at_risk_when_the_projection_lands_after_close(self):
        judge = pace_input("jdg_a", assigned=6, submitted=2,
                           live_submitted_at=(at(0), at(15)))
        row = forecast.judge_forecast(judge, T0, T0 + timedelta(minutes=30))
        self.assertIn("projected_finish_after_close", row.reasons)
        self.assertTrue(row.at_risk)

    def test_not_started_after_the_stalled_window(self):
        judge = pace_input("jdg_a", assigned=3, submitted=0,
                           first_assigned_at=T0 - timedelta(hours=13))
        row = forecast.judge_forecast(judge, T0, T0 + timedelta(days=1))
        self.assertEqual(row.reasons, ("not_started",))
        self.assertEqual(row.status, "not started")

    def test_not_started_is_not_raised_before_the_window(self):
        judge = pace_input("jdg_a", assigned=3, submitted=0,
                           first_assigned_at=T0 - timedelta(hours=2))
        row = forecast.judge_forecast(judge, T0, T0 + timedelta(days=1))
        self.assertFalse(row.at_risk)

    def test_overdue_without_pace_is_at_risk(self):
        judge = pace_input("jdg_a", assigned=2, submitted=1, first_assigned_at=T0)
        row = forecast.judge_forecast(judge, T0, T0 - timedelta(minutes=1))
        self.assertIsNone(row.projected_finish)
        self.assertIn("overdue_without_pace", row.reasons)

    def test_finished_and_unassigned_judges_are_never_at_risk(self):
        done = forecast.judge_forecast(
            pace_input("jdg_done", assigned=2, submitted=2,
                       live_submitted_at=(at(0), at(20))),
            T0, T0 - timedelta(minutes=1),
        )
        idle = forecast.judge_forecast(pace_input("jdg_idle", assigned=0, submitted=0), T0, None)
        self.assertEqual(done.status, "done")
        self.assertFalse(done.at_risk)
        self.assertEqual(idle.status, "no assignments")
        self.assertFalse(idle.at_risk)

    def test_event_projection_is_the_latest_judge_finish(self):
        rows = [
            pace_input("jdg_fast", assigned=2, submitted=2,
                       live_submitted_at=(at(0), at(10))),
            pace_input("jdg_slow", assigned=4, submitted=1,
                       live_submitted_at=(at(0), at(10))),
        ]
        result = forecast.build_forecast(rows, T0, T0 + timedelta(days=1))
        self.assertEqual(result.projected_finish, at(30))
        self.assertEqual([row.judge_id for row in result.judges], ["jdg_slow", "jdg_fast"])

    def test_forecast_is_deterministic_and_puts_risk_first(self):
        rows = [
            pace_input("jdg_zulu", assigned=1, submitted=1, live_submitted_at=(at(0), at(5))),
            pace_input("jdg_alpha", assigned=2, submitted=1, live_submitted_at=(at(0), at(5))),
        ]
        close = T0 + timedelta(minutes=10)
        first = forecast.build_forecast(rows, T0, close)
        second = forecast.build_forecast(list(reversed(rows)), T0, close)
        self.assertEqual([row.judge_id for row in first.judges],
                         [row.judge_id for row in second.judges])
        self.assertEqual(first.judges[0].judge_id, "jdg_alpha")
        self.assertEqual(first.as_dict(), second.as_dict())


class RebalanceProposalTests(TestCase):
    """Only untouched work moves, and only to a judge who can take it."""

    def setUp(self):
        self.slow = receiver("jdg_slow", assigned=3, at_risk=True,
                             assigned_project_ids=frozenset({"prj_a1", "prj_a2", "prj_a3"}))
        self.fast = receiver("jdg_fast", assigned=1, projected_finish=at(0),
                             assigned_project_ids=frozenset({"prj_b1"}))
        self.slower = receiver("jdg_slower", assigned=1, projected_finish=at(30))

    def test_moves_go_to_the_earliest_projected_finish(self):
        proposal = forecast.propose_rebalance(
            [self.slow, self.slower, self.fast], [movable("asg_1", "jdg_slow", "prj_a1")], 3,
        )
        self.assertEqual([(move.assignment_id, move.to_judge) for move in proposal.moves],
                         [("asg_1", "jdg_fast")])

    def test_healthy_judges_keep_their_work(self):
        proposal = forecast.propose_rebalance(
            [self.slow, self.fast], [movable("asg_1", "jdg_fast", "prj_b1")], 3,
        )
        self.assertEqual(proposal.moves, ())

    def test_other_track_is_never_a_receiver(self):
        outside = receiver("jdg_beta", assigned=0, projected_finish=at(-600),
                           track_ids=frozenset({"trk_beta"}))
        proposal = forecast.propose_rebalance(
            [self.slow, outside, self.fast], [movable("asg_1", "jdg_slow", "prj_a1")], 3,
        )
        self.assertEqual([move.to_judge for move in proposal.moves], ["jdg_fast"])

    def test_conflicted_receiver_is_never_a_receiver(self):
        conflicted = receiver("jdg_conflict", assigned=0, projected_finish=at(-600),
                              conflict_team_ids=frozenset({"team_a"}))
        proposal = forecast.propose_rebalance(
            [self.slow, conflicted, self.fast], [movable("asg_1", "jdg_slow", "prj_a1")], 3,
        )
        self.assertEqual([move.to_judge for move in proposal.moves], ["jdg_fast"])

    def test_max_load_caps_every_receiver(self):
        only_fast = receiver("jdg_fast", assigned=0, projected_finish=at(0))
        proposal = forecast.propose_rebalance(
            [self.slow, only_fast],
            [movable("asg_1", "jdg_slow", "prj_a1"), movable("asg_2", "jdg_slow", "prj_a2")],
            1,
        )
        self.assertEqual([move.assignment_id for move in proposal.moves], ["asg_1"])
        self.assertEqual(proposal.skipped[0]["assignment"], "asg_2")
        self.assertIn("max load 1", proposal.skipped[0]["reason"])

    def test_a_receiver_who_already_reviews_the_project_is_skipped(self):
        holder = receiver("jdg_holder", assigned=1, projected_finish=at(-60),
                          assigned_project_ids=frozenset({"prj_a1"}))
        proposal = forecast.propose_rebalance(
            [self.slow, holder], [movable("asg_1", "jdg_slow", "prj_a1")], 3,
        )
        self.assertEqual(proposal.moves, ())
        self.assertIn("already review this project", proposal.skipped[0]["reason"])

    def test_default_max_load_is_the_busiest_current_load(self):
        self.assertEqual(forecast.default_max_load([self.slow, self.fast]), 3)

    def test_proposal_is_deterministic_whatever_the_input_order(self):
        movables = [movable("asg_2", "jdg_slow", "prj_a2"), movable("asg_1", "jdg_slow", "prj_a1")]
        judges = [self.slow, self.slower, self.fast]
        first = forecast.propose_rebalance(judges, movables, 3)
        second = forecast.propose_rebalance(list(reversed(judges)), list(reversed(movables)), 3)
        self.assertEqual(first.as_dict(), second.as_dict())


class CommandCenterFixture(TestCase):
    """One event, one stuck judge, and judges who could take the work."""

    def setUp(self):
        now = timezone.now()
        self.organizer = User.objects.create_user("organizer@ex.org", "password",
                                                  display_name="Olive Organizer")
        self.judge = User.objects.create_user("judge@ex.org", "password", display_name="Ada Judge")
        self.peer = User.objects.create_user("peer@ex.org", "password", display_name="Bo Peer")
        self.participant = User.objects.create_user("participant@ex.org", "password")
        self.event = Event.objects.create(
            slug="command-center", name="Command Center",
            submissions_close_at=now - timedelta(days=2),
            judging_open_at=now - timedelta(days=2),
            judging_close_at=now + timedelta(days=2),
            created_by=self.organizer,
        )
        EventRole.objects.create(event=self.event, user=self.organizer, role=Role.ORGANIZER,
                                 public_id="org_cc")
        EventRole.objects.create(event=self.event, user=self.participant, role=Role.PARTICIPANT,
                                 public_id="par_cc")
        self.alpha = Track.objects.create(event=self.event, name="Alpha", public_id="trk_alpha")
        self.beta = Track.objects.create(event=self.event, name="Beta", public_id="trk_beta")
        self.slow = self._judge("jdg_slow", "Stuck Sam", [self.alpha, self.beta])
        self.fast = self._judge("jdg_fast", "Fast Fay", [self.alpha, self.beta])
        self.ok = self._judge("jdg_ok", "Okay Ola", [self.alpha])
        self.beta_only = self._judge("jdg_beta", "Beta Bo", [self.beta])
        self.team_a = Team.objects.create(event=self.event, name="Team A", public_id="team_a1")
        self.teams_alpha = [self.team_a]
        self.teams_beta = []
        for index in (2, 3, 4):
            self.teams_alpha.append(Team.objects.create(
                event=self.event, name=f"Team A{index}", public_id=f"team_a{index}"))
        for index in (1, 2, 3, 4, 5):
            self.teams_beta.append(Team.objects.create(
                event=self.event, name=f"Team B{index}", public_id=f"team_b{index}"))
        self.a1 = self._project("prj_a1", "Alpha One", self.alpha, self.team_a)
        self.a2 = self._project("prj_a2", "Alpha Two", self.alpha, self.teams_alpha[1])
        self.a3 = self._project("prj_a3", "Alpha Three", self.alpha, self.teams_alpha[2])
        beta_projects = [
            self._project(f"prj_b{index}", f"Beta {index}", self.beta, team)
            for index, team in enumerate(self.teams_beta, start=1)
        ]
        self.b1 = beta_projects[0]
        # The stuck judge: three untouched alpha projects, first assigned days ago.
        self.slow_assignments = [
            self._assign("asg_slow_%d" % index, self.slow, project, days=3)
            for index, project in enumerate([self.a1, self.a2, self.a3], start=1)
        ]
        # The receivers: Fast Fay submitted two reviews 15 minutes apart and owes
        # nothing, so she projects a finish of now. Okay Ola has one submission,
        # so no pace and no projection: the last resort, used only with room left.
        self.fast_assignments = [
            self._assign("asg_fast_%d" % index, self.fast, project, days=1)
            for index, project in enumerate(beta_projects[:2], start=1)
        ]
        self._submit(self.fast_assignments[0], minutes_ago=30)
        self._submit(self.fast_assignments[1], minutes_ago=15)
        self.ok_assignment = self._assign("asg_ok_1", self.ok, beta_projects[2], days=1)
        self.ok_review = self._submit(self.ok_assignment, minutes_ago=30)
        self.beta_assignment = self._assign("asg_beta_1", self.beta_only, beta_projects[3], days=0)
        for team in self.teams_alpha:
            Conflict.objects.create(event=self.event, judge=self.beta_only, team=team,
                                    reason="Their cousin submitted")
        # A judge whose only review came from the import: real numbers, no timing.
        self.imported = self._judge("jdg_import", "Import Ivy", [self.beta])
        self.imported_assignment = self._assign("asg_import_1", self.imported, beta_projects[4],
                                                days=1)
        self._submit(self.imported_assignment, minutes_ago=5, source=ReviewSource.IMPORT)

    def _judge(self, public_id, name, tracks):
        user = User.objects.create_user(f"{public_id}@ex.org", "password", display_name=name)
        role = EventRole.objects.create(event=self.event, user=user, role=Role.JUDGE,
                                        public_id=public_id)
        role.tracks.set(tracks)
        return role

    def _project(self, public_id, title, track, team):
        return Project.objects.create(
            event=self.event, team=team, track=track, public_id=public_id, title=title,
            status=ProjectStatus.SUBMITTED, revision=1,
        )

    def _assign(self, public_id, judge, project, days):
        row = Assignment.objects.create(event=self.event, judge=judge, project=project,
                                        public_id=public_id)
        Assignment.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(days=days))
        row.refresh_from_db()
        return row

    def _submit(self, assignment, minutes_ago, source=ReviewSource.LIVE,
                status=ReviewStatus.SUBMITTED):
        return Review.objects.create(
            event=self.event, assignment=assignment, judge=assignment.judge,
            project=assignment.project, status=status, source=source,
            submitted_at=timezone.now() - timedelta(minutes=minutes_ago),
            rubric_version=1, project_revision=1,
        )

    def assertApiError(self, expected_status, operation, expected_code=None):
        with self.assertRaises(ApiError) as context:
            operation()
        self.assertEqual(context.exception.status_code, expected_status)
        if expected_code:
            self.assertEqual(context.exception.code, expected_code)


class CommandCenterServiceTests(CommandCenterFixture):
    def test_forecast_reports_pace_projection_and_risk(self):
        payload = services.command_center(self.organizer, self.event)
        rows = {row["judge"]: row for row in payload["forecast"]["judges"]}
        self.assertEqual(payload["event"], self.event.slug)
        self.assertEqual(rows["jdg_slow"]["assigned"], 3)
        self.assertEqual(rows["jdg_slow"]["remaining"], 3)
        self.assertEqual(rows["jdg_slow"]["reasons"], ["not_started"])
        self.assertTrue(rows["jdg_slow"]["at_risk"])
        self.assertEqual(rows["jdg_slow"]["tracks"], ["Alpha", "Beta"])
        self.assertEqual(rows["jdg_slow"]["status"], "not started")
        # Two submits 15 minutes apart: pace 15, nothing left to do.
        self.assertEqual(rows["jdg_fast"]["pace_minutes"], 15.0)
        self.assertEqual(rows["jdg_fast"]["assigned"], 2)
        self.assertEqual(rows["jdg_fast"]["remaining"], 0)
        self.assertEqual(rows["jdg_fast"]["status"], "done")
        self.assertFalse(rows["jdg_fast"]["at_risk"])
        # One submit is not a pace yet, so the projection is honestly unknown.
        self.assertIsNone(rows["jdg_ok"]["pace_minutes"])
        self.assertIsNone(rows["jdg_ok"]["projected_finish"])
        self.assertFalse(rows["jdg_ok"]["at_risk"])
        self.assertEqual(payload["forecast"]["at_risk_count"], 1)

    def test_imported_reviews_are_left_out_of_the_pace(self):
        payload = services.command_center(self.organizer, self.event)
        rows = {row["judge"]: row for row in payload["forecast"]["judges"]}
        # The count is honest, the timing is not: an import has no real clock.
        self.assertEqual(rows["jdg_import"]["submitted"], 1)
        self.assertEqual(rows["jdg_import"]["remaining"], 0)
        self.assertIsNone(rows["jdg_import"]["pace_minutes"])
        self.assertIsNone(rows["jdg_import"]["last_submitted_at"])

    def test_proposal_moves_only_the_stuck_judges_untouched_work(self):
        proposal = services.command_center(self.organizer, self.event)["proposal"]
        moved = {move["project"]: move for move in proposal["moves"]}
        self.assertEqual(set(moved), {self.a1.public_id, self.a2.public_id, self.a3.public_id})
        self.assertEqual({move["from_judge"] for move in proposal["moves"]}, {"jdg_slow"})
        self.assertEqual(moved[self.a1.public_id]["to_judge"], "jdg_fast")
        self.assertNotIn("jdg_beta", {move["to_judge"] for move in proposal["moves"]})
        # The proposal prints names as well as ids: a judge recognises the work.
        self.assertEqual(moved[self.a1.public_id]["project_title"], "Alpha One")
        self.assertEqual(moved[self.a1.public_id]["track_name"], "Alpha")

    def test_the_same_plan_comes_back_every_time(self):
        first = services.command_center(self.organizer, self.event)
        second = services.command_center(self.organizer, self.event)
        self.assertEqual(first["proposal"], second["proposal"])
        self.assertEqual([row["judge"] for row in first["forecast"]["judges"]],
                         [row["judge"] for row in second["forecast"]["judges"]])

    def test_dry_run_changes_nothing(self):
        result = services.rebalance(self.organizer, self.event, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["batch_created"])
        self.assertTrue(result["moves"])
        self.assertEqual(Assignment.objects.get(public_id="asg_slow_1").judge, self.slow)
        self.assertEqual(AssignmentBatch.objects.count(), 0)

    def test_apply_moves_assignments_records_the_batch_and_audits_it(self):
        result = services.rebalance(self.organizer, self.event, dry_run=False)
        self.assertEqual(result["moved"], 3)
        self.assertTrue(result["batch_created"])
        # Fast Fay projects the earliest finish, so she takes the first move;
        # Okay Ola has no pace at all and is only used while she has room.
        self.assertEqual(Assignment.objects.get(public_id="asg_slow_1").judge, self.fast)
        self.assertEqual(Assignment.objects.get(public_id="asg_slow_2").judge, self.ok)
        self.assertEqual(Assignment.objects.get(public_id="asg_slow_3").judge, self.ok)
        self.assertEqual(self.slow.assignments.count(), 0)
        self.assertEqual(self.fast.assignments.count(), 3)
        self.assertEqual(self.ok.assignments.count(), 3)
        batch = AssignmentBatch.objects.get(event=self.event)
        self.assertEqual(batch.method, AssignmentMethod.REBALANCE)
        self.assertEqual(batch.params["max_load"], 3)
        moved = AuditEvent.objects.filter(event=self.event,
                                          action="judging.assignment_rebalanced")
        self.assertEqual(moved.count(), 3)
        self.assertTrue(AuditEvent.objects.filter(
            event=self.event, action="judging.assignment_batch_created",
            data__method="rebalance").exists())

    def test_max_load_caps_the_moves_the_service_applies(self):
        result = services.rebalance(self.organizer, self.event, dry_run=False, max_load=2)
        self.assertEqual(result["moved"], 1)
        self.assertEqual(result["max_load"], 2)
        # Fast Fay already carries two, so the one move lands on Okay Ola.
        self.assertEqual(Assignment.objects.get(public_id="asg_slow_1").judge, self.ok)
        self.assertEqual(Assignment.objects.get(public_id="asg_slow_2").judge, self.slow)
        self.assertEqual([item["assignment"] for item in result["skipped"]],
                         ["asg_slow_2", "asg_slow_3"])

    def test_drafted_and_submitted_work_never_moves(self):
        drafted = self._submit(self.slow_assignments[0], minutes_ago=90, status=ReviewStatus.DRAFT)
        submitted = self._submit(self.slow_assignments[1], minutes_ago=60)
        result = services.rebalance(self.organizer, self.event, dry_run=False)
        moved_ids = {move["assignment"] for move in result["moves"]}
        self.assertNotIn(drafted.assignment.public_id, moved_ids)
        self.assertNotIn(submitted.assignment.public_id, moved_ids)
        self.assertEqual(Assignment.objects.get(pk=drafted.assignment_id).judge, self.slow)
        self.assertEqual(Assignment.objects.get(pk=submitted.assignment_id).judge, self.slow)

    def test_conflict_and_track_are_rechecked_under_the_lock(self):
        # The plan says Beta Bo takes alpha work; the lock says she is not in that
        # track, so nothing is written rather than a rule bypassed.
        forced = self._forced_plan(to_judge="jdg_beta", to_name="Beta Bo")
        with mock.patch.object(services, "_command_center_data", return_value=forced):
            self.assertApiError(409, lambda: services.rebalance(
                self.organizer, self.event, dry_run=False), "rebalance_changed")
        self.assertEqual(Assignment.objects.get(public_id="asg_slow_1").judge, self.slow)
        self.assertEqual(AssignmentBatch.objects.count(), 0)

    def test_started_work_aborts_the_whole_run(self):
        self._submit(self.slow_assignments[0], minutes_ago=5, status=ReviewStatus.DRAFT)
        forced = self._forced_plan(to_judge="jdg_fast", to_name="Fast Fay")
        with mock.patch.object(services, "_command_center_data", return_value=forced):
            self.assertApiError(409, lambda: services.rebalance(
                self.organizer, self.event, dry_run=False), "rebalance_started")
        self.assertEqual(Assignment.objects.get(public_id="asg_slow_1").judge, self.slow)
        self.assertEqual(AssignmentBatch.objects.count(), 0)

    def _forced_plan(self, to_judge: str, to_name: str) -> dict:
        """A one-move plan handed to the service in place of the computed one."""
        return {
            "forecast": forecast.Forecast(T0, T0, 12.0, (), None, False),
            "proposal": forecast.RebalanceProposal(
                max_load=3,
                moves=(forecast.RebalanceMove(
                    assignment_id="asg_slow_1", from_judge="jdg_slow",
                    from_judge_name="Stuck Sam", to_judge=to_judge, to_judge_name=to_name,
                    project_id="prj_a1", track_id="trk_alpha",
                ),),
            ),
            "roles": {"jdg_fast": self.fast, "jdg_beta": self.beta_only},
        }

    def test_permissions_and_validation(self):
        self.assertApiError(403, lambda: services.command_center(self.judge, self.event),
                            "forbidden")
        self.assertApiError(403, lambda: services.command_center(self.participant, self.event))
        self.assertApiError(401, lambda: services.command_center(None, self.event),
                            "not_authenticated")
        self.assertApiError(403, lambda: services.rebalance(self.judge, self.event),
                            "forbidden")
        self.assertApiError(400, lambda: services.rebalance(
            self.organizer, self.event, dry_run="yes"), "invalid")
        self.assertApiError(400, lambda: services.rebalance(
            self.organizer, self.event, dry_run=True, max_load=0), "invalid")
        self.assertApiError(400, lambda: services.rebalance(
            self.organizer, self.event, dry_run=True, max_load=True), "invalid")


class CommandCenterApiTests(CommandCenterFixture):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.command_url = f"/api/v1/events/{self.event.slug}/command-center"
        self.rebalance_url = f"/api/v1/events/{self.event.slug}/assignments/rebalance"
        self.page_url = f"/manage/{self.event.slug}/command-center"

    def post(self, payload):
        return self.api.post(self.rebalance_url, payload, content_type="application/json")

    def test_organizer_reads_the_forecast(self):
        self.api.force_authenticate(self.organizer)
        response = self.api.get(self.command_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["forecast"]["at_risk_count"], 1)
        self.assertEqual(len(response.data["proposal"]["moves"]), 3)

    def test_permission_matrix(self):
        self.assertEqual(self.api.get(self.command_url).status_code, 401)
        self.assertEqual(self.post({"dry_run": True}).status_code, 401)
        for user in (self.judge, self.participant):
            self.api.force_authenticate(user=user)
            with self.subTest(role=user.email):
                self.assertEqual(self.api.get(self.command_url).status_code, 403)
                self.assertEqual(self.post({"dry_run": True}).status_code, 403)
        self.api.force_authenticate(self.organizer)
        self.assertEqual(
            self.api.get("/api/v1/events/no-such-event/command-center").status_code, 404)
        self.assertEqual(self.post({"dry_run": True, "max_load": 0}).status_code, 400)
        self.assertEqual(self.post({"dry_run": "maybe"}).status_code, 400)

    def test_dry_run_then_apply_through_the_api(self):
        self.api.force_authenticate(self.organizer)
        preview = self.post({"dry_run": True})
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.data["dry_run"])
        self.assertEqual(preview.data["moved"], 0)
        applied = self.post({"dry_run": False, "max_load": 2})
        self.assertEqual(applied.status_code, 200)
        self.assertEqual(applied.data["moved"], 1)
        self.assertEqual(AssignmentBatch.objects.get().method, AssignmentMethod.REBALANCE)

    def test_forecast_costs_a_fixed_number_of_queries(self):
        self.api.force_authenticate(self.organizer)
        with CaptureQueriesContext(connection) as first:
            self.assertEqual(self.api.get(self.command_url).status_code, 200)
        # More judges, more assignments, more reviews: the forecast must not grow
        # its query count, which is what an N+1 would do.
        for index in range(3):
            role = self._judge(f"jdg_more_{index}", f"More Mo {index}", [self.alpha, self.beta])
            for step in range(2):
                track = self.alpha if step else self.beta
                team = Team.objects.create(event=self.event, name=f"More {index}-{step}",
                                           public_id=f"team_more_{index}_{step}")
                project = self._project(f"prj_more_{index}_{step}", f"More {index}-{step}",
                                        track, team)
                assignment = self._assign(f"asg_more_{index}_{step}", role, project, days=1)
                self._submit(assignment, minutes_ago=10 * (index + 1) + step)
        with CaptureQueriesContext(connection) as second:
            self.assertEqual(self.api.get(self.command_url).status_code, 200)
        self.assertEqual(len(first), len(second))
        self.assertLessEqual(len(first), 12)

    def test_page_renders_the_forecast_for_its_organizer(self):
        self.client.force_login(self.organizer)
        response = self.client.get(self.page_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Judging command center")
        self.assertContains(response, "Stuck Sam")
        self.assertContains(response, self.rebalance_url)
        payload = json.loads(response.context["command_center_json"])
        self.assertEqual(payload["forecast"]["at_risk_count"], 1)
        # Timestamps reach the page as ISO 8601, so the JS can parse them.
        self.assertRegex(payload["forecast"]["now"], r"^\d{4}-\d\d-\d\dT")
        self.assertIn("Z", payload["forecast"]["judging_close_at"])

    def test_page_is_closed_to_non_organizers(self):
        self.assertEqual(self.client.get(self.page_url).status_code, 302)
        for user in (self.judge, self.participant):
            self.client.force_login(user)
            with self.subTest(role=user.email):
                self.assertEqual(self.client.get(self.page_url).status_code, 403)
        self.assertEqual(self.client.get("/manage/no-such-event/command-center").status_code, 404)
