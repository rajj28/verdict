"""The submission window, checked on the server clock (BUILD-SEC sections 2.4 and 5).

Windows are half-open: ``open_at <= now < close_at``. Every test here freezes
``core.clock.now`` where the services read it and then probes the exact second
the window shuts, because "one second before" and "exactly at" are the two
moments a deadline bug hides in.
"""
import contextlib
import json
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest import mock

from accounts.models import User
from core.clock import now as real_now
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from events.models import CustomQuestion, Event, EventRole, QuestionKind, Role, Track
from projects.models import Project, ProjectStatus
from teams.models import Team, TeamInvite, TeamMember

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
       b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00"
       b"\x00\x00IEND\xaeB`\x82")

SLUG = "deadline-hack"
# A fixed moment so every assertion is about the window and not about the clock.
T0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime_timezone.utc)


@contextlib.contextmanager
def frozen(moment):
    """Pin the clock everywhere a rule reads it.

    Each module imported ``now`` by value, so patching core.clock.now alone
    would leave the services on real time. The list is the whole set of readers.
    """
    with mock.patch("core.clock.now", return_value=moment), \
         mock.patch("events.policy.now", return_value=moment), \
         mock.patch("projects.services.now", return_value=moment), \
         mock.patch("teams.services.now", return_value=moment):
        yield moment


def make_user(email: str, name: str) -> User:
    return User.objects.create_user(email, "verdict-demo", display_name=name)


class DeadlineTests(TestCase):
    """One second before the close everything is allowed; at the close nothing is."""

    @classmethod
    def setUpTestData(cls):
        cls.organizer = make_user("organizer@example.org", "Olive Organizer")
        cls.owner = make_user("owner@example.org", "Ola Owner")
        cls.member = make_user("member@example.org", "Mika Member")
        cls.outsider = make_user("outsider@example.org", "Otto Outsider")
        cls.event = Event.objects.create(
            slug=SLUG, name="Deadline Hack",
            submissions_open_at=T0 - timedelta(days=1),
            submissions_close_at=T0,
            judging_open_at=T0,
            judging_close_at=None,
            max_team_size=4,
            created_by=cls.organizer,
        )
        EventRole.objects.create(event=cls.event, user=cls.organizer, role=Role.ORGANIZER,
                                 public_id="org_01")
        cls.track = Track.objects.create(event=cls.event, name="General", position=1)
        cls.question = CustomQuestion.objects.create(
            event=cls.event, prompt="Link to the demo", kind=QuestionKind.URL, required=True)
        cls.team = Team.objects.create(event=cls.event, name="North Kiln", created_by=cls.owner)
        TeamMember.objects.create(team=cls.team, user=cls.owner, event=cls.event, is_owner=True)
        TeamMember.objects.create(team=cls.team, user=cls.member, event=cls.event)
        cls.project = Project.objects.create(
            event=cls.event, team=cls.team, title="Glass Signal", summary="A summary",
            description="A write-up.", track=cls.track, repo_url="https://example.org/repo",
            status=ProjectStatus.SUBMITTED, first_submitted_at=T0 - timedelta(days=1),
            last_submitted_at=T0 - timedelta(days=1), revision=1)

    def setUp(self):
        self.client.force_login(self.owner)
        self.api = f"/api/v1/events/{SLUG}/projects/{self.project.public_id}"

    # --- helpers ---------------------------------------------------------

    def patch_project(self, **extra):
        payload = {"summary": "A sharper summary"}
        payload.update(extra)
        return self.client.patch(self.api, data=payload, content_type="application/json")

    def post(self, path: str, data=None):
        return self.client.post(path, data={} if data is None else data,
                                content_type="application/json")

    def upload(self):
        return self.client.post(f"{self.api}/images", {
            "image": SimpleUploadedFile("shot.png", PNG, "image/png")})

    def put_answers(self):
        return self.client.put(f"{self.api}/answers",
                               data=json.dumps({self.question.public_id:
                                                "https://example.org/try-it"}),
                               content_type="application/json")

    # --- one second before the close -------------------------------------

    def test_one_second_before_the_close_every_write_is_allowed(self):
        moment = T0 - timedelta(seconds=1)
        with frozen(moment):
            self.assertEqual(self.patch_project().status_code, 200)
            self.assertEqual(self.upload().status_code, 201)
            self.assertEqual(self.put_answers().status_code, 200)
            self.assertEqual(self.post(f"{self.api}/withdraw").status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, ProjectStatus.WITHDRAWN)
        # Withdrawing freed the one-active-project slot, so a new one may start.
        with frozen(moment):
            self.assertEqual(self.post(f"/api/v1/events/{SLUG}/projects",
                                       {"title": "Fresh"}).status_code, 201)

    def test_a_team_can_still_be_created_and_joined_one_second_before_the_close(self):
        moment = T0 - timedelta(seconds=1)
        self.client.force_login(self.outsider)
        with frozen(moment):
            formed = self.post(f"/api/v1/events/{SLUG}/teams", {"name": "Late But Legal"})
        self.assertEqual(formed.status_code, 201, formed.content)
        # A team formed in the last second may still start a project in it.
        with frozen(moment):
            started = self.post(f"/api/v1/events/{SLUG}/projects", {"title": "Just In Time"})
        self.assertEqual(started.status_code, 201, started.content)

    def test_the_last_second_still_accepts_an_invite(self):
        invite = TeamInvite.objects.create(team=self.team, created_by=self.owner,
                                           expires_at=T0 + timedelta(days=1))
        self.client.force_login(self.outsider)
        with frozen(T0 - timedelta(seconds=1)):
            response = self.post(f"/api/v1/invites/{invite.token}/accept")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(TeamMember.objects.filter(team=self.team, user=self.outsider).exists())

    # --- exactly at the close --------------------------------------------

    def test_every_participant_write_at_the_close_is_403_window_closed(self):
        cases = {
            "create project": ("post", f"/api/v1/events/{SLUG}/projects", {"title": "Late"}),
            "update project": ("patch", self.api, {"summary": "Late"}),
            "submit project": ("post", f"{self.api}/submit", None),
            "withdraw project": ("post", f"{self.api}/withdraw", None),
            "save answers": ("put", f"{self.api}/answers",
                             {self.question.public_id: "https://example.org/late"}),
            "create team": ("post", f"/api/v1/events/{SLUG}/teams", {"name": "Too Late"}),
            "leave team": ("post", f"/api/v1/events/{SLUG}/teams/{self.team.public_id}/leave", None),
        }
        with frozen(T0):
            for label, (method, path, data) in cases.items():
                with self.subTest(write=label):
                    response = getattr(self.client, method)(
                        path, data={} if data is None else data, content_type="application/json")
                    self.assertEqual(response.status_code, 403, label)
                    self.assertEqual(response.json()["error"]["code"], "window_closed", label)
            # An image is a multipart write, so it cannot go through the JSON helper.
            with self.subTest(write="add image"):
                response = self.upload()
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error"]["code"], "window_closed")

    def test_the_window_message_names_the_close_time(self):
        with frozen(T0):
            response = self.post(f"/api/v1/events/{SLUG}/projects", {"title": "Late"})
        message = response.json()["error"]["message"]
        self.assertIn("Submissions for Deadline Hack closed at 2026-03-01T12:00:00Z", message)

    def test_a_closed_event_answers_the_window_before_it_validates_the_body(self):
        with frozen(T0):
            response = self.post(f"/api/v1/events/{SLUG}/projects", {"nonsense": True})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "window_closed")
        self.assertFalse(Project.objects.filter(title="Late").exists())

    def test_nothing_changed_while_the_window_was_closed(self):
        before = Project.objects.get(pk=self.project.pk)
        with frozen(T0):
            self.patch_project(summary="Should not land")
            self.post(f"{self.api}/withdraw")
            self.upload()
        after = Project.objects.get(pk=self.project.pk)
        self.assertEqual(after.summary, before.summary)
        self.assertEqual(after.status, before.status)
        self.assertEqual(after.revision, before.revision)
        self.assertEqual(after.images.count(), 0)
        self.assertEqual(after.answers.count(), 0)
        self.assertEqual(after.updated_at, before.updated_at)

    def test_an_invite_at_the_close_is_refused_with_the_window_code(self):
        invite = TeamInvite.objects.create(team=self.team, created_by=self.owner,
                                           expires_at=T0 + timedelta(days=1))
        with frozen(T0):
            response = self.post(f"/api/v1/invites/{invite.token}/accept")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "window_closed")
        self.assertFalse(TeamMember.objects.filter(team=self.team, user=self.outsider).exists())

    def test_an_organizer_can_still_disqualify_after_the_close(self):
        self.client.force_login(self.organizer)
        with frozen(T0 + timedelta(days=3)):
            response = self.post(f"{self.api}/disqualify", {"reason": "Rules broken"})
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, ProjectStatus.DISQUALIFIED)

    def test_an_anonymous_write_is_401_before_the_window_is_consulted(self):
        self.client.logout()
        with frozen(T0):
            response = self.post(f"/api/v1/events/{SLUG}/projects", {"title": "Late"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")

    def test_a_closed_event_keeps_serving_its_pages(self):
        self.client.logout()
        with frozen(T0 + timedelta(hours=5)):
            gallery = self.client.get("/projects")
            detail = self.client.get(f"/events/{SLUG}/projects/{self.project.public_id}")
        self.assertEqual(gallery.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Glass Signal")

    # --- the other end of the window -------------------------------------

    def test_a_window_that_has_not_opened_is_403_window_not_open(self):
        later = Event.objects.create(
            slug="later-hack", name="Later Hack",
            submissions_open_at=T0 + timedelta(hours=1),
            submissions_close_at=T0 + timedelta(hours=3),
            judging_open_at=T0 + timedelta(hours=3),
            created_by=self.organizer,
        )
        team = Team.objects.create(event=later, name="Early Birds", created_by=self.owner)
        TeamMember.objects.create(team=team, user=self.owner, event=later, is_owner=True)
        with frozen(T0):
            created = self.post("/api/v1/events/later-hack/projects", {"title": "Too early"})
            formed = self.post("/api/v1/events/later-hack/teams", {"name": "Too Early"})
        self.assertEqual(created.status_code, 403)
        self.assertEqual(created.json()["error"]["code"], "window_not_open")
        self.assertEqual(formed.status_code, 403)
        self.assertEqual(formed.json()["error"]["code"], "window_not_open")

    def test_the_window_opens_exactly_at_the_open_bound(self):
        later = Event.objects.create(
            slug="opening-hack", name="Opening Hack",
            submissions_open_at=T0 + timedelta(hours=1),
            submissions_close_at=T0 + timedelta(hours=3),
            judging_open_at=T0 + timedelta(hours=3),
            created_by=self.organizer,
        )
        Team.objects.create(event=later, name="On Time", created_by=self.owner)
        TeamMember.objects.create(event=later, team=Team.objects.get(event=later, name="On Time"),
                                  user=self.owner, is_owner=True)
        with frozen(T0 + timedelta(hours=1)):
            response = self.post("/api/v1/events/opening-hack/projects", {"title": "Right now"})
        self.assertEqual(response.status_code, 201, response.content)

    def test_the_real_clock_is_still_the_default(self):
        # A guard against the patches leaking: outside the context manager the
        # services read the live clock, so a long-closed event is still closed.
        self.event.submissions_open_at = real_now() - timedelta(days=2)
        self.event.submissions_close_at = real_now() - timedelta(minutes=1)
        self.event.judging_open_at = self.event.submissions_close_at
        self.event.save(update_fields=["submissions_open_at", "submissions_close_at",
                                       "judging_open_at"])
        response = self.post(f"/api/v1/events/{SLUG}/projects", {"title": "Late"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "window_closed")
