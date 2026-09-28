"""Judge console pages: own queue only, own evidence, no cross-judge leakage.

The API side of the same rules is proven in tests/test_isolation.py; this module
proves the HTML pages enforce them too, because a page must never be the weaker
half of a rule.
"""
import json
import re
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import User
from events.models import CustomQuestion, Event, EventRole, Role, Track
from judging import policy
from judging.models import (Assignment, Criterion, CriterionScore, JudgeInvite, Review,
                            ReviewStatus, Rubric)
from projects.models import Answer, Project, ProjectStatus
from teams.models import Team


class JudgePageTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.judge = User.objects.create_user("judge@ex.org", "password", display_name="Ada Judge")
        self.other_judge = User.objects.create_user(
            "peer@ex.org", "password", display_name="Bo Peer")
        self.participant = User.objects.create_user("participant@ex.org", "password",
                                                    display_name="Cal Participant")
        self.organizer = User.objects.create_user("organizer@ex.org", "password")
        self.event = Event.objects.create(
            slug="judge-pages", name="Judge Pages",
            submissions_close_at=now - timedelta(days=2),
            judging_open_at=now - timedelta(days=2),
            judging_close_at=now + timedelta(days=2),
            created_by=self.organizer,
        )
        EventRole.objects.create(event=self.event, user=self.organizer, role=Role.ORGANIZER,
                                 public_id="org_pages")
        EventRole.objects.create(event=self.event, user=self.participant, role=Role.PARTICIPANT,
                                 public_id="par_pages")
        self.role = EventRole.objects.create(event=self.event, user=self.judge, role=Role.JUDGE,
                                             public_id="jdg_pages")
        self.other_role = EventRole.objects.create(event=self.event, user=self.other_judge,
                                                   role=Role.JUDGE, public_id="jdg_peer")
        self.track = Track.objects.create(event=self.event, name="Track A")
        self.other_track = Track.objects.create(event=self.event, name="Track B")
        self.role.tracks.add(self.track)
        self.other_role.tracks.add(self.other_track)
        self.project = self._project("prj_pages", "Glass Signal", self.track, "NorthKiln")
        self.queued = self._project("prj_queued", "Copper Kettle", self.track, "Brass Co")
        self.peer_project = self._project("prj_peer", "Peer Project", self.other_track, "Peer Co")
        self.assignment = Assignment.objects.create(
            event=self.event, judge=self.role, project=self.project, public_id="asg_pages")
        self.queued_assignment = Assignment.objects.create(
            event=self.event, judge=self.role, project=self.queued)
        self.peer_assignment = Assignment.objects.create(
            event=self.event, judge=self.other_role, project=self.peer_project)
        self.peer_review = Review.objects.create(
            event=self.event, assignment=self.peer_assignment, judge=self.other_role,
            project=self.peer_project, public_id="rev_peer", status=ReviewStatus.SUBMITTED,
        )
        rubric = Rubric.objects.create(event=self.event)
        self.criterion = Criterion.objects.create(
            rubric=rubric, key="quality", name="Quality", description="How solid it is.",
            weight="3", min_score=1, max_score=5,
        )
        Criterion.objects.create(rubric=rubric, key="impact", name="Impact", weight="1",
                                 min_score=1, max_score=5, position=1)
        self.question = CustomQuestion.objects.create(
            event=self.event, prompt="What is your secret sauce?", is_public=False)
        Answer.objects.create(project=self.project, question=self.question,
                              value="Undisclosed method")
        Answer.objects.create(project=self.peer_project, question=self.question,
                              value="Peer secret sauce")
        self.review_url = f"/judge/{self.event.slug}/review/{self.project.public_id}"

    def _project(self, public_id, title, track, team_name) -> Project:
        return Project.objects.create(
            event=self.event, team=Team.objects.create(event=self.event, name=team_name),
            track=track, public_id=public_id, title=title, summary=f"{title} in one line",
            description=f"{title} description.", repo_url="https://example.org/repo",
            status=ProjectStatus.SUBMITTED, revision=2,
        )

    def review_path(self, project: Project) -> str:
        return f"/judge/{self.event.slug}/review/{project.public_id}"

    def test_console_lists_only_the_callers_own_assignments(self):
        self.client.force_login(self.judge)
        page = self.client.get("/judge")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Glass Signal")
        self.assertContains(page, "Copper Kettle")
        self.assertNotContains(page, "Peer Project")
        self.assertContains(page, "of 2 reviews submitted")

    def test_console_continues_at_the_first_unreviewed_assignment(self):
        Review.objects.create(
            event=self.event, assignment=self.queued_assignment, judge=self.role,
            project=self.queued, public_id="rev_queued", status=ReviewStatus.DRAFT)
        self.client.force_login(self.judge)
        page = self.client.get("/judge")
        self.assertContains(page, self.review_path(self.project))
        self.assertContains(page, self.review_path(self.queued))
        self.assertContains(page, "Draft saved")
        self.assertContains(page, "Not started")

    def test_console_shows_the_judging_deadline(self):
        self.client.force_login(self.judge)
        page = self.client.get("/judge")
        self.assertContains(page, "data-countdown")

    def test_anonymous_readers_are_sent_to_the_login_page(self):
        queue = self.client.get("/judge")
        self.assertEqual(queue.status_code, 302)
        self.assertIn("/login", queue["Location"])
        review = self.client.get(self.review_path(self.project))
        self.assertEqual(review.status_code, 302)
        self.assertIn("/login", review["Location"])

    def test_participants_and_organizers_are_refused_the_console(self):
        for user in (self.participant, self.organizer):
            with self.subTest(user=user.email):
                self.client.force_login(user)
                self.assertEqual(self.client.get("/judge").status_code, 403)

    def test_review_page_shows_evidence_and_the_rubric(self):
        self.client.force_login(self.judge)
        page = self.client.get(self.review_path(self.project))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Glass Signal in one line")
        self.assertContains(page, "Glass Signal description.")
        self.assertContains(page, "https://example.org/repo")
        self.assertContains(page, "NorthKiln")
        self.assertContains(page, "Track A")
        self.assertContains(page, "revision 2")
        # Private custom answers reach the assigned judge and nobody else.
        self.assertContains(page, "Undisclosed method")
        self.assertContains(page, "Quality")
        self.assertContains(page, "Impact")
        self.assertContains(page, "75.0% of the score")
        self.assertContains(page, f"data-api-url=\"/api/v1/events/{self.event.slug}"
                                  f"/judge/reviews/{self.project.public_id}\"")
        self.assertContains(page, "/submit")

    def test_review_page_is_refused_for_unassigned_and_out_of_track_projects(self):
        self.client.force_login(self.judge)
        self.assertEqual(self.client.get(self.review_path(self.peer_project)).status_code, 403)
        # An assignment in the wrong track is still refused: the service re-checks
        # the track, and the page refuses on the same rule.
        Assignment.objects.create(event=self.event, judge=self.role, project=self.peer_project)
        self.assertEqual(self.client.get(self.review_path(self.peer_project)).status_code, 403)

    def test_review_page_is_a_404_for_an_unknown_project(self):
        self.client.force_login(self.judge)
        self.assertEqual(
            self.client.get(f"/judge/{self.event.slug}/review/prj_missing").status_code, 404)

    def test_a_participant_cannot_open_any_review_page(self):
        self.client.force_login(self.participant)
        for project in (self.project, self.peer_project):
            with self.subTest(project=project.public_id):
                self.assertEqual(self.client.get(self.review_path(project)).status_code, 403)

    def test_a_judge_of_another_event_cannot_open_this_review(self):
        elsewhere = Event.objects.create(
            slug="elsewhere", name="Elsewhere",
            submissions_close_at=timezone.now() - timedelta(days=1),
            judging_open_at=timezone.now() - timedelta(days=1),
            judging_close_at=timezone.now() + timedelta(days=1),
            created_by=self.organizer,
        )
        EventRole.objects.create(event=elsewhere, user=self.other_judge, role=Role.JUDGE,
                                 public_id="jdg_elsewhere")
        self.client.force_login(self.other_judge)
        self.assertEqual(self.client.get(self.review_path(self.project)).status_code, 403)

    def test_no_other_judges_name_comment_or_score_reaches_a_judge_page(self):
        CriterionScore.objects.create(review=self.peer_review, criterion=self.criterion, value=5)
        self.client.force_login(self.judge)
        pages = [self.client.get("/judge"), self.client.get(self.review_path(self.project)),
                 self.client.get(self.review_path(self.queued))]
        for page in pages:
            self.assertEqual(page.status_code, 200)
            self.assertNotContains(page, "Bo Peer")
            self.assertNotContains(page, "peer@ex.org")
            self.assertNotContains(page, "Peer secret sauce")
            self.assertNotContains(page, "jdg_peer")

    def test_review_page_is_read_only_once_judging_has_closed(self):
        self.event.judging_close_at = timezone.now() - timedelta(seconds=1)
        self.event.save(update_fields=["judging_close_at"])
        self.client.force_login(self.judge)
        page = self.client.get(self.review_path(self.project))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Judging is closed")
        self.assertContains(page, 'data-readonly="1"')
        self.assertContains(page, "disabled")
        # The service refuses the write even though the controls are rendered.
        self.assertEqual(self.client.put(
            f"/api/v1/events/{self.event.slug}/judge/reviews/{self.project.public_id}",
            data=json.dumps({"scores": {"quality": 4}, "comment": "late"}),
            content_type="application/json",
        ).status_code, 403)

    def test_review_page_links_to_the_neighbouring_assignments(self):
        # Neighbours follow the per-judge queue order, not creation or title
        # order (packet Q1): derive the expected sequence from the same helper
        # the views use.
        self.client.force_login(self.judge)
        ordered = [row.project for row in policy.order_judge_queue(list(
            Assignment.objects.filter(event=self.event, judge=self.role)
            .select_related("event", "judge", "project").prefetch_related("review")))]
        self.assertEqual(len(ordered), 2)
        first, second = ordered
        page = self.client.get(self.review_path(first))
        self.assertNotContains(page, 'rel="prev"')
        self.assertContains(page, f'href="{self.review_path(second)}" rel="next"')
        last = self.client.get(self.review_path(second))
        self.assertContains(last, f'href="{self.review_path(first)}" rel="prev"')
        # The last assignment has no next link to follow.
        self.assertNotContains(last, 'rel="next"')

    def test_review_page_prefills_the_callers_own_draft(self):
        Review.objects.create(
            event=self.event, assignment=self.assignment, judge=self.role, project=self.project,
            public_id="rev_mine", status=ReviewStatus.DRAFT, comment="Promising work",
        )
        CriterionScore.objects.create(review=Review.objects.get(public_id="rev_mine"),
                                      criterion=self.criterion, value=4)
        self.client.force_login(self.judge)
        page = self.client.get(self.review_path(self.project))
        self.assertContains(page, "Promising work")
        self.assertContains(page, 'id="score-quality-4"')
        self.assertContains(page, "&quot;quality&quot;: 4")
        self.assertContains(page, "Draft saved")

    def test_console_query_count_does_not_grow_per_assignment(self):
        self.client.force_login(self.judge)
        with CaptureQueriesContext(connection) as two_assignments:
            self.assertEqual(self.client.get("/judge").status_code, 200)
        for index in range(3):
            project = self._project(f"prj_extra_{index}", f"Extra {index}", self.track,
                                    f"Extra team {index}")
            Assignment.objects.create(event=self.event, judge=self.role, project=project)
        with CaptureQueriesContext(connection) as five_assignments:
            self.assertEqual(self.client.get("/judge").status_code, 200)
        self.assertEqual(len(two_assignments), len(five_assignments))

    def test_a_judge_without_assignments_gets_an_empty_queue(self):
        Assignment.objects.filter(event=self.event, judge=self.role).delete()
        self.client.force_login(self.judge)
        page = self.client.get("/judge")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "No assignments yet")

    def test_the_payload_the_page_saves_and_submits_is_accepted(self):
        self.client.force_login(self.judge)
        review_path = f"/api/v1/events/{self.event.slug}/judge/reviews/{self.project.public_id}"
        # Exactly what the rubric form serializes: one scores object plus the comment.
        draft = self.client.put(review_path, data=json.dumps(
            {"scores": {"quality": 4, "impact": 2}, "comment": "Solid demo."}),
            content_type="application/json")
        self.assertEqual(draft.status_code, 200)
        self.assertEqual(draft.json()["status"], "draft")
        partial = self.client.post(f"{review_path}/submit",
                                   data=json.dumps({"scores": {"quality": 4}, "comment": ""}),
                                   content_type="application/json")
        self.assertEqual(partial.status_code, 400)
        self.assertIn("impact", json.dumps(partial.json()["error"]["fields"]))
        submitted = self.client.post(f"{review_path}/submit", data=json.dumps(
            {"scores": {"quality": 4, "impact": 2}, "comment": "Solid demo."}),
            content_type="application/json")
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(submitted.json()["status"], "submitted")
        self.assertContains(self.client.get("/judge"), "Submitted")
        self.assertContains(self.client.get(self.review_path(self.project)),
                            "Review submitted")


class PerJudgeQueueOrderTests(TestCase):
    """The judge queue follows a per-judge deterministic order (packet Q1).

    Sort key ``sha256("{event.slug}:{judge.public_id}:{project.public_id}")``,
    to-do before submitted. Two judges with the same projects get different
    orders; one judge always gets the same order on the page and the API.
    """

    PROJECT_IDS = ("prj_q1", "prj_q2", "prj_q3", "prj_q4", "prj_q5")

    def setUp(self):
        now = timezone.now()
        self.organizer = User.objects.create_user("queue-organizer@ex.org", "password")
        self.judge_a = User.objects.create_user("queue-a@ex.org", "password")
        self.judge_b = User.objects.create_user("queue-b@ex.org", "password")
        self.event = Event.objects.create(
            slug="queue-order", name="Queue Order",
            submissions_close_at=now - timedelta(days=2),
            judging_open_at=now - timedelta(days=2),
            judging_close_at=now + timedelta(days=2),
            created_by=self.organizer,
        )
        track = Track.objects.create(event=self.event, name="Track A")
        self.role_a = EventRole.objects.create(
            event=self.event, user=self.judge_a, role=Role.JUDGE, public_id="jdg_q_a")
        self.role_b = EventRole.objects.create(
            event=self.event, user=self.judge_b, role=Role.JUDGE, public_id="jdg_q_b")
        self.role_a.tracks.add(track)
        self.role_b.tracks.add(track)
        titles = {"prj_q1": "Queue One", "prj_q2": "Queue Two", "prj_q3": "Queue Three",
                  "prj_q4": "Queue Four", "prj_q5": "Queue Five"}
        self.projects = {}
        for public_id in self.PROJECT_IDS:
            self.projects[public_id] = Project.objects.create(
                event=self.event, team=Team.objects.create(event=self.event, name=f"Team {public_id}"),
                track=track, public_id=public_id, title=titles[public_id],
                summary=f"{titles[public_id]} in one line", description="Description.",
                repo_url="https://example.org/repo",
                status=ProjectStatus.SUBMITTED, revision=1,
            )
        for role in (self.role_a, self.role_b):
            for public_id in self.PROJECT_IDS:
                Assignment.objects.create(event=self.event, judge=role,
                                          project=self.projects[public_id])

    def _expected_order(self, role) -> list[str]:
        return sorted(self.PROJECT_IDS, key=lambda pid: policy.review_order_key(
            self.event.slug, role.public_id, pid))

    def _page_order(self, user) -> list[str]:
        self.client.force_login(user)
        content = self.client.get("/judge").content.decode()
        hrefs = re.findall(r"/judge/queue-order/review/(\S+?)\"", content)
        return list(dict.fromkeys(hrefs))

    def _api_order(self, user) -> list[str]:
        self.client.force_login(user)
        response = self.client.get("/api/v1/judge/assignments")
        self.assertEqual(response.status_code, 200)
        return [row["project"]["public_id"] for row in response.data["assignments"]]

    def test_two_judges_with_the_same_projects_see_different_orders(self):
        order_a = self._page_order(self.judge_a)
        order_b = self._page_order(self.judge_b)
        # Each judge's page follows their own hash order, which is neither
        # title order nor the other judge's order for these ids.
        self.assertEqual(order_a, self._expected_order(self.role_a))
        self.assertEqual(order_b, self._expected_order(self.role_b))
        self.assertNotEqual(order_a, order_b)
        title_order = sorted(self.PROJECT_IDS,
                             key=lambda pid: self.projects[pid].title)
        self.assertNotEqual(order_a, title_order)
        self.assertNotEqual(order_b, title_order)

    def test_one_judges_order_is_stable_across_page_and_api(self):
        first = self._page_order(self.judge_a)
        self.assertEqual(self._page_order(self.judge_a), first)
        self.assertEqual(self._api_order(self.judge_a), first)
        self.assertEqual(first, self._expected_order(self.role_a))

    def test_todo_comes_before_done_and_every_project_appears_once(self):
        initial = self._page_order(self.judge_a)
        submitted_pid = initial[0]
        assignment = Assignment.objects.get(
            event=self.event, judge=self.role_a, project=self.projects[submitted_pid])
        Review.objects.create(event=self.event, assignment=assignment, judge=self.role_a,
                              project=self.projects[submitted_pid], status=ReviewStatus.SUBMITTED)
        after = self._page_order(self.judge_a)
        # Every project exactly once, the submitted review moved to the end,
        # and the remaining to-do rows keep their per-judge order.
        self.assertEqual(sorted(after), sorted(self.PROJECT_IDS))
        self.assertEqual(after[-1], submitted_pid)
        self.assertEqual(after[:-1], [pid for pid in initial if pid != submitted_pid])
        api_order = self._api_order(self.judge_a)
        self.assertEqual(sorted(api_order), sorted(self.PROJECT_IDS))
        self.assertEqual(len(set(api_order)), len(self.PROJECT_IDS))
        self.assertEqual(api_order[-1], submitted_pid)


class JudgeInvitePageTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.organizer = User.objects.create_user("host@ex.org", "password")
        self.invited = User.objects.create_user("invited@ex.org", "password")
        self.someone_else = User.objects.create_user("other@ex.org", "password")
        self.event = Event.objects.create(
            slug="invite-event", name="Invite Event",
            submissions_close_at=now - timedelta(days=1),
            judging_open_at=now - timedelta(days=1),
            created_by=self.organizer,
        )
        self.track = Track.objects.create(event=self.event, name="Track A")
        self.invite = JudgeInvite.objects.create(
            event=self.event, email=self.invited.email, created_by=self.organizer,
            expires_at=now + timedelta(days=3))
        self.invite.tracks.add(self.track)
        self.url = f"/judge-invite?token={self.invite.token}"

    def test_an_anonymous_reader_must_sign_in_first(self):
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Sign in to accept")
        self.assertContains(page, "/login?next=")
        # The address is recognisable but never printed in full.
        self.assertContains(page, "i***@ex.org")
        self.assertNotContains(page, self.invited.email)

    def test_an_email_mismatch_is_explained(self):
        self.client.force_login(self.someone_else)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "This link cannot be used")
        self.assertContains(page, "Sign in with that address to accept it.")

    def test_the_invited_address_may_accept(self):
        self.client.force_login(self.invited)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Accept the invitation")
        self.assertContains(page, f"/api/v1/judge-invites/{self.invite.token}/accept")
        accepted = self.client.post(f"/api/v1/judge-invites/{self.invite.token}/accept",
                                    content_type="application/json", data=json.dumps({}))
        self.assertEqual(accepted.status_code, 201)
        self.assertTrue(EventRole.objects.filter(event=self.event, user=self.invited,
                                                 role=Role.JUDGE).exists())

    def test_an_expired_or_used_invitation_says_so(self):
        self.client.force_login(self.invited)
        self.invite.accepted_at = timezone.now()
        self.invite.save(update_fields=["accepted_at"])
        page = self.client.get(self.url)
        self.assertContains(page, "has already been used")
        self.assertContains(self.client.get("/judge-invite?token=not-a-real-token"),
                            "is not valid")
