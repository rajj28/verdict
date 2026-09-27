"""Results services, policy and API tests.

Covers: preview, publish, public_results, decision_record, verify_publication,
feedback release, and all permission checks (organizer / judge / participant).
"""
from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from audit.models import AuditEvent
from core.errors import ApiError
from events.models import Event, EventRole, Role, Track
from judging.models import (
    Assignment,
    CriterionScore,
    Review,
    ReviewExclusion,
    ReviewStatus,
    Rubric,
    Criterion as JudgingCriterion,
)
from projects.models import Project, ProjectStatus
from results.models import ResultPublication
from results import services, policy
from teams.models import Team, TeamMember


def _utc(days_offset: int = 0):
    return timezone.now() + timedelta(days=days_offset)


class ResultsTestCase(TestCase):
    """Base test case: one event, rubric, two judges, two submitted projects and reviews."""

    @classmethod
    def setUpTestData(cls):
        cls.organizer = User.objects.create_user(
            "org@results.test", "password", display_name="Organizer"
        )
        cls.judge1 = User.objects.create_user("j1@results.test", "password")
        cls.judge2 = User.objects.create_user("j2@results.test", "password")
        cls.participant_user = User.objects.create_user("p1@results.test", "password")
        cls.other_user = User.objects.create_user("other@results.test", "password")

        past = timezone.now() - timedelta(days=10)
        cls.event = Event.objects.create(
            slug="results-test",
            name="Results Test",
            submissions_close_at=past,
            judging_open_at=past,
            judging_close_at=past + timedelta(days=2),  # judging already closed
            created_by=cls.organizer,
        )
        cls.organizer_role = EventRole.objects.create(
            event=cls.event, user=cls.organizer, role=Role.ORGANIZER, public_id="org_rt",
        )
        cls.track = Track.objects.create(event=cls.event, name="Open Track", position=1)
        cls.judge1_role = EventRole.objects.create(
            event=cls.event, user=cls.judge1, role=Role.JUDGE, public_id="jdg_rt1",
        )
        cls.judge1_role.tracks.add(cls.track)
        cls.judge2_role = EventRole.objects.create(
            event=cls.event, user=cls.judge2, role=Role.JUDGE, public_id="jdg_rt2",
        )
        cls.judge2_role.tracks.add(cls.track)
        cls.participant_role = EventRole.objects.create(
            event=cls.event, user=cls.participant_user, role=Role.PARTICIPANT,
            public_id="par_rt1",
        )

        cls.team1 = Team.objects.create(event=cls.event, name="Team Alpha")
        TeamMember.objects.create(team=cls.team1, user=cls.participant_user, event=cls.event,
                                   is_owner=True)
        cls.team2 = Team.objects.create(event=cls.event, name="Team Beta")

        cls.project1 = Project.objects.create(
            event=cls.event, team=cls.team1, track=cls.track,
            public_id="prj_rt1", title="Alpha Project",
            status=ProjectStatus.SUBMITTED, revision=1,
        )
        cls.project2 = Project.objects.create(
            event=cls.event, team=cls.team2, track=cls.track,
            public_id="prj_rt2", title="Beta Project",
            status=ProjectStatus.SUBMITTED, revision=1,
        )

        # Rubric
        cls.rubric = Rubric.objects.create(event=cls.event, version=1)
        cls.c1 = JudgingCriterion.objects.create(
            rubric=cls.rubric, key="quality", name="Quality",
            weight=Decimal("1.000"), min_score=1, max_score=5, position=0,
        )
        cls.c2 = JudgingCriterion.objects.create(
            rubric=cls.rubric, key="innovation", name="Innovation",
            weight=Decimal("1.000"), min_score=1, max_score=5, position=1,
        )

        # Reviews: judge1 reviews both projects, judge2 reviews project1
        cls.asg1_1 = Assignment.objects.create(
            event=cls.event, judge=cls.judge1_role, project=cls.project1,
        )
        cls.review1_1 = Review.objects.create(
            event=cls.event, assignment=cls.asg1_1, judge=cls.judge1_role,
            project=cls.project1, status=ReviewStatus.SUBMITTED,
            comment="Good work", submitted_at=past,
            rubric_version=1, project_revision=1,
        )
        CriterionScore.objects.create(review=cls.review1_1, criterion=cls.c1, value=4)
        CriterionScore.objects.create(review=cls.review1_1, criterion=cls.c2, value=4)

        cls.asg1_2 = Assignment.objects.create(
            event=cls.event, judge=cls.judge1_role, project=cls.project2,
        )
        cls.review1_2 = Review.objects.create(
            event=cls.event, assignment=cls.asg1_2, judge=cls.judge1_role,
            project=cls.project2, status=ReviewStatus.SUBMITTED,
            comment="Interesting approach", submitted_at=past,
            rubric_version=1, project_revision=1,
        )
        CriterionScore.objects.create(review=cls.review1_2, criterion=cls.c1, value=3)
        CriterionScore.objects.create(review=cls.review1_2, criterion=cls.c2, value=3)

        cls.asg2_1 = Assignment.objects.create(
            event=cls.event, judge=cls.judge2_role, project=cls.project1,
        )
        cls.review2_1 = Review.objects.create(
            event=cls.event, assignment=cls.asg2_1, judge=cls.judge2_role,
            project=cls.project1, status=ReviewStatus.SUBMITTED,
            comment="Excellent", submitted_at=past,
            rubric_version=1, project_revision=1,
        )
        CriterionScore.objects.create(review=cls.review2_1, criterion=cls.c1, value=5)
        CriterionScore.objects.create(review=cls.review2_1, criterion=cls.c2, value=5)

        # Lock scoring
        Event.objects.filter(pk=cls.event.pk).update(
            scoring_locked_at=past
        )
        cls.event.refresh_from_db()


class PreviewTests(ResultsTestCase):
    def test_preview_returns_rows_for_submitted_projects(self):
        data = services.preview(self.event)
        self.assertIn("rows", data)
        self.assertIn("judge_rows", data)
        self.assertIn("input_digest", data)
        project_ids = [r["project_id"] for r in data["rows"]]
        self.assertIn("prj_rt1", project_ids)
        self.assertIn("prj_rt2", project_ids)

    def test_preview_project1_ranked_higher_than_project2(self):
        """Project 1 has better scores from both judges."""
        data = services.preview(self.event)
        rows_by_id = {r["project_id"]: r for r in data["rows"]}
        r1 = rows_by_id["prj_rt1"]
        r2 = rows_by_id["prj_rt2"]
        # Project 1 should have a higher or equal normalized score
        self.assertGreaterEqual(r1.get("normalized") or 0, r2.get("normalized") or 0)

    def test_preview_includes_diagnostics(self):
        data = services.preview(self.event)
        self.assertIn("diagnostics", data)
        self.assertIn("spread", data)

    def test_preview_lam_auto_when_null(self):
        """When event.shrinkage_lambda is null, 'auto' is used and lambda_choice is stored."""
        self.assertIsNone(self.event.shrinkage_lambda)
        data = services.preview(self.event)
        # lambda_choice may be None if too few reviews for CV, but lam is always set
        self.assertIsNotNone(data.get("lam"))

    def test_preview_n_included_and_excluded(self):
        data = services.preview(self.event)
        self.assertEqual(data["n_included"], 3)
        self.assertEqual(data["n_excluded"], 0)

    def test_excluded_reviews_not_in_preview(self):
        """An excluded review must not contribute to the preview results."""
        ReviewExclusion.objects.create(
            review=self.review2_1, reason="Test exclusion", created_by=self.organizer
        )
        try:
            data = services.preview(self.event)
            self.assertEqual(data["n_included"], 2)
            self.assertEqual(data["n_excluded"], 1)
        finally:
            ReviewExclusion.objects.filter(review=self.review2_1).delete()

    def test_superseded_project_not_in_included(self):
        """Superseded projects must never appear in included review inputs."""
        inputs = services.collect_inputs(self.event)
        # All projects here are submitted – create one superseded and verify
        superseded_project = Project.objects.create(
            event=self.event, team=self.team2, track=self.track,
            public_id="prj_sup_rt", title="Superseded",
            status=ProjectStatus.SUPERSEDED, revision=1,
        )
        # A review of the superseded project (shouldn't appear)
        sup_asg = Assignment.objects.create(
            event=self.event, judge=self.judge2_role, project=superseded_project,
        )
        sup_review = Review.objects.create(
            event=self.event, assignment=sup_asg, judge=self.judge2_role,
            project=superseded_project, status=ReviewStatus.SUBMITTED,
            submitted_at=timezone.now() - timedelta(days=10),
            rubric_version=1, project_revision=1,
        )
        inputs2 = services.collect_inputs(self.event)
        included_ids = {r["project_id"] for r in inputs2["included"]}
        self.assertNotIn("prj_sup_rt", included_ids)
        ineligible_ids = {p["project_id"] for p in inputs2["ineligible_projects"]}
        self.assertIn("prj_sup_rt", ineligible_ids)
        # Cleanup
        sup_review.delete()
        sup_asg.delete()
        superseded_project.delete()


class PublishTests(ResultsTestCase):
    def test_publish_blocked_while_judging_open(self):
        """Cannot publish while judging window is still open."""
        future = timezone.now() + timedelta(days=1)
        event2 = Event.objects.create(
            slug="pub-test-open",
            name="Pub Test Open",
            submissions_close_at=timezone.now() - timedelta(days=10),
            judging_open_at=timezone.now() - timedelta(days=10),
            # No close_at = still open
            judging_close_at=None,
            created_by=self.organizer,
        )
        EventRole.objects.create(
            event=event2, user=self.organizer, role=Role.ORGANIZER, public_id="org_pub",
        )
        rubric2 = Rubric.objects.create(event=event2)
        JudgingCriterion.objects.create(
            rubric=rubric2, key="q", name="Q",
            weight=Decimal("1.000"), min_score=1, max_score=5, position=0,
        )
        with self.assertRaises(ApiError) as ctx:
            services.publish(self.organizer, event2)
        self.assertEqual(ctx.exception.code, "judging_open")
        event2.delete()

    def test_publish_succeeds_when_judging_closed(self):
        """Publish creates a ResultPublication with rows and digest."""
        pub = services.publish(self.organizer, self.event, note="Initial")
        self.assertIsInstance(pub, ResultPublication)
        self.assertEqual(len(pub.input_digest), 64)
        self.assertTrue(len(pub.rows) > 0)
        self.assertTrue(len(pub.judge_rows) > 0)
        self.assertEqual(pub.published_by, self.organizer)
        self.assertEqual(pub.event, self.event)
        # Cleanup
        pub.delete()

    def test_second_publish_requires_note(self):
        pub1 = services.publish(self.organizer, self.event, note="First")
        try:
            with self.assertRaises(ApiError) as ctx:
                services.publish(self.organizer, self.event, note="")
            self.assertEqual(ctx.exception.code, "note_required")
        finally:
            pub1.delete()

    def test_second_publish_supersedes_first(self):
        pub1 = services.publish(self.organizer, self.event, note="First")
        try:
            pub2 = services.publish(self.organizer, self.event, note="Re-run")
            try:
                self.assertEqual(pub2.supersedes, pub1)
            finally:
                pub2.delete()
        finally:
            pub1.delete()

    def test_publish_audits_the_event(self):
        initial_count = AuditEvent.objects.filter(
            event=self.event, action="results.published"
        ).count()
        pub = services.publish(self.organizer, self.event, note="Audit test")
        try:
            self.assertEqual(
                AuditEvent.objects.filter(
                    event=self.event, action="results.published"
                ).count(),
                initial_count + 1,
            )
        finally:
            pub.delete()

    def test_unranked_project_blocks_publish_without_acknowledge(self):
        """A project with no reviews causes 409 unless acknowledge_unranked=true."""
        # Create a project with no reviews
        empty_team = Team.objects.create(event=self.event, name="Empty Team")
        unranked = Project.objects.create(
            event=self.event, team=empty_team, track=self.track,
            public_id="prj_unranked", title="Unranked",
            status=ProjectStatus.SUBMITTED, revision=1,
        )
        try:
            with self.assertRaises(ApiError) as ctx:
                services.publish(self.organizer, self.event)
            self.assertEqual(ctx.exception.code, "unranked_projects")
            self.assertIn("prj_unranked", ctx.exception.fields.get("unranked_projects", []))
            # Acknowledge it
            pub = services.publish(
                self.organizer, self.event, note="With unranked", acknowledge_unranked=True
            )
            pub.delete()
        finally:
            unranked.delete()
            empty_team.delete()

    def test_digest_changes_when_review_score_changes(self):
        """Storing different criterion values must change the digest."""
        data1 = services.preview(self.event)
        digest1 = data1["input_digest"]
        # Change a score temporarily
        score = CriterionScore.objects.get(review=self.review1_1, criterion=self.c1)
        original = score.value
        score.value = 2
        score.save()
        data2 = services.preview(self.event)
        digest2 = data2["input_digest"]
        # Restore
        score.value = original
        score.save()
        self.assertNotEqual(digest1, digest2)


class PublicResultsTests(ResultsTestCase):
    def test_public_results_404_when_not_published(self):
        with self.assertRaises(ApiError) as ctx:
            services.public_results(self.event)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_public_results_after_publish(self):
        pub = services.publish(self.organizer, self.event, note="Public test")
        try:
            data = services.public_results(self.event)
            self.assertIn("rows", data)
            # Public rows must not contain judge data
            for row in data["rows"]:
                self.assertNotIn("judge_rows", row)
                self.assertNotIn("offset", row)
                self.assertNotIn("comment", row)
        finally:
            pub.delete()


class PermissionTests(ResultsTestCase):
    def test_judge_cannot_preview(self):
        with self.assertRaises(ApiError) as ctx:
            policy.require_organizer(self.judge1, self.event)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_participant_cannot_preview(self):
        with self.assertRaises(ApiError) as ctx:
            policy.require_organizer(self.participant_user, self.event)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_anonymous_cannot_preview(self):
        from django.contrib.auth.models import AnonymousUser
        with self.assertRaises(ApiError) as ctx:
            policy.require_organizer(AnonymousUser(), self.event)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_organizer_can_preview(self):
        # Should not raise
        policy.require_organizer(self.organizer, self.event)

    def test_admin_can_preview(self):
        admin = User.objects.create_user(
            "admin2@results.test", "password", is_admin=True
        )
        policy.require_organizer(admin, self.event)


class VerifyPublicationTests(ResultsTestCase):
    def test_verify_identical_after_publish(self):
        pub = services.publish(self.organizer, self.event, note="Verify test")
        try:
            result = services.verify_publication(pub)
            self.assertEqual(result["verdict"], "identical")
            self.assertTrue(result["rows_match"])
        finally:
            pub.delete()

    def test_verify_differs_after_score_tampered(self):
        pub = services.publish(self.organizer, self.event, note="Tamper test")
        try:
            # Tamper a criterion score directly
            score = CriterionScore.objects.get(review=self.review1_1, criterion=self.c1)
            original = score.value
            score.value = 1  # change
            score.save()
            result = services.verify_publication(pub)
            self.assertEqual(result["verdict"], "differs")
            self.assertFalse(result["digest_match"])
            # Restore
            score.value = original
            score.save()
        finally:
            pub.delete()


class FeedbackTests(ResultsTestCase):
    def test_feedback_not_available_without_publication(self):
        with self.assertRaises(ApiError) as ctx:
            services.release_feedback(self.organizer, self.event)
        self.assertEqual(ctx.exception.code, "not_published")

    def test_release_and_retract_feedback(self):
        pub = services.publish(self.organizer, self.event, note="Feedback test")
        try:
            # Release
            pub_updated = services.release_feedback(self.organizer, self.event)
            self.assertIsNotNone(pub_updated.feedback_released_at)
            # Retract
            pub_retracted = services.retract_feedback(self.organizer, self.event)
            self.assertIsNone(pub_retracted.feedback_released_at)
        finally:
            pub.delete()

    def test_team_sees_own_feedback_after_release(self):
        """team member can call project_feedback after release."""
        pub = services.publish(self.organizer, self.event, note="Feedback access")
        services.release_feedback(self.organizer, self.event)
        try:
            data = services.project_feedback(self.event, self.project1)
            self.assertTrue(data["feedback_released"])
            self.assertIsNotNone(data.get("official_score"))
            # No judge ids in response
            for comment in data.get("comments", []):
                self.assertNotIn("jdg_", comment)
        finally:
            ResultPublication.objects.filter(event=self.event).delete()

    def test_team_cannot_see_other_teams_feedback(self):
        """can_see_feedback returns False for non-member."""
        self.assertFalse(
            policy.can_see_feedback(self.other_user, self.event, self.project1)
        )

    def test_organizer_can_always_see_feedback(self):
        self.assertTrue(
            policy.can_see_feedback(self.organizer, self.event, self.project1)
        )

    def test_team_member_can_see_own_feedback(self):
        self.assertTrue(
            policy.can_see_feedback(self.participant_user, self.event, self.project1)
        )

    def test_release_twice_raises(self):
        pub = services.publish(self.organizer, self.event, note="Double release")
        services.release_feedback(self.organizer, self.event)
        try:
            with self.assertRaises(ApiError) as ctx:
                services.release_feedback(self.organizer, self.event)
            self.assertEqual(ctx.exception.code, "already_released")
        finally:
            ResultPublication.objects.filter(event=self.event).delete()


class ResultsAPIPermissionTests(TestCase):
    """HTTP-level permission tests via the test client."""

    @classmethod
    def setUpTestData(cls):
        past = timezone.now() - timedelta(days=10)
        cls.organizer = User.objects.create_user("org@api.test", "password")
        cls.judge = User.objects.create_user("judge@api.test", "password")
        cls.participant = User.objects.create_user("part@api.test", "password")
        cls.event = Event.objects.create(
            slug="results-api-test",
            name="Results API Test",
            submissions_close_at=past,
            judging_open_at=past,
            judging_close_at=past + timedelta(days=1),
            created_by=cls.organizer,
        )
        EventRole.objects.create(
            event=cls.event, user=cls.organizer, role=Role.ORGANIZER, public_id="org_api",
        )
        EventRole.objects.create(
            event=cls.event, user=cls.judge, role=Role.JUDGE, public_id="jdg_api",
        )
        EventRole.objects.create(
            event=cls.event, user=cls.participant, role=Role.PARTICIPANT, public_id="par_api",
        )
        rubric = Rubric.objects.create(event=cls.event)
        JudgingCriterion.objects.create(
            rubric=rubric, key="q", name="Q",
            weight=Decimal("1.000"), min_score=1, max_score=5, position=0,
        )

    def test_preview_requires_organizer(self):
        self.client.force_login(self.judge)
        resp = self.client.get(f"/api/v1/events/{self.event.slug}/results/preview")
        self.assertEqual(resp.status_code, 403)

    def test_preview_participant_403(self):
        self.client.force_login(self.participant)
        resp = self.client.get(f"/api/v1/events/{self.event.slug}/results/preview")
        self.assertEqual(resp.status_code, 403)

    def test_preview_anon_401(self):
        resp = self.client.get(f"/api/v1/events/{self.event.slug}/results/preview")
        self.assertEqual(resp.status_code, 401)

    def test_preview_organizer_ok(self):
        self.client.force_login(self.organizer)
        resp = self.client.get(f"/api/v1/events/{self.event.slug}/results/preview")
        # No rubric scores yet, but endpoint accessible; may be 200 with empty rows
        # or 409 if rubric has no criteria.
        self.assertIn(resp.status_code, [200, 409])

    def test_publish_judge_403(self):
        self.client.force_login(self.judge)
        resp = self.client.post(
            f"/api/v1/events/{self.event.slug}/results/publish",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_public_results_anon_404_when_not_published(self):
        resp = self.client.get(f"/api/v1/events/{self.event.slug}/results",
                                follow=False)
        self.assertEqual(resp.status_code, 404)

    def test_audit_judge_403(self):
        self.client.force_login(self.judge)
        resp = self.client.get(f"/api/v1/events/{self.event.slug}/audit")
        self.assertEqual(resp.status_code, 403)

    def test_audit_organizer_200(self):
        self.client.force_login(self.organizer)
        resp = self.client.get(f"/api/v1/events/{self.event.slug}/audit")
        self.assertEqual(resp.status_code, 200)

    def test_audit_action_filter(self):
        self.client.force_login(self.organizer)
        resp = self.client.get(
            f"/api/v1/events/{self.event.slug}/audit?action=results"
        )
        self.assertEqual(resp.status_code, 200)

    def test_feedback_release_requires_organizer(self):
        self.client.force_login(self.judge)
        resp = self.client.post(f"/api/v1/events/{self.event.slug}/feedback-release")
        self.assertEqual(resp.status_code, 403)
