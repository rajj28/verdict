"""Cross-role and cross-event isolation tests for the judging API."""
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from events.models import CustomQuestion, Event, EventRole, JudgingMode, Role, Track
from judging.models import Assignment, Criterion, Review, Rubric
from projects.models import Answer, Project, ProjectStatus
from teams.models import Team


class JudgingIsolationTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.admin = User.objects.create_user("admin@ex.org", "password", is_admin=True)
        self.organizer = User.objects.create_user("org@ex.org", "password")
        self.other_organizer = User.objects.create_user("other@ex.org", "password")
        self.judge_a = User.objects.create_user("a@ex.org", "password", display_name="Judge A")
        self.judge_b = User.objects.create_user("b@ex.org", "password", display_name="Judge B")
        self.participant = User.objects.create_user("participant@ex.org", "password")
        self.event = Event.objects.create(
            slug="isolation-event", name="Isolation Event",
            submissions_close_at=now - timedelta(days=2),
            judging_open_at=now - timedelta(days=2),
            judging_close_at=now + timedelta(days=2),
            created_by=self.organizer,
        )
        self.other_event = Event.objects.create(
            slug="other-event", name="Other Event",
            submissions_close_at=now - timedelta(days=2),
            judging_open_at=now - timedelta(days=2),
            judging_close_at=now + timedelta(days=2),
            created_by=self.other_organizer,
        )
        EventRole.objects.create(event=self.event, user=self.organizer, role=Role.ORGANIZER,
                                 public_id="org_owner")
        EventRole.objects.create(event=self.other_event, user=self.other_organizer, role=Role.ORGANIZER,
                                 public_id="org_other")
        self.role_a = EventRole.objects.create(event=self.event, user=self.judge_a, role=Role.JUDGE,
                                               public_id="jdg_isolation_a")
        self.role_b = EventRole.objects.create(event=self.event, user=self.judge_b, role=Role.JUDGE,
                                               public_id="jdg_isolation_b")
        EventRole.objects.create(event=self.event, user=self.participant, role=Role.PARTICIPANT,
                                 public_id="par_isolation")
        self.track_a = Track.objects.create(event=self.event, name="Track A")
        self.track_b = Track.objects.create(event=self.event, name="Track B")
        self.role_a.tracks.add(self.track_a)
        self.role_b.tracks.add(self.track_b)
        self.team_a = Team.objects.create(event=self.event, name="Team A")
        self.team_b = Team.objects.create(event=self.event, name="Team B")
        self.project_a = Project.objects.create(
            event=self.event, team=self.team_a, track=self.track_a, public_id="prj_iso_a",
            title="Project A", status=ProjectStatus.SUBMITTED, revision=1,
        )
        self.project_b = Project.objects.create(
            event=self.event, team=self.team_b, track=self.track_b, public_id="prj_iso_b",
            title="Project B", status=ProjectStatus.SUBMITTED, revision=1,
        )
        question = CustomQuestion.objects.create(
            event=self.event, prompt="Private answer", is_public=False,
        )
        Answer.objects.create(project=self.project_a, question=question, value="Assigned private answer")
        Answer.objects.create(project=self.project_b, question=question, value="Unassigned private answer")
        self.rubric = Rubric.objects.create(event=self.event)
        self.criterion = Criterion.objects.create(
            rubric=self.rubric, key="quality", name="Quality", weight="1", min_score=1, max_score=5,
        )
        self.assignment_a = Assignment.objects.create(
            event=self.event, judge=self.role_a, project=self.project_a, public_id="asg_iso_a",
        )
        self.assignment_b = Assignment.objects.create(
            event=self.event, judge=self.role_b, project=self.project_b, public_id="asg_iso_b",
        )
        self.review_b = Review.objects.create(
            event=self.event, assignment=self.assignment_b, judge=self.role_b, project=self.project_b,
            public_id="rev_iso_b",
        )
        self.client = APIClient()

    def login(self, user):
        self.client.force_authenticate(user=user)

    def test_judge_cannot_read_peer_review_or_scores(self):
        self.login(self.judge_a)
        paths = [
            f"/api/v1/events/{self.event.slug}/judge/reviews/{self.project_b.public_id}",
            f"/api/v1/events/{self.event.slug}/judges/{self.role_b.public_id}/scores",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.get(
            f"/api/v1/judge/scores?judge={self.role_b.public_id}"
        ).status_code, 403)

    def test_assignments_are_self_only_and_peer_filter_is_forbidden(self):
        self.login(self.judge_a)
        own = self.client.get("/api/v1/judge/assignments")
        self.assertEqual(own.status_code, 200)
        self.assertEqual([row["project"]["public_id"] for row in own.data["assignments"]],
                         [self.project_a.public_id])
        answer_values = [answer["value"] for answer in own.data["assignments"][0]["project"]["answers"]]
        self.assertIn("Assigned private answer", answer_values)
        self.assertNotIn("Unassigned private answer", answer_values)
        self.assertEqual(self.client.get(
            f"/api/v1/judge/assignments?judge={self.role_b.public_id}"
        ).status_code, 403)
        self.assertEqual(self.client.get(
            f"/api/v1/events/{self.event.slug}/assignments?judge={self.role_b.public_id}"
        ).status_code, 403)

    def test_cross_event_reused_judge_public_id_does_not_expand_assignment_scope(self):
        same_id_role = EventRole.objects.create(
            event=self.other_event, user=self.judge_b, role=Role.JUDGE,
            public_id=self.role_a.public_id,
        )
        project = Project.objects.create(
            event=self.other_event, team=Team.objects.create(event=self.other_event, name="Other team"),
            public_id="prj_other_event", title="Other event project",
            status=ProjectStatus.SUBMITTED, revision=1,
        )
        Assignment.objects.create(event=self.other_event, judge=same_id_role, project=project)
        self.login(self.judge_a)
        response = self.client.get(f"/api/v1/judge/assignments?judge={self.role_a.public_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["event"] for row in response.data["assignments"]], [self.event.slug])

    def test_judge_cannot_view_or_write_unassigned_or_out_of_track_project(self):
        self.login(self.judge_a)
        path = f"/api/v1/events/{self.event.slug}/judge/reviews/{self.project_b.public_id}"
        body = {"scores": {"quality": 4}, "comment": "attempt"}
        self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.put(path, body, format="json").status_code, 403)
        self.assertEqual(self.client.post(path + "/submit", body, format="json").status_code, 403)
        misplaced = Project.objects.create(
            event=self.event, team=Team.objects.create(event=self.event, name="Team C"),
            track=self.track_b, public_id="prj_wrong_track", title="Wrong track",
            status=ProjectStatus.SUBMITTED, revision=1,
        )
        Assignment.objects.create(event=self.event, judge=self.role_a, project=misplaced)
        self.assertEqual(self.client.get(
            f"/api/v1/events/{self.event.slug}/judge/reviews/{misplaced.public_id}"
        ).status_code, 403)

    def test_judges_cannot_read_progress(self):
        self.login(self.judge_a)
        self.assertEqual(self.client.get(f"/api/v1/events/{self.event.slug}/progress").status_code, 403)

    def test_other_event_organizer_cannot_read_judging_data(self):
        self.login(self.other_organizer)
        for path in (
            f"/api/v1/events/{self.event.slug}/rubric",
            f"/api/v1/events/{self.event.slug}/judges",
            f"/api/v1/events/{self.event.slug}/assignments",
            f"/api/v1/events/{self.event.slug}/progress",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)

    def test_participant_is_denied_every_judging_surface(self):
        self.login(self.participant)
        forbidden_gets = [
            "/api/v1/judge/assignments",
            "/api/v1/judge/scores",
            f"/api/v1/events/{self.event.slug}/judges/{self.role_a.public_id}/scores",
            f"/api/v1/events/{self.event.slug}/rubric",
            f"/api/v1/events/{self.event.slug}/judges",
            f"/api/v1/events/{self.event.slug}/conflicts",
            f"/api/v1/events/{self.event.slug}/assignments",
            f"/api/v1/events/{self.event.slug}/progress",
            f"/api/v1/events/{self.event.slug}/judge/reviews/{self.project_a.public_id}",
            f"/api/v1/events/{self.event.slug}/judge/pairs/next",
        ]
        for path in forbidden_gets:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.put(
            f"/api/v1/events/{self.event.slug}/rubric", {"criteria": []}, format="json"
        ).status_code, 403)
        self.assertEqual(self.client.post(
            f"/api/v1/events/{self.event.slug}/assignments/auto", {}, format="json"
        ).status_code, 403)
        json_posts = [
            ("post", f"/api/v1/events/{self.event.slug}/judges",
             {"email": self.participant.email, "tracks": []}),
            ("patch", f"/api/v1/events/{self.event.slug}/judges/{self.role_a.public_id}",
             {"tracks": []}),
            ("delete", f"/api/v1/events/{self.event.slug}/judges/{self.role_a.public_id}", None),
            ("post", f"/api/v1/events/{self.event.slug}/judge-invites",
             {"emails": ["invite@example.org"], "tracks": []}),
            ("post", f"/api/v1/events/{self.event.slug}/conflicts",
             {"judge": self.role_a.public_id, "team": self.team_a.public_id}),
            ("post", f"/api/v1/events/{self.event.slug}/judge/conflicts",
             {"team": self.team_a.public_id}),
            ("post", f"/api/v1/events/{self.event.slug}/assignments",
             {"judges": [self.role_a.public_id], "projects": [self.project_a.public_id]}),
            ("delete", f"/api/v1/events/{self.event.slug}/assignments/{self.assignment_a.public_id}", None),
            ("put", f"/api/v1/events/{self.event.slug}/judge/reviews/{self.project_a.public_id}",
             {"scores": {"quality": 4}}),
            ("post", f"/api/v1/events/{self.event.slug}/judge/reviews/{self.project_a.public_id}/submit",
             {"scores": {"quality": 4}}),
            ("post", f"/api/v1/events/{self.event.slug}/judge/comparisons",
             {"left": self.project_a.public_id, "right": self.project_b.public_id}),
            ("post", f"/api/v1/events/{self.event.slug}/reviews/{self.review_b.public_id}/exclusion",
             {"reason": "No"}),
            ("delete", f"/api/v1/events/{self.event.slug}/reviews/{self.review_b.public_id}/exclusion", None),
        ]
        for method, path, body in json_posts:
            with self.subTest(method=method, path=path):
                response = getattr(self.client, method)(path, body, format="json") if body is not None else \
                    getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 403)

    def test_visitor_is_unauthenticated_on_judging_surface(self):
        self.client.force_authenticate(user=None)
        for path in (
            "/api/v1/judge/assignments",
            "/api/v1/judge/scores",
            f"/api/v1/events/{self.event.slug}/rubric",
            f"/api/v1/events/{self.event.slug}/judges",
            f"/api/v1/events/{self.event.slug}/assignments",
            f"/api/v1/events/{self.event.slug}/progress",
            f"/api/v1/events/{self.event.slug}/judge/reviews/{self.project_a.public_id}",
            f"/api/v1/events/{self.event.slug}/judge/pairs/next",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)

    def test_admin_can_read_event_judging_data(self):
        self.login(self.admin)
        self.assertEqual(self.client.get(f"/api/v1/events/{self.event.slug}/judges").status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/events/{self.event.slug}/progress").status_code, 200)
        self.assertEqual(self.client.get(
            f"/api/v1/events/{self.event.slug}/judge/reviews/{self.project_b.public_id}"
        ).status_code, 200)

    def test_judging_list_query_count_does_not_grow_per_assignment(self):
        self.login(self.judge_a)
        with CaptureQueriesContext(connection) as one_assignment:
            self.assertEqual(self.client.get("/api/v1/judge/assignments").status_code, 200)
        for index in range(3):
            team = Team.objects.create(event=self.event, name=f"Extra team {index}")
            project = Project.objects.create(
                event=self.event, team=team, track=self.track_a, public_id=f"prj_extra_{index}",
                title=f"Extra {index}", status=ProjectStatus.SUBMITTED, revision=1,
            )
            Assignment.objects.create(event=self.event, judge=self.role_a, project=project)
        with CaptureQueriesContext(connection) as four_assignments:
            self.assertEqual(self.client.get("/api/v1/judge/assignments").status_code, 200)
        self.assertEqual(len(one_assignment), len(four_assignments))

    def test_event_judging_reads_are_available_to_organizers(self):
        self.login(self.organizer)
        self.assertEqual(self.client.get(f"/api/v1/events/{self.event.slug}/assignments").status_code, 200)
        self.assertEqual(self.client.get(
            f"/api/v1/events/{self.event.slug}/judge/reviews/{self.project_b.public_id}"
        ).status_code, 200)

    def test_progress_query_count_does_not_grow_per_project(self):
        self.login(self.organizer)
        path = f"/api/v1/events/{self.event.slug}/progress"
        with CaptureQueriesContext(connection) as two_projects:
            self.assertEqual(self.client.get(path).status_code, 200)
        for index in range(3):
            project = Project.objects.create(
                event=self.event, team=Team.objects.create(event=self.event, name=f"Progress {index}"),
                track=self.track_a, public_id=f"prj_progress_{index}", title=f"Progress {index}",
                status=ProjectStatus.SUBMITTED, revision=1,
            )
            Assignment.objects.create(event=self.event, judge=self.role_a, project=project)
        with CaptureQueriesContext(connection) as five_projects:
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(len(two_projects), len(five_projects))

    def test_judging_api_allows_scoped_management_and_review_flow(self):
        rubric_path = f"/api/v1/events/{self.event.slug}/rubric"
        self.login(self.organizer)
        rubric = self.client.put(rubric_path, {"criteria": [
            {"key": "clarity", "name": "Clarity", "weight": "1.000", "min_score": 1, "max_score": 5},
        ]}, format="json")
        self.assertEqual(rubric.status_code, 200)
        candidate = User.objects.create_user("candidate@ex.org", "password")
        invitation = self.client.post(
            f"/api/v1/events/{self.event.slug}/judge-invites",
            {"emails": [candidate.email], "tracks": [self.track_a.public_id]}, format="json",
        )
        self.assertEqual(invitation.status_code, 201)
        token = invitation.data["invites"][0]["token"]
        self.login(candidate)
        accepted = self.client.post(f"/api/v1/judge-invites/{token}/accept", {}, format="json")
        self.assertEqual(accepted.status_code, 201)
        self.assertEqual(accepted.data["event"], self.event.slug)
        self.login(self.judge_a)
        review_path = f"/api/v1/events/{self.event.slug}/judge/reviews/{self.project_a.public_id}"
        draft = self.client.put(
            review_path, {"scores": {"clarity": 4}, "comment": "Draft"}, format="json",
        )
        self.assertEqual(draft.status_code, 200)
        submitted = self.client.post(
            review_path + "/submit", {"scores": {"clarity": 5}, "comment": "Final"}, format="json",
        )
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(submitted.data["status"], "submitted")
        self.assertEqual(self.client.get("/api/v1/judge/scores").status_code, 200)
        self.login(self.organizer)
        excluded = self.client.post(
            f"/api/v1/events/{self.event.slug}/reviews/{submitted.data['review_id']}/exclusion",
            {"reason": "Duplicate evidence"}, format="json",
        )
        self.assertEqual(excluded.status_code, 201)
        self.assertEqual(self.client.delete(
            f"/api/v1/events/{self.event.slug}/reviews/{submitted.data['review_id']}/exclusion"
        ).status_code, 204)

    def test_pairwise_api_returns_only_assigned_projects(self):
        self.event.judging_mode = JudgingMode.PAIRWISE
        self.event.save(update_fields=["judging_mode"])
        second = Project.objects.create(
            event=self.event, team=Team.objects.create(event=self.event, name="Pair team"),
            track=self.track_a, public_id="prj_pair_api", title="Pair API",
            status=ProjectStatus.SUBMITTED, revision=1,
        )
        Assignment.objects.create(event=self.event, judge=self.role_a, project=second)
        self.login(self.judge_a)
        next_path = f"/api/v1/events/{self.event.slug}/judge/pairs/next"
        response = self.client.get(next_path)
        self.assertEqual(response.status_code, 200)
        pair = response.data["pair"]
        self.assertEqual(
            {pair["left"]["public_id"], pair["right"]["public_id"]},
            {self.project_a.public_id, second.public_id},
        )
        comparison = self.client.post(
            f"/api/v1/events/{self.event.slug}/judge/comparisons",
            {"left": pair["left"]["public_id"], "right": pair["right"]["public_id"],
             "winner": pair["left"]["public_id"]}, format="json",
        )
        self.assertEqual(comparison.status_code, 201)
        self.assertIsNone(self.client.get(next_path).data["pair"])
