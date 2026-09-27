"""Community voting, result visibility, abuse controls and comments."""
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from community.models import (
    AbuseFlag,
    Ballot,
    BallotItem,
    Comment,
    Voter,
    VoterKind,
    VotingAccess,
    VotingConfig,
    VotingStyle,
)
from community.services import ballot_order
from events.models import Event, EventRole, Role, Track
from projects.models import Project, ProjectStatus
from teams.models import Team


class CommunityVotingTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.organizer = User.objects.create_user("org@community.test", "password")
        self.voter = User.objects.create_user("voter@community.test", "password")
        self.judge = User.objects.create_user("judge@community.test", "password")
        self.other_voter = User.objects.create_user("other@community.test", "password")
        self.event = Event.objects.create(
            slug="community-test", name="Community Test",
            submissions_close_at=now - timedelta(days=1),
            voting_open_at=now - timedelta(hours=1),
            voting_close_at=now + timedelta(hours=1),
            created_by=self.organizer,
        )
        EventRole.objects.create(event=self.event, user=self.organizer, role=Role.ORGANIZER,
                                 public_id="org_community")
        EventRole.objects.create(event=self.event, user=self.voter, role=Role.PARTICIPANT,
                                 public_id="par_community")
        EventRole.objects.create(event=self.event, user=self.judge, role=Role.JUDGE,
                                 public_id="jdg_community")
        self.track = Track.objects.create(event=self.event, name="Open")
        self.projects = []
        for number in range(5):
            team = Team.objects.create(event=self.event, name=f"Team {number}")
            self.projects.append(Project.objects.create(
                event=self.event, team=team, track=self.track,
                public_id=f"prj_community_{number}", title=f"Project {number}",
                status=ProjectStatus.SUBMITTED,
            ))
        self.config = VotingConfig.objects.create(
            event=self.event, access=VotingAccess.AUTHENTICATED,
            style=VotingStyle.QUADRATIC, credits=16,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.voter)
        self.base = f"/api/v1/events/{self.event.slug}"

    def create_ballot(self, client=None, **kwargs):
        return (client or self.client).post(
            f"{self.base}/votes/ballot", kwargs, format="json"
        )

    def test_window_and_role_conflict_are_enforced(self):
        allowed = self.create_ballot()
        self.assertEqual(allowed.status_code, 201)
        self.client.force_authenticate(user=self.organizer)
        organizer_refused = self.create_ballot()
        self.assertEqual(organizer_refused.status_code, 409)
        self.assertEqual(organizer_refused.data["error"]["code"], "role_conflict")
        self.client.force_authenticate(user=self.judge)
        refused = self.create_ballot()
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(refused.data["error"]["code"], "role_conflict")
        self.event.voting_close_at = timezone.now() - timedelta(seconds=1)
        self.event.save(update_fields=["voting_close_at"])
        self.client.force_authenticate(user=self.other_voter)
        closed = self.create_ballot()
        self.assertEqual(closed.status_code, 403)
        self.assertEqual(closed.data["error"]["code"], "voting_closed")

    def test_voting_window_uses_a_half_open_interval(self):
        open_at = timezone.now() + timedelta(minutes=5)
        close_at = open_at + timedelta(minutes=5)
        self.event.voting_open_at = open_at
        self.event.voting_close_at = close_at
        self.event.save(update_fields=["voting_open_at", "voting_close_at"])
        with patch("community.services.now", return_value=open_at):
            opened = self.create_ballot()
        self.assertEqual(opened.status_code, 201)
        another_client = APIClient()
        another_client.force_authenticate(user=self.other_voter)
        with patch("community.services.now", return_value=close_at):
            closed = another_client.post(f"{self.base}/votes/ballot", {}, format="json")
        self.assertEqual(closed.status_code, 403)
        self.assertEqual(closed.data["error"]["code"], "voting_closed")

    def test_gallery_must_be_published_before_ballot_creation(self):
        self.event.gallery_public = False
        self.event.save(update_fields=["gallery_public"])
        refused = self.create_ballot()
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.data["error"]["code"], "gallery_unpublished")

    def test_authenticated_identity_cannot_create_a_second_ballot(self):
        first = self.create_ballot()
        second = self.create_ballot()
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.data["error"]["code"], "duplicate_voter")
        self.assertEqual(Ballot.objects.filter(voter__event=self.event).count(), 1)

    def test_only_organizers_configure_voting_and_rules_lock_after_first_voter(self):
        denied = self.client.patch(
            f"{self.base}/voting/config", {"access": "email"}, format="json"
        )
        self.assertEqual(denied.status_code, 403)
        manager = APIClient()
        manager.force_authenticate(user=self.organizer)
        open_at = timezone.now() - timedelta(minutes=30)
        close_at = timezone.now() + timedelta(minutes=90)
        configured = manager.patch(
            f"{self.base}/voting/config",
            {"style": "quadratic", "credits": 24,
             "voting_open_at": open_at.isoformat(), "voting_close_at": close_at.isoformat()},
            format="json",
        )
        self.assertEqual(configured.status_code, 200, configured.data)
        self.assertEqual(configured.data["credits"], 24)
        invalid_window = manager.patch(
            f"{self.base}/voting/config",
            {"voting_open_at": close_at.isoformat(), "voting_close_at": open_at.isoformat()},
            format="json",
        )
        self.assertEqual(invalid_window.status_code, 400)
        self.assertEqual(AuditEvent.objects.filter(
            event=self.event, action="community.voting.configured"
        ).count(), 1)
        self.create_ballot()
        locked = manager.patch(
            f"{self.base}/voting/config", {"credits": 30}, format="json"
        )
        self.assertEqual(locked.status_code, 409)
        cleared = manager.patch(
            f"{self.base}/voting/config",
            {"voting_open_at": None, "voting_close_at": None}, format="json",
        )
        self.assertEqual(cleared.status_code, 409)

    def test_open_link_uses_a_device_cookie_and_rejects_second_ballot(self):
        self.config.access = VotingAccess.OPEN_LINK
        self.config.save(update_fields=["access"])
        client = APIClient()
        first = client.post(
            f"{self.base}/votes/ballot?v={self.config.link_token}", {}, format="json"
        )
        self.assertEqual(first.status_code, 201, first.data)
        self.assertTrue(first.cookies["verdict_voter"]["httponly"])
        second = client.post(
            f"{self.base}/votes/ballot?v={self.config.link_token}", {}, format="json"
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.data["error"]["code"], "duplicate_voter")
        self.assertEqual(Voter.objects.filter(event=self.event).count(), 1)

    def test_ballot_and_organizer_pages_render_with_access_controls(self):
        ballot_page = self.client.get(f"/events/{self.event.slug}/vote")
        self.assertEqual(ballot_page.status_code, 200)
        self.assertContains(ballot_page, "Each extra vote on the same project costs more")
        denied = APIClient().get(f"/manage/{self.event.slug}/voting")
        self.assertEqual(denied.status_code, 403)
        manager = APIClient()
        manager.force_login(self.organizer)
        dashboard = manager.get(f"/manage/{self.event.slug}/voting")
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "Live tallies")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_access_sends_verification_and_allows_one_verified_ballot(self):
        self.config.access = VotingAccess.EMAIL
        self.config.save(update_fields=["access"])
        sent = self.client.post(
            f"{self.base}/votes/email", {"email": "Voter+hackathon@gmail.com"},
            format="json",
        )
        self.assertEqual(sent.status_code, 202, sent.data)
        self.assertEqual(len(mail.outbox), 1)
        link = mail.outbox[0].body.rsplit(" ", 1)[-1]
        ticket = parse_qs(urlparse(link).query)["token"][0]
        verified = APIClient().post(
            f"{self.base}/votes/email/verify", {"token": ticket}, format="json"
        )
        self.assertEqual(verified.status_code, 201, verified.data)
        duplicate = APIClient().post(
            f"{self.base}/votes/email/verify", {"token": ticket}, format="json"
        )
        self.assertEqual(duplicate.status_code, 409)
        alias = self.client.post(
            f"{self.base}/votes/email", {"email": "voter@gmail.com"}, format="json"
        )
        self.assertEqual(alias.status_code, 409)
        voter = Voter.objects.get(event=self.event, kind=VoterKind.EMAIL)
        self.assertEqual(voter.email_hash, Voter.objects.get(pk=voter.pk).email_hash)
        self.assertIsNotNone(voter.verified_at)

    def test_quadratic_budget_is_enforced_without_mutating_previous_ballot(self):
        created = self.create_ballot()
        ballot_id = created.data["ballot"]
        accepted = self.client.put(
            f"{self.base}/votes/ballot/{ballot_id}",
            {"items": [
                {"project": self.projects[0].public_id, "votes": 2},
                {"project": self.projects[1].public_id, "votes": 2},
            ]}, format="json",
        )
        self.assertEqual(accepted.status_code, 200, accepted.data)
        over_budget = self.client.put(
            f"{self.base}/votes/ballot/{ballot_id}",
            {"items": [{"project": self.projects[0].public_id, "votes": 5}]},
            format="json",
        )
        self.assertEqual(over_budget.status_code, 400)
        self.assertEqual(over_budget.data["error"]["code"], "budget_exceeded")
        self.assertEqual(BallotItem.objects.filter(ballot__public_id=ballot_id).count(), 2)
        replacement = self.client.put(
            f"{self.base}/votes/ballot/{ballot_id}",
            {"items": [{"project": self.projects[2].public_id, "votes": 3}]},
            format="json",
        )
        self.assertEqual(replacement.status_code, 200, replacement.data)
        items = list(BallotItem.objects.filter(ballot__public_id=ballot_id))
        self.assertEqual(len(items), 1)
        self.assertEqual(sum(item.votes ** 2 for item in items), 9)

    def test_voter_change_throttle_and_single_vote_limit(self):
        created = self.create_ballot()
        ballot_id = created.data["ballot"]
        with patch("community.services.CHANGES_PER_HOUR", 0):
            limited = self.client.put(
                f"{self.base}/votes/ballot/{ballot_id}",
                {"items": []}, format="json",
            )
        self.assertEqual(limited.status_code, 429)
        self.config.style = VotingStyle.SINGLE
        self.config.save(update_fields=["style"])
        limited_per_project = self.client.put(
            f"{self.base}/votes/ballot/{ballot_id}",
            {"items": [{"project": self.projects[0].public_id, "votes": 2}]},
            format="json",
        )
        self.assertEqual(limited_per_project.status_code, 400)
        self.assertEqual(limited_per_project.data["error"]["code"], "vote_limit")

    def test_ip_hash_limits_new_ballots_to_thirty_per_hour(self):
        self.config.access = VotingAccess.OPEN_LINK
        self.config.style = VotingStyle.SINGLE
        self.config.save(update_fields=["access", "style"])
        client = APIClient()
        for _ in range(30):
            response = client.post(
                f"{self.base}/votes/ballot?v={self.config.link_token}", {}, format="json"
            )
            self.assertEqual(response.status_code, 201, response.data)
            client.cookies.clear()
        limited = client.post(
            f"{self.base}/votes/ballot?v={self.config.link_token}", {}, format="json"
        )
        self.assertEqual(limited.status_code, 429)

    def test_project_order_is_seeded_per_ballot_and_not_popularity_sorted(self):
        ballot_a = Ballot.objects.create(
            voter=Voter.objects.create(event=self.event, kind=VoterKind.USER,
                                       user=self.other_voter),
            order_seed="seed-one",
        )
        third_user = User.objects.create_user("third@community.test", "password")
        ballot_b = Ballot.objects.create(
            voter=Voter.objects.create(event=self.event, kind=VoterKind.USER, user=third_user),
            order_seed="seed-two",
        )
        self.assertEqual(
            [p.public_id for p in ballot_order(ballot_a)],
            [p.public_id for p in ballot_order(ballot_a)],
        )
        self.assertNotEqual(
            [p.public_id for p in ballot_order(ballot_a)],
            [p.public_id for p in ballot_order(ballot_b)],
        )
        self.assertEqual(ballot_a.order_seed, "seed-one")

    def test_results_hidden_from_voters_in_api_and_page_until_close(self):
        ballot = self.create_ballot()
        BallotItem.objects.create(ballot_id=Ballot.objects.get(public_id=ballot.data["ballot"]).pk,
                                  project=self.projects[0], votes=1)
        api = self.client.get(f"{self.base}/voting/results")
        self.assertEqual(api.status_code, 403)
        self.assertEqual(api.data["error"]["code"], "results_hidden")
        anonymous = APIClient().get(f"{self.base}/voting/results")
        self.assertEqual(anonymous.status_code, 403)
        page = self.client.get(f"/events/{self.event.slug}/voting/results")
        self.assertEqual(page.status_code, 403)
        self.assertNotContains(page, "Project 0", status_code=403)
        export = self.client.get(f"{self.base}/exports/votes.csv")
        self.assertEqual(export.status_code, 403)
        organizer = APIClient()
        organizer.force_authenticate(user=self.organizer)
        organizer_result = organizer.get(f"{self.base}/voting/results")
        self.assertEqual(organizer_result.status_code, 200)
        self.assertEqual(organizer_result.data["results"][0]["project__public_id"],
                         self.projects[0].public_id)
        csv_response = organizer.get(f"{self.base}/exports/votes.csv")
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn(b"project_public_id,project_title,votes,quadratic_credits", csv_response.content)

    def test_organizer_voting_dashboard_list_has_constant_queries(self):
        created = self.create_ballot()
        ballot = Ballot.objects.get(public_id=created.data["ballot"])
        BallotItem.objects.create(ballot=ballot, project=self.projects[0], votes=1)
        manager = APIClient()
        manager.force_authenticate(user=self.organizer)
        with self.assertNumQueries(13):
            response = manager.get(f"{self.base}/voting/manage")
        self.assertEqual(response.status_code, 200)

    def test_results_become_public_at_close_and_voided_ballots_are_excluded(self):
        created = self.create_ballot()
        ballot = Ballot.objects.get(public_id=created.data["ballot"])
        BallotItem.objects.create(ballot=ballot, project=self.projects[0], votes=2)
        manager = APIClient()
        manager.force_authenticate(user=self.organizer)
        voided = manager.post(
            f"{self.base}/voting/ballots/{ballot.public_id}/void",
            {"reason": "Duplicate submission"}, format="json",
        )
        self.assertEqual(voided.status_code, 200)
        restored = manager.post(
            f"{self.base}/voting/ballots/{ballot.public_id}/restore",
            {"reason": "Verified as valid"}, format="json",
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(AuditEvent.objects.filter(
            event=self.event, action__startswith="community.ballot."
        ).count(), 3)
        self.event.voting_close_at = timezone.now() - timedelta(seconds=1)
        self.event.save(update_fields=["voting_close_at"])
        result = APIClient().get(f"{self.base}/voting/results")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["results"][0]["votes"], 2)

    def test_public_comments_escape_and_organizer_moderation_is_audited(self):
        created = self.client.post(
            f"{self.base}/projects/{self.projects[0].public_id}/comments",
            {"body": "<script>alert(1)</script>"},
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        comment = Comment.objects.get(public_id=created.data["public_id"])
        page = self.client.get(
            f"/events/{self.event.slug}/projects/{self.projects[0].public_id}"
        )
        self.assertContains(page, "&lt;script&gt;")
        self.assertNotContains(page, "<script>alert(1)</script>")
        manager = APIClient()
        manager.force_authenticate(user=self.organizer)
        hide = manager.post(
            f"{self.base}/comments/{comment.public_id}/hide",
            {"reason": "Abusive content"}, format="json",
        )
        self.assertEqual(hide.status_code, 200)
        public_comments = self.client.get(
            f"{self.base}/projects/{self.projects[0].public_id}/comments"
        )
        self.assertEqual(public_comments.data["comments"], [])
        restore = manager.post(
            f"{self.base}/comments/{comment.public_id}/restore",
            {"reason": "Reviewed"}, format="json",
        )
        self.assertEqual(restore.status_code, 200)
        self.assertEqual(AuditEvent.objects.filter(
            event=self.event, action__startswith="community.comment."
        ).count(), 3)

    def test_comment_rate_limit_is_five_per_ten_minutes(self):
        for _ in range(5):
            response = self.client.post(
                f"{self.base}/projects/{self.projects[0].public_id}/comments",
                {"body": "Hello"}, format="json",
            )
            self.assertEqual(response.status_code, 201)
        limited = self.client.post(
            f"{self.base}/projects/{self.projects[0].public_id}/comments",
            {"body": "Sixth"}, format="json",
        )
        self.assertEqual(limited.status_code, 429)

    def test_comment_list_endpoint_uses_a_fixed_number_of_queries(self):
        Comment.objects.create(project=self.projects[0], author=self.voter, body="One")
        Comment.objects.create(project=self.projects[0], author=self.other_voter, body="Two")
        with self.assertNumQueries(4):
            response = self.client.get(
                f"{self.base}/projects/{self.projects[0].public_id}/comments"
            )
        self.assertEqual(response.status_code, 200)

    def test_burst_from_one_ip_hash_creates_an_abuse_flag(self):
        self.config.access = VotingAccess.OPEN_LINK
        self.config.save(update_fields=["access"])
        client = APIClient()
        for _ in range(11):
            response = client.post(
                f"{self.base}/votes/ballot?v={self.config.link_token}", {}, format="json"
            )
            self.assertEqual(response.status_code, 201, response.data)
            client.cookies.clear()
        self.assertEqual(AbuseFlag.objects.filter(event=self.event, kind="ip_burst").count(), 1)


class CommunityEmailNormalizationTests(TestCase):
    def test_gmail_plus_address_heuristic(self):
        from community.services import normalize_email
        self.assertEqual(normalize_email("First.Last+demo@Gmail.com"), "firstlast@gmail.com")
        self.assertEqual(normalize_email("User+tag@example.test"), "user+tag@example.test")
