"""Judging service validation, lifecycle, and audit tests."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from audit.models import AuditEvent
from core.errors import ApiError
from events.models import Event, EventRole, JudgingMode, Role, Track
from judging import policy, services
from judging.models import (
    Assignment, AssignmentBatch, Comparison, Conflict, Criterion, CriterionScore, JudgeInvite, Review,
    ReviewStatus, Rubric,
)
from projects.models import Project, ProjectStatus
from teams.models import Team


class JudgingServiceTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.organizer = User.objects.create_user("organizer@test.org", "password", display_name="Organize")
        self.judge = User.objects.create_user("judge@test.org", "password", display_name="Judge")
        self.other_judge = User.objects.create_user("other@test.org", "password")
        self.participant = User.objects.create_user("participant@test.org", "password")
        self.event = Event.objects.create(
            slug="judging-test", name="Judging Test",
            submissions_close_at=now - timedelta(days=2),
            judging_open_at=now - timedelta(days=2),
            judging_close_at=now + timedelta(days=2),
            created_by=self.organizer,
        )
        self.organizer_role = EventRole.objects.create(
            event=self.event, user=self.organizer, role=Role.ORGANIZER, public_id="org_test",
        )
        self.judge_role = EventRole.objects.create(
            event=self.event, user=self.judge, role=Role.JUDGE, public_id="jdg_test_judge",
        )
        self.judge_role.tracks.add(Track.objects.create(event=self.event, name="Open"))
        self.track = self.judge_role.tracks.first()
        self.team = Team.objects.create(event=self.event, name="Team")
        self.project = Project.objects.create(
            event=self.event, team=self.team, track=self.track, public_id="prj_judging",
            title="Glass Signal", status=ProjectStatus.SUBMITTED, revision=3,
        )
        self.rubric = Rubric.objects.create(event=self.event)
        self.criteria = [
            Criterion.objects.create(rubric=self.rubric, key="quality", name="Quality",
                                     weight=Decimal("1.000"), min_score=1, max_score=5, position=0),
            Criterion.objects.create(rubric=self.rubric, key="impact", name="Impact",
                                     weight=Decimal("2.000"), min_score=0, max_score=10, position=1),
        ]
        self.assignment = Assignment.objects.create(
            event=self.event, judge=self.judge_role, project=self.project,
        )

    def assertApiError(self, expected_status, operation, expected_code=None):
        with self.assertRaises(ApiError) as context:
            operation()
        self.assertEqual(context.exception.status_code, expected_status)
        if expected_code:
            self.assertEqual(context.exception.code, expected_code)

    def test_rubric_replace_validates_and_bumps_version(self):
        rubric = services.replace_rubric(self.organizer, self.event, [
            {"key": "clarity", "name": "Clarity", "weight": "1.250", "min_score": 1, "max_score": 5},
            {"key": "craft", "name": "Craft", "weight": "2", "min_score": 0, "max_score": 10},
        ])
        self.assertEqual(rubric.version, 2)
        self.assertEqual(list(rubric.criteria.values_list("key", flat=True)), ["clarity", "craft"])
        self.assertApiError(400, lambda: services.replace_rubric(
            self.organizer, self.event, [
                {"key": "same", "name": "One", "weight": 1},
                {"key": "same", "name": "Two", "weight": 1},
            ],
        ), "invalid")

    def test_rubric_is_locked_after_first_submitted_review(self):
        services.submit_review(self.judge, self.event, self.project,
                               {"quality": 4, "impact": 8}, "Good work")
        self.event.refresh_from_db()
        self.assertIsNotNone(self.event.scoring_locked_at)
        self.assertApiError(409, lambda: services.replace_rubric(
            self.organizer, self.event, [{"key": "quality", "name": "Quality", "weight": 1}],
        ), "scoring_locked")

    def test_review_draft_and_submit_validation(self):
        draft = services.save_review_draft(self.judge, self.event, self.project, {"quality": 4}, "Draft")
        self.assertEqual(draft.status, ReviewStatus.DRAFT)
        self.assertEqual(draft.rubric_version, self.rubric.version)
        self.assertEqual(draft.project_revision, 3)
        self.assertApiError(400, lambda: services.submit_review(
            self.judge, self.event, self.project, {"quality": 4}, "",
        ), "invalid")
        self.assertApiError(400, lambda: services.save_review_draft(
            self.judge, self.event, self.project, {"quality": "4"}, "",
        ), "invalid")
        self.assertApiError(400, lambda: services.save_review_draft(
            self.judge, self.event, self.project, {"quality": 6}, "",
        ), "invalid")
        self.assertApiError(400, lambda: services.save_review_draft(
            self.judge, self.event, self.project, {"quality": True}, "",
        ), "invalid")

    def test_submitted_review_locks_scoring_and_audits_summary(self):
        review = services.submit_review(
            self.judge, self.event, self.project, {"quality": 5, "impact": 9}, "Excellent",
        )
        self.assertEqual(review.status, ReviewStatus.SUBMITTED)
        self.assertEqual(review.scores.count(), 2)
        self.event.refresh_from_db()
        self.assertIsNotNone(self.event.scoring_locked_at)
        audit = AuditEvent.objects.get(event=self.event, action="judging.review_submitted")
        self.assertIn("Judge submitted a review of Glass Signal", audit.summary)
        draft = services.save_review_draft(self.judge, self.event, self.project, {"quality": 3}, "Edit")
        self.assertEqual(draft.status, ReviewStatus.DRAFT)
        self.assertIsNone(draft.submitted_at)
        self.assertEqual(draft.scores.get(criterion=self.criteria[1]).value, 9)
        services.submit_review(self.judge, self.event, self.project,
                               {"quality": 4, "impact": 8}, "Revised")
        self.assertEqual(Review.objects.filter(assignment=self.assignment).count(), 1)
        self.assertEqual(CriterionScore.objects.get(review=review, criterion=self.criteria[0]).value, 4)

    def test_judging_closed_denies_review_writes(self):
        self.event.judging_close_at = timezone.now() - timedelta(seconds=1)
        self.event.save(update_fields=["judging_close_at"])
        self.assertApiError(403, lambda: services.save_review_draft(
            self.judge, self.event, self.project, {"quality": "not an integer"}, "",
        ), "judging_closed")

    def test_invite_requires_matching_email_and_accepts_once(self):
        invites = services.create_judge_invites(
            self.organizer, self.event, [self.other_judge.email], [self.track.public_id],
        )
        invite = JudgeInvite.objects.get(token=invites[0]["token"])
        self.assertEqual(invites[0]["url"], f"/judge-invite?token={invite.token}")
        self.assertApiError(403, lambda: services.accept_judge_invite(self.participant, invite.token),
                            "invite_email_mismatch")
        role = services.accept_judge_invite(self.other_judge, invite.token)
        self.assertEqual(role.role, Role.JUDGE)
        self.assertEqual(list(role.tracks.all()), [self.track])
        self.assertApiError(410, lambda: services.accept_judge_invite(self.other_judge, invite.token), "invite_gone")

    def test_add_judge_role_conflict_returns_409(self):
        self.assertApiError(409, lambda: services.add_judge(
            self.organizer, self.event, self.judge.email, [self.track.public_id],
        ), "role_conflict")

    def test_conflict_removes_unreviewed_assignment(self):
        conflict = services.declare_conflict(self.judge, self.event, self.team, "Former colleague")
        self.assertEqual(conflict.source, "declared_by_judge")
        self.assertFalse(Assignment.objects.filter(pk=self.assignment.pk).exists())
        self.assertTrue(Conflict.objects.filter(pk=conflict.pk).exists())

    def test_conflict_cannot_remove_assignment_with_submitted_review(self):
        services.submit_review(self.judge, self.event, self.project, {"quality": 4, "impact": 8})
        self.assertApiError(409, lambda: services.declare_conflict(
            self.judge, self.event, self.team, "Late conflict",
        ), "review_exists")

    def test_remove_judge_with_submitted_review_is_refused(self):
        services.submit_review(self.judge, self.event, self.project, {"quality": 4, "impact": 8})
        self.assertApiError(409, lambda: services.remove_judge(
            self.organizer, self.event, self.judge_role.public_id,
        ), "judge_has_reviews")

    def test_remove_judge_deletes_drafts_and_assignments(self):
        Review.objects.create(event=self.event, assignment=self.assignment, judge=self.judge_role,
                              project=self.project, status=ReviewStatus.DRAFT)
        services.remove_judge(self.organizer, self.event, self.judge_role.public_id)
        self.assertFalse(EventRole.objects.filter(pk=self.judge_role.pk).exists())
        self.assertFalse(Assignment.objects.filter(pk=self.assignment.pk).exists())

    def test_cross_event_track_assignment_and_conflict_are_rejected(self):
        other_event = Event.objects.create(
            slug="cross-event", name="Cross Event",
            submissions_close_at=timezone.now() + timedelta(days=1), created_by=self.organizer,
        )
        other_track = Track.objects.create(event=other_event, name="Foreign")
        other_role = EventRole.objects.create(
            event=other_event, user=self.other_judge, role=Role.JUDGE, public_id="jdg_foreign",
        )
        other_team = Team.objects.create(event=other_event, name="Foreign team")
        self.assertApiError(400, lambda: services.update_judge_tracks(
            self.organizer, self.event, self.judge_role.public_id, [other_track.public_id],
        ), "cross_event")
        self.assertApiError(400, lambda: services.assign_batch(
            self.organizer, self.event, [other_role.public_id], [self.project.public_id],
        ), "cross_event")
        self.assertApiError(400, lambda: services.add_conflict(
            self.organizer, self.event, self.judge_role.public_id, other_team.public_id,
        ), "cross_event")

    def test_manual_assignment_reports_ineligibility_and_creates_batch(self):
        second_track = Track.objects.create(event=self.event, name="Other")
        second_judge = User.objects.create_user("second@test.org", "password")
        second_role = EventRole.objects.create(
            event=self.event, user=second_judge, role=Role.JUDGE, public_id="jdg_second",
        )
        second_role.tracks.add(second_track)
        project = Project.objects.create(
            event=self.event, team=Team.objects.create(event=self.event, name="Other track team"),
            track=second_track, public_id="prj_other_track", title="Other track",
            status=ProjectStatus.SUBMITTED, revision=1,
        )
        result = services.assign_batch(
            self.organizer, self.event, [self.judge_role.public_id], [project.public_id],
        )
        self.assertEqual(result["created"], [])
        self.assertEqual(result["skipped"][0]["reason"], "track_mismatch")
        result = services.assign_batch(
            self.organizer, self.event, [second_role.public_id], [project.public_id],
        )
        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["created"][0].batch.method, "manual")
        self.assertTrue(AuditEvent.objects.filter(action="judging.assignment_batch_created").exists())

    def test_auto_assignment_is_dry_until_applied(self):
        second_judge = User.objects.create_user("second-auto@test.org", "password")
        second_role = EventRole.objects.create(
            event=self.event, user=second_judge, role=Role.JUDGE, public_id="jdg_auto",
        )
        second_role.tracks.add(self.track)
        project = Project.objects.create(
            event=self.event, team=Team.objects.create(event=self.event, name="Auto team"),
            track=self.track, public_id="prj_auto", title="Auto project",
            status=ProjectStatus.SUBMITTED, revision=1,
        )
        preview = services.auto_assign(self.organizer, self.event, target=1, seed="seed", dry_run=True)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(Assignment.objects.filter(project=project).count(), 0)
        applied = services.auto_assign(self.organizer, self.event, target=1, seed="seed", dry_run=False)
        self.assertFalse(applied["dry_run"])
        self.assertEqual(Assignment.objects.filter(project=project).count(), 1)
        self.assertEqual(applied["batch"].method, "auto")
        self.assertTrue(AssignmentBatch.objects.filter(event=self.event, method="auto").exists())

    def test_progress_counts_submitted_reviews_not_assignments(self):
        another_project = Project.objects.create(
            event=self.event, team=Team.objects.create(event=self.event, name="Unreviewed team"),
            track=self.track, public_id="prj_unreviewed", title="Unreviewed",
            status=ProjectStatus.SUBMITTED, revision=1,
        )
        Assignment.objects.create(event=self.event, judge=self.judge_role, project=another_project)
        review = services.submit_review(
            self.judge, self.event, self.project, {"quality": 4, "impact": 8},
        )
        data = policy.progress(self.event)
        judge_row = next(row for row in data["judges"] if row["judge"] == self.judge_role.public_id)
        unreviewed = next(row for row in data["projects"] if row["project"] == another_project.public_id)
        self.assertEqual(judge_row["submitted"], 1)
        self.assertEqual(judge_row["remaining"], 1)
        self.assertEqual(unreviewed["submitted"], 0)
        self.assertTrue(unreviewed["under_covered"])
        self.assertEqual(review.status, ReviewStatus.SUBMITTED)

    def test_other_event_project_is_rejected_for_assignment(self):
        other_event = Event.objects.create(
            slug="foreign-project-event", name="Foreign Project",
            submissions_close_at=timezone.now() + timedelta(days=1), created_by=self.organizer,
        )
        other_project = Project.objects.create(
            event=other_event, team=Team.objects.create(event=other_event, name="Foreign"),
            public_id="prj_foreign", title="Foreign", status=ProjectStatus.SUBMITTED, revision=1,
        )
        self.assertApiError(400, lambda: services.assign_batch(
            self.organizer, self.event, [self.judge_role.public_id], [other_project.public_id],
        ), "cross_event")

    def test_pairwise_assignments_propose_and_record_each_pair_once(self):
        self.event.judging_mode = JudgingMode.PAIRWISE
        self.event.save(update_fields=["judging_mode"])
        second_project = Project.objects.create(
            event=self.event, team=Team.objects.create(event=self.event, name="Pair team"),
            track=self.track, public_id="prj_pair", title="Pair project",
            status=ProjectStatus.SUBMITTED, revision=1,
        )
        Assignment.objects.create(event=self.event, judge=self.judge_role, project=second_project)
        pair = services.next_pair(self.judge, self.event)
        self.assertEqual({pair[0].pk, pair[1].pk}, {self.project.pk, second_project.pk})
        comparison = services.save_comparison(
            self.judge, self.event, pair[0], pair[1], pair[0],
        )
        self.assertEqual(comparison.winner_id, pair[0].pk)
        self.assertEqual(Comparison.objects.filter(judge=self.judge_role).count(), 1)
        self.assertIsNone(services.next_pair(self.judge, self.event))
        self.assertApiError(409, lambda: services.save_comparison(
            self.judge, self.event, pair[1], pair[0], pair[1],
        ), "comparison_exists")

    def test_pairwise_disabled_and_unassigned_comparison_are_denied(self):
        self.assertApiError(409, lambda: services.next_pair(self.judge, self.event), "pairwise_disabled")
        self.event.judging_mode = JudgingMode.BOTH
        self.event.save(update_fields=["judging_mode"])
        unassigned = Project.objects.create(
            event=self.event, team=Team.objects.create(event=self.event, name="Unassigned pair"),
            track=self.track, public_id="prj_unassigned_pair", title="Unassigned pair",
            status=ProjectStatus.SUBMITTED, revision=1,
        )
        self.assertApiError(403, lambda: services.save_comparison(
            self.judge, self.event, self.project, unassigned, self.project,
        ), "forbidden")

    def test_services_and_policy_deny_non_judges_participants_and_other_organizers(self):
        other_organizer = User.objects.create_user("other-organizer@test.org", "password")
        other_event = Event.objects.create(
            slug="other-judging-event", name="Other Judging Event",
            submissions_close_at=timezone.now() + timedelta(days=1), created_by=other_organizer,
        )
        EventRole.objects.create(event=other_event, user=other_organizer, role=Role.ORGANIZER,
                                 public_id="org_other_judging")
        self.assertApiError(403, lambda: services.replace_rubric(
            self.participant, self.event, [{"key": "x", "name": "X", "weight": 1}],
        ), "forbidden")
        self.assertApiError(403, lambda: services.add_judge(
            other_organizer, self.event, self.other_judge.email, [],
        ), "forbidden")
        self.assertApiError(403, lambda: services.save_review_draft(
            self.participant, self.event, self.project, {"quality": 4}, "",
        ), "forbidden")
        self.assertApiError(403, lambda: services.submit_review(
            other_organizer, self.event, self.project, {"quality": 4, "impact": 5},
        ), "forbidden")
        self.assertApiError(403, lambda: services.assign_batch(
            self.judge, self.event, [self.judge_role.public_id], [self.project.public_id],
        ), "forbidden")
        self.assertFalse(policy.visible_reviews(self.participant, self.event).exists())
        self.assertApiError(403, lambda: policy.visible_assignments(self.participant, self.event),
                            "forbidden")
