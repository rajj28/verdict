"""The Decision Room is evidence-backed, private and strictly read-only."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from django.utils import timezone

from audit.models import AuditEvent
from community.models import VotingConfig
from core.decisions import decision_room
from core.errors import ApiError
from events.models import Event, EventRole, Prize, Role
from judging.models import CriterionScore, ReviewExclusion
from results import services
from results.models import ResultPublication
from tests.test_results import ResultsTestCase


class DecisionRoomTests(ResultsTestCase):
    def report(self):
        return decision_room(self.organizer, self.event.slug)

    def codes(self):
        return {row["code"] for row in self.report()["findings"]}

    def test_service_denies_anonymous_judge_participant_and_other_organizer(self):
        other = Event.objects.create(slug="other-room", name="Other",
                                     submissions_close_at=timezone.now(), created_by=self.other_user)
        EventRole.objects.create(event=other, user=self.other_user, role=Role.ORGANIZER,
                                 public_id="org_other_room")
        for user in (AnonymousUser(), self.judge1, self.participant_user, self.other_user):
            with self.subTest(user=user), self.assertRaises(ApiError) as caught:
                decision_room(user, self.event.slug)
            self.assertIn(caught.exception.status_code, {401, 403})

    def test_default_report_has_real_coverage_counts_and_no_writes(self):
        before = AuditEvent.objects.count()
        report = self.report()
        rows = {row["project"]: row for row in report["coverage"]}
        self.assertEqual(rows[self.project1.public_id]["reviews"], 2)
        self.assertEqual(rows[self.project2.public_id]["reviews"], 1)
        self.assertEqual(report["method"], self.event.ranking_method)
        self.assertEqual(report["preview_digest"], services.preview(self.event)["input_digest"])
        self.assertEqual(AuditEvent.objects.count(), before)
        self.assertIn("coverage_shortfall", self.codes())

    def test_excluded_review_never_counts_as_coverage(self):
        ReviewExclusion.objects.create(review=self.review2_1, reason="Withdrawn conflict",
                                       created_by=self.organizer)
        rows = {row["project"]: row for row in self.report()["coverage"]}
        self.assertEqual(rows[self.project1.public_id]["reviews"], 1)

    def test_deadlines_use_half_open_close_boundary(self):
        boundary = timezone.now()
        Event.objects.filter(pk=self.event.pk).update(judging_close_at=boundary)
        with patch("core.decisions.now", return_value=boundary - timedelta(microseconds=1)):
            self.assertIn("judging_open", self.codes())
        with patch("core.decisions.now", return_value=boundary):
            self.assertNotIn("judging_open", self.codes())

    def test_configured_voting_without_dates_blocks_readiness(self):
        VotingConfig.objects.create(event=self.event)
        self.assertIn("voting_open", self.codes())

    def test_missing_rubric_is_actionable_not_server_error(self):
        self.rubric.delete()
        report = self.report()
        self.assertEqual(report["state"], "blocked")
        self.assertIsNone(report["preview_digest"])
        self.assertIn("no_rubric", self.codes())

    def test_shared_prize_tie_is_not_silently_resolved(self):
        Event.objects.filter(pk=self.event.pk).update(ranking_method="raw")
        CriterionScore.objects.filter(review__event=self.event).update(value=4)
        Prize.objects.create(event=self.event, name="Grand Prize", public_id="prz_room")
        self.assertIn("prize_ties", self.codes())

    def test_publication_is_not_verified_and_links_to_real_decision_record(self):
        publication = services.publish(self.organizer, self.event)
        with patch("results.services.verify_publication") as mocked:
            report = self.report()
            self.assertEqual(report["publication"]["verification"], "not_run")
            finding = next(row for row in report["findings"] if row["code"] == "publication_unverified")
            self.assertNotIn(finding["title"].lower(), {"identical", "verified", "ready"})
            match = resolve(finding["action"]["href"])
            self.assertEqual(match.url_name, "manage-decision-record")
            self.assertEqual(match.kwargs["slug"], self.event.slug)
            self.assertEqual(match.kwargs["pub_id"], publication.public_id)
            self.assertEqual(report["state"], "review")
        mocked.assert_not_called()

    def test_publication_not_verified_via_json_and_html_endpoints(self):
        services.publish(self.organizer, self.event)
        self.client.force_login(self.organizer)
        with patch("results.services.verify_publication") as mocked_api:
            api_response = self.client.get(f"/api/v1/events/{self.event.slug}/decision-room")
            self.assertEqual(api_response.status_code, 200)
            self.assertEqual(api_response.json()["publication"]["verification"], "not_run")
            mocked_api.assert_not_called()
        with patch("results.services.verify_publication") as mocked_html:
            page_response = self.client.get(f"/manage/{self.event.slug}/decision-room")
            self.assertContains(page_response, "not_run")
            self.assertNotContains(page_response, "verification: identical")
            mocked_html.assert_not_called()

    def test_malformed_publication_rows_and_params_do_not_crash_room(self):
        publication = services.publish(self.organizer, self.event)
        ResultPublication.objects.filter(pk=publication.pk).update(rows=17, params="not-a-dict")
        report = self.report()
        self.assertEqual(report["publication"]["verification"], "not_run")
        self.assertIsNone(report["publication"]["row_count"])
        self.assertIsNone(report["publication"]["component_hint"])
        self.assertIsNotNone(report["preview_digest"])
        self.client.force_login(self.organizer)
        self.assertContains(self.client.get(f"/manage/{self.event.slug}/decision-room"), "Decision Room")

    def test_pairwise_does_not_invent_rubric_coverage_requirement(self):
        real_preview = services.preview(self.event)
        real_preview["method"] = "pairwise"
        with patch("core.decisions.services.preview", return_value=real_preview):
            self.assertNotIn("coverage_shortfall", self.codes())

    def test_api_and_page_private_and_share_same_findings(self):
        api = f"/api/v1/events/{self.event.slug}/decision-room"
        page = f"/manage/{self.event.slug}/decision-room"
        self.assertEqual(self.client.get(api).status_code, 401)
        self.assertEqual(self.client.get(page).status_code, 302)
        self.client.force_login(self.judge1)
        self.assertEqual(self.client.get(api).status_code, 403)
        self.assertEqual(self.client.get(page).status_code, 403)
        self.client.force_login(self.organizer)
        response = self.client.get(api)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(self.client.get(page), "Decision Room")
        self.assertEqual(self.client.get("/api/v1/events/absent-room/decision-room").status_code, 404)
        self.assertEqual(self.client.post(api, {}).status_code, 405)

    def test_query_count_does_not_grow_per_project(self):
        from projects.models import Project
        from teams.models import Team
        with CaptureQueriesContext(connection) as first:
            self.report()
        for index in range(6):
            team = Team.objects.create(event=self.event, name=f"Room team {index}")
            Project.objects.create(event=self.event, team=team, track=self.track,
                                   title=f"Room project {index}", status="submitted")
        with CaptureQueriesContext(connection) as second:
            self.report()
        self.assertEqual(len(first), len(second))
        self.assertLessEqual(len(second), 40)
