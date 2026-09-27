"""Teams: creating one, sharing an invite link, joining it and leaving it.

Every rule in teams/services.py is proved here with the allowed case and the
refused one, because the interesting part of a team system is what it says no
to: a closed window, a full team, a rotated link, a judge trying to compete.
"""
import json
from datetime import timedelta

from accounts.models import User
from core.clock import now
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from events.models import Event, EventRole, Role
from teams.models import Team, TeamInvite, TeamMember

SLUG = "team-hack-2026"


def make_event(*, slug: str = SLUG, organizer=None, max_team_size: int = 2,
               gallery_public: bool = True) -> Event:
    return Event.objects.create(
        slug=slug,
        name="Team Hack 2026",
        submissions_open_at=None,
        submissions_close_at=now() + timedelta(hours=6),
        judging_open_at=None,
        judging_close_at=None,
        max_team_size=max_team_size,
        gallery_public=gallery_public,
        created_by=organizer,
    )


def make_user(email: str, name: str) -> User:
    return User.objects.create_user(email, "verdict-demo", display_name=name)


class TeamApiTests(TestCase):
    """POST/GET on /api/v1/events/{slug}/teams and everything hanging off it."""

    @classmethod
    def setUpTestData(cls):
        cls.organizer = make_user("organizer@example.org", "Olive Organizer")
        cls.owner = make_user("owner@example.org", "Ola Owner")
        cls.member = make_user("member@example.org", "Mika Member")
        cls.outsider = make_user("outsider@example.org", "Otto Outsider")
        cls.judge = make_user("judge@example.org", "Juno Judge")
        cls.event = make_event(organizer=cls.organizer, max_team_size=4)
        EventRole.objects.create(event=cls.event, user=cls.organizer, role=Role.ORGANIZER,
                                 public_id="org_01")
        EventRole.objects.create(event=cls.event, user=cls.judge, role=Role.JUDGE,
                                 public_id="jdg_01")
        cls.team = Team.objects.create(event=cls.event, name="North Kiln", created_by=cls.owner)
        TeamMember.objects.create(team=cls.team, user=cls.owner, event=cls.event, is_owner=True)
        TeamMember.objects.create(team=cls.team, user=cls.member, event=cls.event)

    def post(self, path: str, data=None):
        return self.client.post(path, data={} if data is None else data,
                                content_type="application/json")

    def as_user(self, user):
        self.client.force_login(user)

    # --- creating --------------------------------------------------------

    def test_anonymous_cannot_create_a_team(self):
        response = self.post(f"/api/v1/events/{SLUG}/teams", {"name": "Ghosts"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")
        self.assertFalse(Team.objects.filter(name="Ghosts").exists())

    def test_creating_a_team_registers_the_caller_and_makes_them_owner(self):
        self.as_user(self.outsider)
        response = self.post(f"/api/v1/events/{SLUG}/teams", {"name": "Quiet Harbor"})
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["name"], "Quiet Harbor")
        self.assertEqual(body["member_count"], 1)
        self.assertTrue(body["members"][0]["is_owner"])
        self.assertEqual(body["members"][0]["user"], self.outsider.public_id)
        self.assertTrue(EventRole.objects.filter(event=self.event, user=self.outsider,
                                                 role=Role.PARTICIPANT).exists())
        self.assertTrue(TeamMember.objects.filter(team__public_id=body["public_id"],
                                                  user=self.outsider, is_owner=True).exists())

    def test_a_team_name_is_unique_per_event_case_insensitively(self):
        self.as_user(self.outsider)
        response = self.post(f"/api/v1/events/{SLUG}/teams", {"name": "  north kiln "})
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.json()["error"]["fields"])
        self.assertFalse(Team.objects.filter(name="  north kiln ").exists())

    def test_a_blank_team_name_is_refused(self):
        self.as_user(self.outsider)
        response = self.post(f"/api/v1/events/{SLUG}/teams", {"name": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["fields"]["name"], ["Give your team a name."])

    def test_a_second_team_for_the_same_person_is_409_already_in_team(self):
        self.as_user(self.member)
        response = self.post(f"/api/v1/events/{SLUG}/teams", {"name": "Second Wind"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "already_in_team")

    def test_one_team_per_person_per_event_is_also_a_database_constraint(self):
        # The service check is the friendly one; this is the one that cannot be
        # raced past by two requests at the same moment.
        other = Team.objects.create(event=self.event, name="Constraint Test")
        with self.assertRaises(IntegrityError), transaction.atomic():
            TeamMember.objects.create(team=other, user=self.owner, event=self.event)

    def test_a_judge_cannot_create_a_team(self):
        self.as_user(self.judge)
        response = self.post(f"/api/v1/events/{SLUG}/teams", {"name": "Judges Inc"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "role_conflict")
        self.assertFalse(Team.objects.filter(name="Judges Inc").exists())

    def test_an_organizer_cannot_create_a_team_to_compete_in(self):
        self.as_user(self.organizer)
        response = self.post(f"/api/v1/events/{SLUG}/teams", {"name": "Organizers Inc"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "role_conflict")

    # --- reading ---------------------------------------------------------

    def test_a_participant_sees_only_their_own_team(self):
        self.as_user(self.member)
        body = self.client.get(f"/api/v1/events/{SLUG}/teams").json()
        self.assertEqual([row["public_id"] for row in body], [self.team.public_id])

    def test_an_organizer_sees_every_team_and_never_an_email(self):
        Team.objects.create(event=self.event, name="Second Wind", created_by=self.outsider)
        self.as_user(self.organizer)
        body = self.client.get(f"/api/v1/events/{SLUG}/teams").json()
        self.assertEqual(len(body), 2)
        for row in body:
            for member in row["members"]:
                self.assertNotIn("email", member)
                self.assertNotIn("@", json.dumps(member))

    def test_a_judge_sees_no_team_list(self):
        self.as_user(self.judge)
        self.assertEqual(self.client.get(f"/api/v1/events/{SLUG}/teams").json(), [])

    def test_a_team_outside_the_caller_s_is_404(self):
        self.as_user(self.outsider)
        response = self.client.get(f"/api/v1/events/{SLUG}/teams/{self.team.public_id}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "team_not_found")

    def test_the_team_list_is_a_constant_number_of_queries(self):
        Team.objects.create(event=self.event, name="Second Wind", created_by=self.outsider)
        self.as_user(self.organizer)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(f"/api/v1/events/{SLUG}/teams")
        self.assertEqual(response.status_code, 200)
        # Session and user (authentication), event, role, the team page, and one
        # prefetch each for memberships and their users. Five of these are the
        # listing; none of them grows with the number of teams.
        self.assertEqual(len(queries.captured_queries), 7)

    # --- invites ---------------------------------------------------------

    def rotate(self, user=None):
        self.as_user(user or self.member)
        response = self.client.post(
            f"/api/v1/events/{SLUG}/teams/{self.team.public_id}/invite", content_type="application/json")
        return response

    def test_only_a_member_can_share_the_invite_link(self):
        response = self.rotate(self.outsider)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_a_member")
        self.assertEqual(TeamInvite.objects.count(), 0)

    def test_rotating_returns_a_link_with_the_token_in_the_query_string(self):
        response = self.rotate()
        self.assertEqual(response.status_code, 201)
        url = response.json()["url"]
        self.assertTrue(url.startswith("/invite?token="), url)
        # The token must not be a path segment: the access log records paths.
        self.assertNotIn(f"/invite/{url.rsplit('=', 1)[1]}", url)

    def test_rotating_revokes_the_previous_link(self):
        first = self.rotate().json()["url"]
        second = self.rotate().json()["url"]
        self.assertNotEqual(first, second)
        revoked = TeamInvite.objects.exclude(revoked_at__isnull=True)
        self.assertEqual(revoked.count(), 1)
        self.as_user(self.outsider)
        self.assertEqual(self.accept(first.rsplit("=", 1)[1])["status_code"], 410)
        self.assertEqual(self.accept(second.rsplit("=", 1)[1])["status_code"], 201)

    def test_an_expired_link_is_410_invite_invalid(self):
        invite = TeamInvite.objects.create(
            team=self.team, created_by=self.owner, expires_at=now() - timedelta(minutes=1))
        result = self.accept(invite.token, self.outsider)
        self.assertEqual(result["status_code"], 410)
        self.assertEqual(result["json"]["error"]["code"], "invite_invalid")
        self.assertFalse(TeamMember.objects.filter(team=self.team, user=self.outsider).exists())

    def test_an_invite_never_outlives_the_submission_window(self):
        event = make_event(slug="short-hack", organizer=self.organizer)
        event.submissions_close_at = now() + timedelta(hours=1)
        event.save(update_fields=["submissions_close_at"])
        team = Team.objects.create(event=event, name="Short Window", created_by=self.owner)
        TeamMember.objects.create(team=team, user=self.owner, event=event, is_owner=True)
        self.as_user(self.owner)
        response = self.client.post(f"/api/v1/events/short-hack/teams/{team.public_id}/invite",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 201)
        invite = TeamInvite.objects.get(team=team)
        self.assertLessEqual(invite.expires_at, event.submissions_close_at)
        self.assertLess(invite.expires_at, now() + timedelta(days=7))

    def accept(self, token: str, user=None):
        self.as_user(user or self.outsider)
        response = self.client.post(f"/api/v1/invites/{token}/accept", data={},
                                    content_type="application/json")
        return {"status_code": response.status_code, "json": response.json()}

    def test_joining_counts_against_the_team_size(self):
        event = make_event(slug="full-hack", organizer=self.organizer, max_team_size=2)
        team = Team.objects.create(event=event, name="Full House", created_by=self.owner)
        TeamMember.objects.create(team=team, user=self.owner, event=event, is_owner=True)
        TeamMember.objects.create(team=team, user=self.member, event=event)
        invite = TeamInvite.objects.create(team=team, created_by=self.owner,
                                           expires_at=now() + timedelta(days=1))
        result = self.accept(invite.token)
        self.assertEqual(result["status_code"], 409)
        self.assertEqual(result["json"]["error"]["code"], "team_full")
        self.assertFalse(TeamMember.objects.filter(team=team, user=self.outsider).exists())

    def test_an_anonymous_caller_cannot_accept_an_invite(self):
        invite = TeamInvite.objects.create(team=self.team, created_by=self.owner,
                                           expires_at=now() + timedelta(days=1))
        self.client.logout()
        response = self.client.post(f"/api/v1/invites/{invite.token}/accept", data={},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_a_judge_cannot_join_a_team(self):
        invite = TeamInvite.objects.create(team=self.team, created_by=self.owner,
                                           expires_at=now() + timedelta(days=1))
        result = self.accept(invite.token, self.judge)
        self.assertEqual(result["status_code"], 409)
        self.assertEqual(result["json"]["error"]["code"], "role_conflict")

    def test_somebody_already_in_a_team_cannot_accept_another_invite(self):
        invite = TeamInvite.objects.create(team=self.team, created_by=self.owner,
                                           expires_at=now() + timedelta(days=1))
        result = self.accept(invite.token, self.member)
        self.assertEqual(result["status_code"], 409)
        self.assertEqual(result["json"]["error"]["code"], "already_in_team")

    def test_joining_uses_the_invite_and_keeps_the_team_at_the_size_limit(self):
        event = make_event(slug="room-hack", organizer=self.organizer, max_team_size=3)
        team = Team.objects.create(event=event, name="Room For Two", created_by=self.owner)
        TeamMember.objects.create(team=team, user=self.owner, event=event, is_owner=True)
        invite = TeamInvite.objects.create(team=team, created_by=self.owner,
                                           expires_at=now() + timedelta(days=1))
        result = self.accept(invite.token)
        self.assertEqual(result["status_code"], 201)
        invite.refresh_from_db()
        self.assertEqual(invite.use_count, 1)
        self.assertFalse(result["json"]["is_owner"])

    # --- leaving and removing -------------------------------------------

    def leave(self, user, event_slug: str = SLUG, team=None):
        self.as_user(user)
        return self.client.post(
            f"/api/v1/events/{event_slug}/teams/{(team or self.team).public_id}/leave", data={},
            content_type="application/json")

    def test_the_owner_passes_ownership_to_the_earliest_member_on_leaving(self):
        self.assertEqual(self.leave(self.owner).status_code, 200)
        self.assertTrue(TeamMember.objects.get(team=self.team, user=self.member).is_owner)
        self.assertFalse(TeamMember.objects.filter(team=self.team, user=self.owner).exists())

    def test_a_non_member_cannot_leave(self):
        response = self.leave(self.outsider)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_a_member")

    def test_the_last_member_out_deletes_a_team_with_only_a_draft(self):
        from projects.models import Project, ProjectStatus

        event = make_event(slug="draft-hack", organizer=self.organizer)
        team = Team.objects.create(event=event, name="Draft Only", created_by=self.outsider)
        TeamMember.objects.create(team=team, user=self.outsider, event=event, is_owner=True)
        Project.objects.create(event=event, team=team, title="Draft", status=ProjectStatus.DRAFT)
        response = self.leave(self.outsider, "draft-hack", team)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Team.objects.filter(pk=team.pk).exists())

    def test_leaving_a_team_with_a_submitted_project_asks_for_a_withdrawal_first(self):
        from projects.models import Project, ProjectStatus

        event = make_event(slug="submitted-hack", organizer=self.organizer)
        team = Team.objects.create(event=event, name="Submitted", created_by=self.outsider)
        TeamMember.objects.create(team=team, user=self.outsider, event=event, is_owner=True)
        Project.objects.create(event=event, team=team, title="Live", status=ProjectStatus.SUBMITTED)
        response = self.leave(self.outsider, "submitted-hack", team)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "withdraw_first")
        self.assertTrue(Team.objects.filter(pk=team.pk).exists())

    def test_a_team_with_withdrawn_history_is_kept(self):
        from projects.models import Project, ProjectStatus

        event = make_event(slug="history-hack", organizer=self.organizer)
        team = Team.objects.create(event=event, name="History", created_by=self.outsider)
        TeamMember.objects.create(team=team, user=self.outsider, event=event, is_owner=True)
        Project.objects.create(event=event, team=team, title="Old", status=ProjectStatus.WITHDRAWN)
        response = self.leave(self.outsider, "history-hack", team)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "team_not_deletable")
        self.assertEqual(Project.objects.filter(team=team).count(), 1)

    def test_only_the_owner_can_remove_a_member(self):
        self.as_user(self.member)
        response = self.client.delete(
            f"/api/v1/events/{SLUG}/teams/{self.team.public_id}/members/{self.member.public_id}")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_team_owner")

    def test_the_owner_can_remove_somebody_else(self):
        self.as_user(self.owner)
        response = self.client.delete(
            f"/api/v1/events/{SLUG}/teams/{self.team.public_id}/members/{self.member.public_id}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TeamMember.objects.filter(team=self.team, user=self.member).exists())

    def test_the_owner_has_to_leave_rather_than_remove_themselves(self):
        self.as_user(self.owner)
        response = self.client.delete(
            f"/api/v1/events/{SLUG}/teams/{self.team.public_id}/members/{self.owner.public_id}")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "owner_cannot_be_removed")
        self.assertTrue(TeamMember.objects.filter(team=self.team, user=self.owner).exists())

    def test_removing_somebody_who_is_not_in_the_team_is_404(self):
        self.as_user(self.owner)
        response = self.client.delete(
            f"/api/v1/events/{SLUG}/teams/{self.team.public_id}/members/{self.outsider.public_id}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "member_not_found")

    # --- the two pages ---------------------------------------------------

    def test_the_team_page_lists_members_and_never_another_person_s_email(self):
        self.as_user(self.member)
        response = self.client.get(f"/events/{SLUG}/team")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "North Kiln")
        self.assertContains(response, "Mika Member")
        self.assertContains(response, "Ola Owner")
        # The signed-in person's own address is in their own nav; nobody else's is.
        self.assertNotContains(response, self.organizer.email)
        self.assertNotContains(response, self.member.email.replace("member", "owner"))
        self.assertContains(response, "owner")

    def test_the_team_page_sends_an_anonymous_visitor_to_the_login_page(self):
        response = self.client.get(f"/events/{SLUG}/team")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_the_team_page_offers_the_create_form_to_somebody_without_a_team(self):
        self.as_user(self.outsider)
        response = self.client.get(f"/events/{SLUG}/team")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"/api/v1/events/{SLUG}/teams")
        self.assertContains(response, "Join with a link")

    def test_the_invite_page_shows_the_team_and_the_event(self):
        invite = TeamInvite.objects.create(team=self.team, created_by=self.owner,
                                           expires_at=now() + timedelta(days=1))
        response = self.client.get("/invite", {"token": invite.token})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "North Kiln")
        self.assertContains(response, "Team Hack 2026")
        self.assertContains(response, "/login")

    def test_the_invite_page_explains_a_dead_link(self):
        response = self.client.get("/invite", {"token": "nope"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This link cannot be used")
        self.assertNotContains(response, "/api/v1/invites/nope/accept")

    def test_the_invite_page_explains_a_full_team(self):
        event = make_event(slug="full-hack", organizer=self.organizer, max_team_size=2)
        team = Team.objects.create(event=event, name="Full House", created_by=self.owner)
        TeamMember.objects.create(team=team, user=self.owner, event=event, is_owner=True)
        TeamMember.objects.create(team=team, user=self.member, event=event)
        invite = TeamInvite.objects.create(team=team, created_by=self.owner,
                                           expires_at=now() + timedelta(days=1))
        self.as_user(self.outsider)
        response = self.client.get("/invite", {"token": invite.token})
        self.assertContains(response, "already has its 2 members")


class ClosedWindowTeamTests(TestCase):
    """The window is checked before anything else a team write could say."""

    @classmethod
    def setUpTestData(cls):
        cls.organizer = make_user("organizer@example.org", "Olive Organizer")
        cls.member = make_user("member@example.org", "Mika Member")
        cls.event = make_event(organizer=cls.organizer)
        EventRole.objects.create(event=cls.event, user=cls.organizer, role=Role.ORGANIZER,
                                 public_id="org_01")
        cls.team = Team.objects.create(event=cls.event, name="North Kiln", created_by=cls.member)
        TeamMember.objects.create(team=cls.team, user=cls.member, event=cls.event, is_owner=True)
        cls.closed = make_event(slug="closed-hack", organizer=cls.organizer)
        cls.closed.submissions_close_at = timezone.now() - timedelta(minutes=1)
        cls.closed.save(update_fields=["submissions_close_at"])
        cls.closed_team = Team.objects.create(event=cls.closed, name="Too Late",
                                              created_by=cls.member)
        TeamMember.objects.create(team=cls.closed_team, user=cls.member, event=cls.closed,
                                  is_owner=True)

    def setUp(self):
        self.client.force_login(self.member)

    def test_creating_a_team_after_the_close_is_403_window_closed(self):
        response = self.client.post("/api/v1/events/closed-hack/teams", data={"name": "Late"},
                                    content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "window_closed")
        self.assertFalse(Team.objects.filter(name="Late").exists())

    def test_leaving_after_the_close_is_403_window_closed(self):
        response = self.client.post(f"/api/v1/events/closed-hack/teams/{self.closed_team.public_id}/leave",
                                    data={}, content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "window_closed")
        self.assertTrue(TeamMember.objects.filter(team=self.closed_team).exists())

    def test_the_window_message_names_the_close_time(self):
        response = self.client.post("/api/v1/events/closed-hack/teams", data={"name": "Late"},
                                    content_type="application/json")
        message = response.json()["error"]["message"]
        self.assertIn("Submissions for Team Hack 2026 closed at", message)
