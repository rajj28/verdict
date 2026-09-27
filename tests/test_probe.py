"""The integrity runner exercises permissions through Django's HTTP stack."""
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import ApiToken, User
from accounts.tokens import issue_token
from audit.models import AuditEvent
from community.models import AbuseFlag, Ballot, Voter, VotingConfig
from core.probe import run_probe
from events.models import CustomQuestion, Event, EventRole, Role, Track
from judging.models import Assignment, Review, Rubric
from projects.models import Answer, Project
from teams.models import Team, TeamInvite, TeamMember


class IntegrityProbeTests(TestCase):
    def test_every_current_attack_is_refused_and_fixture_data_rolls_back(self):
        before = {
            "events": Event.objects.count(),
            "users": User.objects.count(),
            "tokens": ApiToken.objects.count(),
            "probe_events": Event.objects.filter(slug__startswith="probe-").count(),
            "tracks": Track.objects.count(),
            "roles": EventRole.objects.count(),
            "teams": Team.objects.count(),
            "members": TeamMember.objects.count(),
            "invites": TeamInvite.objects.count(),
            "projects": Project.objects.count(),
            "answers": Answer.objects.count(),
            "assignments": Assignment.objects.count(),
            "reviews": Review.objects.count(),
            "questions": CustomQuestion.objects.count(),
            "rubrics": Rubric.objects.count(),
            "audit": AuditEvent.objects.count(),
            "voting_configs": VotingConfig.objects.count(),
            "voters": Voter.objects.count(),
            "ballots": Ballot.objects.count(),
            "abuse_flags": AbuseFlag.objects.count(),
        }
        report = run_probe()
        self.assertTrue(report["ok"], report["cases"])
        self.assertEqual(report["total"], 28)
        self.assertTrue(all(case["passed"] for case in report["cases"]))
        self.assertEqual(
            next(case["path"] for case in report["cases"] if case["key"] == "participant-join-after-deadline"),
            "/api/v1/invites/[temporary]/accept",
        )
        self.assertEqual(Event.objects.count(), before["events"])
        self.assertEqual(User.objects.count(), before["users"])
        self.assertEqual(ApiToken.objects.count(), before["tokens"])
        self.assertEqual(
            Event.objects.filter(slug__startswith="probe-").count(), before["probe_events"]
        )
        self.assertEqual(Track.objects.count(), before["tracks"])
        self.assertEqual(EventRole.objects.count(), before["roles"])
        self.assertEqual(Team.objects.count(), before["teams"])
        self.assertEqual(TeamMember.objects.count(), before["members"])
        self.assertEqual(TeamInvite.objects.count(), before["invites"])
        self.assertEqual(Project.objects.count(), before["projects"])
        self.assertEqual(Answer.objects.count(), before["answers"])
        self.assertEqual(Assignment.objects.count(), before["assignments"])
        self.assertEqual(Review.objects.count(), before["reviews"])
        self.assertEqual(CustomQuestion.objects.count(), before["questions"])
        self.assertEqual(Rubric.objects.count(), before["rubrics"])
        self.assertEqual(AuditEvent.objects.count(), before["audit"] + 1)
        self.assertEqual(VotingConfig.objects.count(), before["voting_configs"])
        self.assertEqual(Voter.objects.count(), before["voters"])
        self.assertEqual(Ballot.objects.count(), before["ballots"])
        self.assertEqual(AbuseFlag.objects.count(), before["abuse_flags"])

    def test_policy_regression_fails_its_matching_case(self):
        with patch("projects.policy.can_see_project", return_value=True):
            report = run_probe()
        case = next(row for row in report["cases"] if row["key"] == "other-team-draft-by-id")
        self.assertFalse(case["passed"])
        self.assertEqual(case["status_code"], 200)

    def test_authenticated_non_admin_and_non_organizer_cannot_run_api(self):
        user = User.objects.create_user("probe-denied@example.test", password=None)
        _token, plaintext = issue_token(user, "probe api denied")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {plaintext}")
        response = client.post("/api/v1/integrity/probe", {}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "forbidden")

    def test_probe_api_rejects_non_object_scope(self):
        user = User.objects.create_user("probe-payload@example.test", password=None)
        _token, plaintext = issue_token(user, "probe api payload")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {plaintext}")
        response = client.post("/api/v1/integrity/probe", [], format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "invalid")

    def test_admin_runs_all_cases(self):
        user = User.objects.create_user("probe-admin@example.test", password=None, is_admin=True)
        _token, plaintext = issue_token(user, "probe api admin")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {plaintext}")
        response = client.post("/api/v1/integrity/probe", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 28)
        self.assertTrue(response.data["ok"])

    def test_organizer_must_scope_probe_to_their_event(self):
        user = User.objects.create_user("probe-organizer@example.test", password=None)
        event = Event.objects.create(
            slug="probe-organizer-event",
            name="Probe Organizer Event",
            submissions_close_at=timezone.now(),
            created_by=user,
        )
        EventRole.objects.create(event=event, user=user, role=Role.ORGANIZER,
                                 public_id="org_probe_test")
        _token, plaintext = issue_token(user, "probe api organizer")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {plaintext}")
        response = client.post("/api/v1/integrity/probe", {"event": event.slug}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["ok"])
        self.assertEqual(response.data["event"], event.slug)

    def test_unrelated_authenticated_user_cannot_view_admin_page(self):
        user = User.objects.create_user("probe-page-denied@example.test", password=None)
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get("/admin-panel/integrity")
        self.assertEqual(response.status_code, 403)
