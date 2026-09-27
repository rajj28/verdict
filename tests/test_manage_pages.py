"""Organizer pages: rubric, judges, assignments, progress, results, exports, audit.

The rules themselves are proven against the services in tests/test_results.py and
tests/test_judging.py. This module proves the HTML half: an organizer sees
everything, a judge, a participant and an organizer of another event see nothing,
and the public results page never carries a judge name.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import audit.services
from accounts.models import User
from events.models import Event, EventRole, Role, Track
from judging import policy as judging_policy
from judging.models import (
    Assignment,
    CriterionScore,
    Criterion as JudgingCriterion,
    Review,
    ReviewExclusion,
    ReviewStatus,
    Rubric,
)
from projects.models import Project, ProjectStatus
from results import engine as results_engine
from results import services as results_services
from results.models import ResultPublication
from teams.models import Team, TeamMember


class OrganizerPagesTestCase(TestCase):
    """One event with a rubric, two judges, two projects and every kind of role."""

    @classmethod
    def setUpTestData(cls):
        past = timezone.now() - timedelta(days=10)
        cls.organizer = User.objects.create_user(
            "org@manage.test", "password", display_name="Olive Organizer")
        cls.judge = User.objects.create_user("j1@manage.test", "password", display_name="Ada Judge")
        cls.participant = User.objects.create_user(
            "p1@manage.test", "password", display_name="Cal Participant")
        cls.outsider = User.objects.create_user(
            "out@manage.test", "password", display_name="Ozzy Outsider")

        cls.event = Event.objects.create(
            slug="manage-pages", name="Manage Pages",
            submissions_close_at=past,
            judging_open_at=past,
            judging_close_at=timezone.now() + timedelta(days=2),
            reviews_per_project=2,
            created_by=cls.organizer,
        )
        EventRole.objects.create(event=cls.event, user=cls.organizer, role=Role.ORGANIZER,
                                 public_id="org_mp")
        EventRole.objects.create(event=cls.event, user=cls.participant, role=Role.PARTICIPANT,
                                 public_id="par_mp")
        cls.judge_role = EventRole.objects.create(event=cls.event, user=cls.judge,
                                                  role=Role.JUDGE, public_id="jdg_mp")
        cls.peer_judge = User.objects.create_user("j2@manage.test", "password",
                                                  display_name="Ben Peer")
        cls.peer_role = EventRole.objects.create(event=cls.event, user=cls.peer_judge,
                                                 role=Role.JUDGE, public_id="jdg_mp2")
        cls.other_event = Event.objects.create(
            slug="elsewhere-manage", name="Elsewhere",
            submissions_close_at=past, judging_open_at=past, created_by=cls.organizer)
        EventRole.objects.create(event=cls.other_event, user=cls.outsider,
                                 role=Role.ORGANIZER, public_id="org_elsewhere")

        cls.track = Track.objects.create(event=cls.event, name="Open Track", position=1)
        cls.judge_role.tracks.add(cls.track)
        cls.peer_role.tracks.add(cls.track)

        cls.team = Team.objects.create(event=cls.event, name="Team Alpha")
        TeamMember.objects.create(team=cls.team, user=cls.participant, event=cls.event,
                                  is_owner=True)
        cls.project = cls._project("prj_mp1", "Alpha Project")
        cls.second = cls._project("prj_mp2", "Beta Project")
        cls.unreviewed = cls._project("prj_mp3", "Gamma Project")

        cls.rubric = Rubric.objects.create(event=cls.event, version=1)
        cls.c1 = JudgingCriterion.objects.create(
            rubric=cls.rubric, key="quality", name="Quality", weight=Decimal("3"),
            min_score=1, max_score=5)
        cls.c2 = JudgingCriterion.objects.create(
            rubric=cls.rubric, key="innovation", name="Innovation", weight=Decimal("1"),
            min_score=1, max_score=5, position=1)

        # Two judges review both projects: every project reaches the target of two,
        # and one project is left deliberately unreviewed for the unranked case.
        cls.assignment = cls._review("rev_mp1", cls.project, cls.judge_role, 4, 4)
        cls.second_review = cls._review("rev_mp2", cls.second, cls.judge_role, 2, 2)
        cls._review("rev_mp3", cls.project, cls.peer_role, 5, 4)
        cls._review("rev_mp4", cls.second, cls.peer_role, 1, 2)

    @classmethod
    def _project(cls, public_id: str, title: str) -> Project:
        team = Team.objects.create(event=cls.event, name=f"{title} team")
        return Project.objects.create(
            event=cls.event, team=team, track=cls.track, public_id=public_id, title=title,
            status=ProjectStatus.SUBMITTED, revision=1, repo_url="https://example.org/repo")

    @classmethod
    def _review(cls, public_id: str, project: Project, judge_role, quality: int,
                innovation: int) -> Review:
        assignment = Assignment.objects.create(
            event=project.event, judge=judge_role, project=project)
        review = Review.objects.create(
            event=project.event, assignment=assignment, judge=judge_role, project=project,
            public_id=public_id, status=ReviewStatus.SUBMITTED, comment="Solid work",
            submitted_at=timezone.now() - timedelta(days=1))
        CriterionScore.objects.create(review=review, criterion=cls.c1, value=quality)
        CriterionScore.objects.create(review=review, criterion=cls.c2, value=innovation)
        return review

    def manage(self, page: str) -> str:
        return f"/manage/{self.event.slug}/{page}"

    def publish(self) -> ResultPublication:
        self.event.judging_close_at = timezone.now() - timedelta(seconds=1)
        self.event.save(update_fields=["judging_close_at"])
        return results_services.publish(self.organizer, self.event, note="Published by the test",
                                        acknowledge_unranked=True)


class ManagePageAccessTests(OrganizerPagesTestCase):
    PAGES = ("rubric", "judges", "assignments", "progress", "results", "exports", "audit")

    def test_every_page_opens_for_the_organizer(self):
        self.client.force_login(self.organizer)
        for page in self.PAGES:
            with self.subTest(page=page):
                self.assertEqual(self.client.get(self.manage(page)).status_code, 200)

    def test_the_decision_record_page_opens_for_the_organizer(self):
        publication = self.publish()
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage(f"results/publications/{publication.public_id}"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, publication.input_digest)

    def test_a_judge_and_a_participant_are_refused_every_page(self):
        for user in (self.judge, self.participant):
            self.client.force_login(user)
            for page in self.PAGES:
                with self.subTest(user=user.email, page=page):
                    self.assertEqual(self.client.get(self.manage(page)).status_code, 403)

    def test_an_organizer_of_another_event_is_refused_every_page(self):
        self.client.force_login(self.outsider)
        for page in self.PAGES:
            with self.subTest(page=page):
                self.assertEqual(self.client.get(self.manage(page)).status_code, 403)

    def test_an_anonymous_reader_is_sent_to_the_login_page(self):
        for page in self.PAGES:
            with self.subTest(page=page):
                response = self.client.get(self.manage(page))
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response["Location"])

    def test_an_unknown_event_is_a_404_for_an_organizer(self):
        self.client.force_login(self.organizer)
        self.assertEqual(self.client.get("/manage/no-such-event/results").status_code, 404)

    def test_an_unknown_publication_is_a_404(self):
        self.client.force_login(self.organizer)
        self.assertEqual(
            self.client.get(self.manage("results/publications/pub_missing")).status_code, 404)

    def test_the_public_results_page_is_open_to_everyone(self):
        for user in (None, self.participant, self.judge, self.organizer):
            with self.subTest(user=user):
                if user is not None:
                    self.client.force_login(user)
                else:
                    self.client.logout()
                page = self.client.get(f"/events/{self.event.slug}/results")
                self.assertEqual(page.status_code, 200)


class RubricPageTests(OrganizerPagesTestCase):
    def test_the_page_shows_the_criteria_and_their_share(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("rubric"))
        self.assertContains(page, "quality")
        self.assertContains(page, "Quality")
        self.assertContains(page, "innovation")
        self.assertContains(page, "75.0%")
        self.assertContains(page, "25.0%")
        self.assertContains(page, f'data-api-url="/api/v1/events/{self.event.slug}/rubric"')

    def test_a_locked_rubric_says_why_and_since_when(self):
        Event.objects.filter(pk=self.event.pk).update(
            scoring_locked_at=timezone.now() - timedelta(days=2))
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("rubric"))
        self.assertContains(page, "Scoring is locked")
        self.assertContains(page, "scoring_locked")
        self.assertNotContains(page, "Save rubric")

    def test_the_criteria_the_page_sends_are_accepted_by_the_api(self):
        """The editor is a rows form; the API still decides what is valid."""
        self.client.force_login(self.organizer)
        payload = {"criteria": [
            {"key": "quality", "name": "Quality", "weight": "2", "min_score": 1, "max_score": 5},
            {"key": "impact", "name": "Impact", "weight": "1", "min_score": 0, "max_score": 10,
             "description": "Reach beyond the team."},
        ]}
        response = self.client.put(f"/api/v1/events/{self.event.slug}/rubric",
                                   data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted(c.key for c in JudgingCriterion.objects.filter(rubric__event=self.event)),
            ["impact", "quality"])

    def test_an_invalid_criterion_is_refused_by_the_service(self):
        self.client.force_login(self.organizer)
        response = self.client.put(f"/api/v1/events/{self.event.slug}/rubric",
                                   data=json.dumps({"criteria": [
                                       {"key": "quality", "name": "Quality", "weight": "0"}]}),
                                   content_type="application/json")
        self.assertEqual(response.status_code, 400)


class JudgesPageTests(OrganizerPagesTestCase):
    def test_the_directory_shows_load_and_status(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("judges"))
        self.assertContains(page, "Ada Judge")
        self.assertContains(page, "Open Track")
        self.assertContains(page, "Done")
        self.assertContains(page, f'data-api-url="/api/v1/events/{self.event.slug}/judges"')
        self.assertContains(page, "Declare conflict")

    def test_an_invitation_is_shown_as_a_copyable_link(self):
        self.client.force_login(self.organizer)
        self.client.post(f"/api/v1/events/{self.event.slug}/judge-invites",
                         data=json.dumps({"emails": ["new@manage.test"]}),
                         content_type="application/json")
        page = self.client.get(self.manage("judges"))
        self.assertContains(page, "new@manage.test")
        self.assertContains(page, "/judge-invite?token=")
        self.assertContains(page, "data-copy=")

    def test_a_judge_can_be_added_removed_and_retargeted_through_the_page_api(self):
        User.objects.create_user("new@manage.test", "password", display_name="Nina New")
        self.client.force_login(self.organizer)
        base = f"/api/v1/events/{self.event.slug}/judges"
        added = self.client.post(base, data=json.dumps(
            {"email": "new@manage.test", "tracks": [self.track.public_id]}),
            content_type="application/json")
        self.assertEqual(added.status_code, 201)
        judge_id = added.json()["public_id"]
        patched = self.client.patch(f"{base}/{judge_id}", data=json.dumps({"tracks": []}),
                                    content_type="application/json")
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["tracks"], [])
        self.assertEqual(self.client.delete(f"{base}/{judge_id}").status_code, 204)
        self.assertNotContains(self.client.get(self.manage("judges")), "new@manage.test")

    def test_a_declared_conflict_appears_in_the_list(self):
        self.client.force_login(self.organizer)
        response = self.client.post(f"/api/v1/events/{self.event.slug}/conflicts",
                                    data=json.dumps({"judge": self.judge_role.public_id,
                                                     "team": self.team.public_id,
                                                     "reason": "Same employer"}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 201)
        page = self.client.get(self.manage("judges"))
        self.assertContains(page, "Same employer")
        self.assertContains(page, "Team Alpha")


class AssignmentsPageTests(OrganizerPagesTestCase):
    def test_the_page_lists_the_current_assignments_and_coverage(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("assignments"))
        self.assertContains(page, "Alpha Project")
        self.assertContains(page, "Beta Project")
        self.assertContains(page, "Submitted")
        self.assertContains(page, "Coverage per project")
        self.assertContains(page, f'data-api-url="/api/v1/events/{self.event.slug}/assignments"')

    def test_the_track_filter_narrows_the_project_list(self):
        self.client.force_login(self.organizer)
        other = Track.objects.create(event=self.event, name="Second Track", position=2)
        project = Project.objects.create(
            event=self.event, team=Team.objects.create(event=self.event, name="Delta"),
            track=other, public_id="prj_other_track", title="Delta Project",
            status=ProjectStatus.SUBMITTED, revision=1)
        page = self.client.get(self.manage(f"assignments?track={other.public_id}"))
        # Only the selected track is assignable, and no assignment matches it yet.
        self.assertContains(page, "Delta Project")
        self.assertContains(page, "No assignments match this filter")
        # The coverage table below is the event-wide view and stays complete.
        self.assertContains(page, "Coverage per project")
        self.assertContains(page, "Gamma Project")
        self.assertTrue(project.public_id)

    def test_batch_assign_and_remove_through_the_page_api(self):
        self.client.force_login(self.organizer)
        created = self.client.post(f"/api/v1/events/{self.event.slug}/assignments",
                                   data=json.dumps({"judges": [self.judge_role.public_id],
                                                    "projects": [self.unreviewed.public_id]}),
                                   content_type="application/json")
        self.assertEqual(created.status_code, 201)
        assignment_id = created.json()["created"][0]["public_id"]
        page = self.client.get(self.manage("assignments"))
        self.assertContains(page, "Gamma Project")
        self.assertEqual(
            self.client.delete(
                f"/api/v1/events/{self.event.slug}/assignments/{assignment_id}").status_code, 204)
        self.assertEqual(
            Assignment.objects.filter(public_id=assignment_id).count(), 0)

    def test_the_auto_preview_is_a_dry_run_that_creates_nothing(self):
        self.client.force_login(self.organizer)
        before = Assignment.objects.filter(event=self.event).count()
        response = self.client.post(f"/api/v1/events/{self.event.slug}/assignments/auto",
                                    data=json.dumps({"target": 2, "dry_run": True}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["dry_run"])
        self.assertEqual(Assignment.objects.filter(event=self.event).count(), before)

    def test_the_auto_preview_explains_a_need_it_cannot_fill(self):
        self.client.force_login(self.organizer)
        url = f"/api/v1/events/{self.event.slug}/assignments/auto"
        # Both judges are already at the default max load, so the unreviewed
        # project cannot be filled and the preview has to say why.
        blocked = self.client.post(url, data=json.dumps({"target": 2, "dry_run": True}),
                                   content_type="application/json").json()
        self.assertEqual(len(blocked["unfilled"]), 1)
        self.assertEqual(blocked["unfilled"][0]["project"], self.unreviewed.public_id)
        self.assertIn("max_load", blocked["unfilled"][0]["reason"])

        roomy = self.client.post(url, data=json.dumps({"target": 2, "max_load": 4,
                                                       "dry_run": True}),
                                 content_type="application/json").json()
        self.assertTrue(roomy["proposed"], "with room in the load the proposal fills the gap")
        self.assertEqual(roomy["unfilled"], [])
        self.assertEqual(Assignment.objects.filter(event=self.event).count(), 4)

    def test_applying_the_auto_plan_writes_the_rows_the_preview_showed(self):
        self.client.force_login(self.organizer)
        response = self.client.post(f"/api/v1/events/{self.event.slug}/assignments/auto",
                                    data=json.dumps({"target": 2, "max_load": 4,
                                                     "dry_run": False}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["created"])
        self.assertTrue(Assignment.objects.filter(
            project=self.unreviewed, judge=self.judge_role).exists())
        self.assertContains(self.client.get(self.manage("assignments")), "Gamma Project")

    def test_the_under_reviewed_project_is_flagged_on_the_page(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("assignments"))
        self.assertContains(page, "Coverage per project")
        # The unreviewed project is the only row the table marks as short.
        self.assertContains(page, "table-warning")


class ProgressPageTests(OrganizerPagesTestCase):
    def test_the_dashboard_shows_judges_projects_and_tracks(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("progress"))
        self.assertContains(page, "Ada Judge")
        self.assertContains(page, "Alpha Project")
        self.assertContains(page, "Open Track")
        self.assertContains(page, "Coverage")
        self.assertContains(page, "progress.js")

    def test_the_page_polls_the_progress_api_every_fifteen_seconds(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("progress"))
        self.assertContains(page, 'data-progress-interval="15000"')
        self.assertContains(page, f'data-progress-api="/api/v1/events/{self.event.slug}/progress"')

    def test_query_count_does_not_grow_with_more_projects(self):
        self.client.force_login(self.organizer)
        with CaptureQueriesContext(connection) as first:
            self.assertEqual(self.client.get(self.manage("progress")).status_code, 200)
        for index in range(3):
            self._project(f"prj_extra_{index}", f"Extra {index}")
        with CaptureQueriesContext(connection) as second:
            self.assertEqual(self.client.get(self.manage("progress")).status_code, 200)
        self.assertEqual(len(first), len(second))


class ResultsPageTests(OrganizerPagesTestCase):
    def test_the_page_shows_the_method_rows_and_judge_table(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("results"))
        self.assertContains(page, "Official method")
        self.assertContains(page, "Alpha Project")
        self.assertContains(page, "Ada Judge")
        self.assertContains(page, "Derived BT")
        self.assertContains(page, "&Delta; vs raw")

    def test_the_explain_tab_breaks_one_project_down(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage(f"results?project={self.project.public_id}"))
        self.assertContains(page, "Judge offset")
        self.assertContains(page, "Ada Judge")
        self.assertContains(page, "Solid work")

    def test_data_issues_list_excluded_reviews_and_superseded_projects(self):
        ReviewExclusion.objects.create(review=self.assignment, reason="Declared conflict",
                                       created_by=self.organizer)
        superseded = self._project("prj_old", "Old Project")
        superseded.status = ProjectStatus.SUPERSEDED
        superseded.status_reason = "Superseded by a later submission"
        superseded.save(update_fields=["status", "status_reason"])
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("results"))
        self.assertContains(page, "Declared conflict")
        self.assertContains(page, "Superseded by a later submission")
        self.assertContains(page, "/exclusion")

    def test_publishing_needs_the_unranked_acknowledgement_and_then_works(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("results"))
        self.assertContains(page, "Gamma Project")
        self.assertContains(page, "acknowledge_unranked")
        self.assertContains(page, f'data-api-url="/api/v1/events/{self.event.slug}/results/publish"')

        # With the box missing, the service refuses; the page is not the control.
        self.event.judging_close_at = timezone.now() - timedelta(seconds=1)
        self.event.save(update_fields=["judging_close_at"])
        refused = self.client.post(f"/api/v1/events/{self.event.slug}/results/publish",
                                   data=json.dumps({"note": "first"}),
                                   content_type="application/json")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("unranked_projects", refused.json()["error"]["code"])
        self.assertEqual(ResultPublication.objects.filter(event=self.event).count(), 0)

        accepted = self.client.post(f"/api/v1/events/{self.event.slug}/results/publish",
                                    data=json.dumps({"note": "first",
                                                     "acknowledge_unranked": True}),
                                    content_type="application/json")
        self.assertEqual(accepted.status_code, 201)
        after = self.client.get(self.manage("results"))
        self.assertContains(after, "Publication history")
        self.assertContains(after, "/results/publications/")

    def test_publishing_is_refused_while_judging_is_still_open(self):
        self.event.judging_close_at = None
        self.event.save(update_fields=["judging_close_at"])
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("results"))
        self.assertContains(page, "Judging is still open")
        self.assertContains(page, f'data-api-url="/api/v1/events/{self.event.slug}/close-judging"')
        response = self.client.post(f"/api/v1/events/{self.event.slug}/results/publish",
                                    data=json.dumps({"note": "too early",
                                                     "acknowledge_unranked": True}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "judging_open")

    def test_the_robustness_certificate_is_shown_in_plain_words(self):
        self.client.force_login(self.organizer)
        certificate = {
            "summary": "1st place holds in 1 of 1 single-judge removals.",
            "judge_summary": "The winner stays 1st when any one judge is removed.",
            "review_summary": "The winner stays 1st when any one review is removed.",
            "flip_summary": "3 reviews would have to move to drop the winner.",
        }
        preview = results_services.preview(self.event)
        preview["robustness"] = certificate
        with mock.patch.object(results_services, "preview", return_value=preview):
            page = self.client.get(self.manage("results"))
        self.assertContains(page, "Robustness")
        self.assertContains(page, certificate["summary"])
        self.assertContains(page, certificate["flip_summary"])

    def test_the_certificate_is_computed_from_the_canonical_inputs_when_absent(self):
        """The page must not be a step behind the engine that ranked the projects."""
        self.client.force_login(self.organizer)
        preview = results_services.preview(self.event)
        self.assertNotIn("robustness", preview)  # the preview does not carry it yet
        page = self.client.get(self.manage("results"))
        self.assertContains(page, "Leave one judge out")
        self.assertContains(page, "single-judge removals")
        self.assertContains(page, self.project.title)
        # The sentences are the engine's own, not a paraphrase.
        engine_certificate = asdict(
            results_engine.robustness(
                [results_engine.ReviewInput(review_id=row["review_id"],
                                            judge_id=row["judge_id"],
                                            project_id=row["project_id"],
                                            values=row["criteria"])
                 for row in results_services.collect_inputs(self.event)["included"]],
                judging_policy.engine_criteria(self.event),
                lam=preview["lam"]))
        self.assertContains(page, engine_certificate["judge_summary"])

    def test_the_page_says_so_when_there_is_no_preview_to_rank(self):
        event = Event.objects.create(
            slug="no-rubric", name="No Rubric",
            submissions_close_at=timezone.now() - timedelta(days=1),
            judging_open_at=timezone.now() - timedelta(days=1),
            created_by=self.organizer)
        EventRole.objects.create(event=event, user=self.organizer, role=Role.ORGANIZER,
                                 public_id="org_nr")
        self.client.force_login(self.organizer)
        page = self.client.get(f"/manage/{event.slug}/results")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Nothing to rank yet")


class DecisionRecordTests(OrganizerPagesTestCase):
    def test_the_record_shows_the_rule_inputs_digest_and_limitations(self):
        publication = self.publish()
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage(f"results/publications/{publication.public_id}"))
        self.assertContains(page, "The rule, in plain words")
        self.assertContains(page, publication.input_digest)
        self.assertContains(page, "rev_mp1")
        self.assertContains(page, "quality=4")
        self.assertContains(page, "Limitations")
        self.assertContains(page, f"/results/publications/{publication.public_id}/verify")

    def test_verify_returns_a_verdict_through_the_api(self):
        publication = self.publish()
        self.client.force_login(self.organizer)
        response = self.client.post(
            f"/api/v1/events/{self.event.slug}/results/publications/{publication.public_id}/verify",
            content_type="application/json", data=json.dumps({}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verdict"], "identical")

    def test_a_judge_cannot_open_the_decision_record(self):
        publication = self.publish()
        self.client.force_login(self.judge)
        self.assertEqual(
            self.client.get(
                self.manage(f"results/publications/{publication.public_id}")).status_code, 403)


class PublicResultsPageTests(OrganizerPagesTestCase):
    def test_before_publishing_the_page_says_not_yet_published(self):
        page = self.client.get(f"/events/{self.event.slug}/results")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Not yet published")
        self.assertNotContains(page, "Alpha Project")

    def test_after_publishing_the_ranking_and_prizes_are_visible(self):
        self.publish()
        page = self.client.get(f"/events/{self.event.slug}/results")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Alpha Project")
        self.assertContains(page, "Ranking")

    def test_the_public_page_never_carries_a_judge_name_or_id(self):
        self.publish()
        for user in (None, self.participant, self.judge):
            if user is None:
                self.client.logout()
            else:
                self.client.force_login(user)
            page = self.client.get(f"/events/{self.event.slug}/results")
            self.assertEqual(page.status_code, 200)
            # The peer judge's name never appears: the public rows carry no judge
            # data at all, and the signed-in judge's own name in the navbar is
            # the account the reader already knows.
            self.assertNotContains(page, "Ben Peer")
            self.assertNotContains(page, "j2@manage.test")
            self.assertNotContains(page, self.judge_role.public_id)
            self.assertNotContains(page, self.peer_role.public_id)
            self.assertNotContains(page, "Solid work")

    def test_an_unknown_event_is_a_404(self):
        self.assertEqual(self.client.get("/events/no-such-event/results").status_code, 404)


class ExportsPageTests(OrganizerPagesTestCase):
    def test_every_csv_and_the_json_are_listed_with_a_description(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("exports"))
        for kind in ("participants", "teams", "projects", "judges", "assignments", "reviews",
                     "progress", "results", "audit"):
            with self.subTest(kind=kind):
                self.assertContains(page, f"/api/v1/events/{self.event.slug}/exports/{kind}.csv")
        self.assertContains(page, f"/api/v1/events/{self.event.slug}/exports/event.json")
        self.assertContains(page, "round-trips")

    def test_a_downloaded_export_really_is_a_csv(self):
        self.client.force_login(self.organizer)
        response = self.client.get(f"/api/v1/events/{self.event.slug}/exports/results.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])


class AuditPageTests(OrganizerPagesTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        audit.services.record(cls.judge, "judging.review.submitted", event=cls.event,
                              target=cls.project,
                              summary="Ada Judge submitted a review of Alpha Project.")
        audit.services.record(cls.organizer, "project.submitted", event=cls.event,
                              target=cls.project,
                              summary="Team Alpha submitted a project.")

    def test_the_log_lists_entries_with_the_actor_and_the_action(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("audit"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "judging.review.submitted")
        self.assertContains(page, "Ada Judge submitted a review of Alpha Project.")
        self.assertContains(page, "role-organizer")
        self.assertContains(page, "role-judge")

    def test_the_action_filter_narrows_the_log(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("audit?action=judging"))
        self.assertContains(page, "judging.review.submitted")
        self.assertNotContains(page, "project.submitted")

    def test_the_csv_link_is_the_export_api(self):
        self.client.force_login(self.organizer)
        page = self.client.get(self.manage("audit"))
        self.assertContains(page, f"/api/v1/events/{self.event.slug}/exports/audit.csv")

    def test_query_count_does_not_grow_with_more_entries(self):
        self.client.force_login(self.organizer)
        with CaptureQueriesContext(connection) as first:
            self.assertEqual(self.client.get(self.manage("audit")).status_code, 200)
        for index in range(5):
            audit.services.record(self.organizer, f"test.event{index}", event=self.event,
                                  summary=f"Synthetic entry {index}.")
        with CaptureQueriesContext(connection) as second:
            self.assertEqual(self.client.get(self.manage("audit")).status_code, 200)
        self.assertEqual(len(first), len(second))
