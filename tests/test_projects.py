"""Projects: draft, submit, edit as a new revision, withdraw, disqualify, gallery.

The rules under test are the ones a team can feel: what the submission window
allows, who may touch a project, what a revision receipt proves, and exactly
which projects the public gallery is allowed to show.
"""
import io
import tempfile
from datetime import timedelta
from pathlib import Path

from accounts.models import User
from core.bootstrap import DEMO_EVENT_SLUG, bootstrap
from core.clock import now
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from events.models import CustomQuestion, Event, QuestionKind
from projects.models import Project, ProjectStatus
from projects.services import snapshot_digest
from teams.models import Team, TeamMember

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
       b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00"
       b"\x00\x00IEND\xaeB`\x82")
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def make_user(email: str, name: str) -> User:
    return User.objects.create_user(email, "verdict-demo", display_name=name)


@override_settings(DEMO_MODE=True)
class ProjectLifecycleTests(TestCase):
    """Draft to submit to withdraw, through the API a browser form posts to."""

    @classmethod
    def setUpTestData(cls):
        bootstrap()
        cls.event = Event.objects.get(slug=DEMO_EVENT_SLUG)
        cls.track = cls.event.tracks.first()
        cls.fixture_event = Event.objects.get(slug="sample-hack-2026")
        cls.foreign_track = cls.fixture_event.tracks.first()
        cls.required = CustomQuestion.objects.create(
            event=cls.event, prompt="Where can we try it?", kind=QuestionKind.URL,
            required=True, is_public=True, position=10)
        cls.private = CustomQuestion.objects.filter(event=cls.event, is_public=False).first()
        cls.owner = make_user("owner@example.org", "Ola Owner")
        cls.member = make_user("member@example.org", "Mika Member")
        cls.outsider = make_user("outsider@example.org", "Otto Outsider")
        cls.organizer = User.objects.get(email="organizer@verdict.local")
        cls.team = Team.objects.create(event=cls.event, name="Test Kitchen", created_by=cls.owner)
        TeamMember.objects.create(team=cls.team, user=cls.owner, event=cls.event, is_owner=True)
        TeamMember.objects.create(team=cls.team, user=cls.member, event=cls.event)
        cls.other_team = Team.objects.create(event=cls.event, name="Other Kitchen",
                                             created_by=cls.outsider)
        TeamMember.objects.create(team=cls.other_team, user=cls.outsider, event=cls.event,
                                  is_owner=True)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.override = override_settings(MEDIA_ROOT=Path(self._tmp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self._tmp.cleanup)
        self.client.force_login(self.owner)

    # --- helpers ---------------------------------------------------------

    def url(self, suffix: str = "") -> str:
        return f"/api/v1/events/{DEMO_EVENT_SLUG}/projects/{self.project.public_id}{suffix}"

    def call(self, method: str, path: str, data=None, **extra):
        return getattr(self.client, method)(path, data={} if data is None else data,
                                            content_type="application/json", **extra)

    def create_draft(self, title: str = "Test Kitchen Project") -> Project:
        response = self.call("post", f"/api/v1/events/{DEMO_EVENT_SLUG}/projects",
                             {"title": title, "summary": "A working summary"})
        self.assertEqual(response.status_code, 201, response.content)
        return Project.objects.get(public_id=response.json()["public_id"])

    def complete(self, project: Project) -> dict:
        return {
            "summary": "A working summary",
            "description": "What it does, in a paragraph.",
            "track": self.track.public_id,
            "repo_url": "https://github.com/example/test-kitchen",
            "tech_tags": ["Python", "Django"],
        }

    def submit(self, project: Project):
        self.call("patch", self.url_for(project), self.complete(project))
        self.call("put", self.url_for(project, "/answers"),
                  {self.required.public_id: "https://example.org/try-it"})
        return self.call("post", self.url_for(project, "/submit"))

    def url_for(self, project: Project, suffix: str = "") -> str:
        return f"/api/v1/events/{DEMO_EVENT_SLUG}/projects/{project.public_id}{suffix}"

    # --- creating --------------------------------------------------------

    def test_creating_a_draft_needs_a_team(self):
        TeamMember.objects.filter(team=self.other_team, user=self.outsider).delete()
        self.client.force_login(self.outsider)
        response = self.call("post", f"/api/v1/events/{DEMO_EVENT_SLUG}/projects",
                             {"title": "No team"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_a_participant")

    def test_a_second_active_project_for_one_team_is_409(self):
        self.create_draft()
        response = self.call("post", f"/api/v1/events/{DEMO_EVENT_SLUG}/projects",
                             {"title": "Second"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "team_has_project")

    def test_a_javascript_url_is_refused(self):
        self.project = self.create_draft()
        response = self.call("patch", self.url(), {"live_url": "javascript:alert(1)"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("live_url", response.json()["error"]["fields"])

    def test_tags_are_lowercased_capped_and_deduplicated(self):
        self.project = self.create_draft()
        response = self.call("patch", self.url(), {"tech_tags": ["Python", " python ", "Django"]})
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.tech_tags, ["python", "django"])

    def test_too_many_tags_is_400(self):
        self.project = self.create_draft()
        response = self.call("patch", self.url(),
                             {"tech_tags": [f"tag{index}" for index in range(11)]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("tech_tags", response.json()["error"]["fields"])

    def test_a_track_from_another_event_is_400_cross_event(self):
        self.project = self.create_draft()
        response = self.call("patch", self.url(), {"track": self.foreign_track.public_id})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "cross_event")

    # --- submitting ------------------------------------------------------

    def test_submitting_without_the_required_fields_lists_every_one(self):
        self.project = self.create_draft()
        response = self.call("post", self.url("/submit"))
        self.assertEqual(response.status_code, 400)
        error = response.json()["error"]
        self.assertEqual(error["code"], "incomplete")
        self.assertLessEqual({"description", "repo_url", "track"}, set(error["fields"]))
        self.assertIn(f"question_{self.required.public_id}", error["fields"])
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, ProjectStatus.DRAFT)

    def test_a_draft_becomes_a_submitted_project_with_a_receipt(self):
        self.project = self.create_draft()
        response = self.submit(self.project)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], "submitted")
        self.assertEqual(body["revision"], 1)
        self.project.refresh_from_db()
        self.assertIsNotNone(self.project.first_submitted_at)
        self.assertEqual(self.project.first_submitted_at, self.project.last_submitted_at)
        revision = self.project.revisions.get()
        self.assertEqual(revision.number, 1)
        # The receipt is the sha256 of that revision's own snapshot.
        self.assertEqual(revision.digest, snapshot_digest(revision.snapshot))
        self.assertEqual(revision.snapshot["title"], self.project.title)
        # The snapshot carries every answer, keyed by the question row it belongs to.
        self.assertEqual(revision.snapshot["answers"][str(self.required.pk)],
                         "https://example.org/try-it")

    def test_submitting_twice_is_409(self):
        self.project = self.create_draft()
        self.submit(self.project)
        response = self.call("post", self.url("/submit"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "already_submitted")

    def test_editing_a_submitted_project_creates_a_revision_immediately(self):
        self.project = self.create_draft()
        self.submit(self.project)
        first_digest = self.project.revisions.get().digest
        response = self.call("patch", self.url(), {"summary": "A sharper summary"})
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.revision, 2)
        second = self.project.revisions.get(number=2)
        self.assertEqual(second.snapshot["summary"], "A sharper summary")
        self.assertNotEqual(first_digest, second.digest)
        self.assertEqual(second.digest, snapshot_digest(second.snapshot))

    def test_a_save_that_changes_nothing_creates_no_revision(self):
        self.project = self.create_draft()
        self.submit(self.project)
        response = self.call("patch", self.url(), {"title": self.project.title})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.project.revisions.count(), 1)

    def test_a_stale_base_updated_at_is_409_stale_edit(self):
        self.project = self.create_draft()
        self.call("patch", self.url(), {"summary": "First summary"})
        self.project.refresh_from_db()
        stale = (self.project.updated_at - timedelta(seconds=30)).isoformat()
        response = self.call("patch", self.url(), {"summary": "From another tab",
                                                   "base_updated_at": stale})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "stale_edit")
        self.project.refresh_from_db()
        self.assertEqual(self.project.summary, "First summary")

    def test_the_current_base_updated_at_is_accepted(self):
        self.project = self.create_draft()
        self.project.refresh_from_db()
        response = self.call("patch", self.url(), {"summary": "Same tab",
                                                   "base_updated_at": self.project.updated_at.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.summary, "Same tab")

    # --- who may write ---------------------------------------------------

    def test_another_member_of_the_same_team_may_edit(self):
        self.project = self.create_draft()
        self.client.force_login(self.member)
        response = self.call("patch", self.url(), {"summary": "Edited by a teammate"})
        self.assertEqual(response.status_code, 200)

    def test_another_team_may_not_edit(self):
        self.project = self.create_draft()
        self.submit(self.project)
        self.client.force_login(self.outsider)
        response = self.call("patch", self.url(), {"summary": "Not mine to edit"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_a_member")
        self.project.refresh_from_db()
        self.assertNotEqual(self.project.summary, "Not mine to edit")

    def test_another_team_may_not_edit_a_draft_either(self):
        self.project = self.create_draft()
        self.client.force_login(self.outsider)
        response = self.call("patch", self.url(), {"summary": "Not mine to edit"})
        # A draft the caller may not read is 404, not 403: the page must not
        # confirm that somebody else's draft exists.
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "project_not_found")

    def test_another_team_gets_404_for_a_draft(self):
        self.project = self.create_draft()
        self.client.force_login(self.outsider)
        response = self.call("get", self.url())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "project_not_found")

    def test_a_participant_may_not_disqualify(self):
        self.project = self.create_draft()
        self.submit(self.project)
        response = self.call("post", self.url("/disqualify"), {"reason": "Because"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "forbidden")

    def test_an_organizer_disqualifies_with_a_recorded_reason(self):
        self.project = self.create_draft()
        self.submit(self.project)
        self.client.force_login(self.organizer)
        response = self.call("post", self.url("/disqualify"), {"reason": "Not built in the hackathon"})
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, ProjectStatus.DISQUALIFIED)
        self.assertEqual(self.project.status_reason, "Not built in the hackathon")
        from audit.models import AuditEvent
        self.assertTrue(AuditEvent.objects.filter(action="project.disqualified",
                                                  target_id=self.project.public_id).exists())

    def test_disqualifying_without_a_reason_is_400(self):
        self.project = self.create_draft()
        self.submit(self.project)
        self.client.force_login(self.organizer)
        response = self.call("post", self.url("/disqualify"), {"reason": "  "})
        self.assertEqual(response.status_code, 400)
        self.assertIn("reason", response.json()["error"]["fields"])

    def test_disqualifying_twice_is_409(self):
        self.project = self.create_draft()
        self.submit(self.project)
        self.client.force_login(self.organizer)
        self.call("post", self.url("/disqualify"), {"reason": "First"})
        response = self.call("post", self.url("/disqualify"), {"reason": "Second"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "not_disqualifiable")

    # --- withdrawing -----------------------------------------------------

    def test_only_the_team_owner_can_withdraw(self):
        self.project = self.create_draft()
        self.submit(self.project)
        self.client.force_login(self.member)
        response = self.call("post", self.url("/withdraw"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_team_owner")

    def test_the_owner_withdraws_and_the_gallery_forgets_it(self):
        self.project = self.create_draft()
        self.submit(self.project)
        response = self.call("post", self.url("/withdraw"))
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, ProjectStatus.WITHDRAWN)
        self.client.logout()
        gallery = self.client.get("/api/v1/projects", {"q": self.project.title}).json()
        self.assertEqual(gallery["count"], 0)

    def test_a_withdrawn_project_can_no_longer_be_edited(self):
        self.project = self.create_draft()
        self.submit(self.project)
        self.call("post", self.url("/withdraw"))
        response = self.call("patch", self.url(), {"summary": "Too late"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "project_not_active")

    def test_a_new_project_is_allowed_after_a_withdrawal(self):
        self.project = self.create_draft()
        self.submit(self.project)
        self.call("post", self.url("/withdraw"))
        response = self.call("post", f"/api/v1/events/{DEMO_EVENT_SLUG}/projects",
                             {"title": "Second Idea"})
        self.assertEqual(response.status_code, 201)

    # --- answers ---------------------------------------------------------

    def test_answers_survive_a_revision(self):
        self.project = self.create_draft()
        self.submit(self.project)
        response = self.call("put", self.url("/answers"),
                             {self.private.public_id: "Ask us anything."})
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.revision, 2)

    def test_an_answer_to_another_event_s_question_is_400_cross_event(self):
        foreign = CustomQuestion.objects.create(
            event=self.fixture_event, prompt="Fixture only?", kind=QuestionKind.SHORT_TEXT)
        self.project = self.create_draft()
        response = self.call("put", self.url("/answers"), {foreign.public_id: "hello"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "cross_event")

    def test_a_private_answer_is_hidden_from_the_public_and_shown_to_the_team(self):
        self.project = self.create_draft()
        self.call("put", self.url("/answers"), {self.private.public_id: "Secret sauce"})
        self.project.refresh_from_db()
        self.submit(self.project)
        self.client.logout()
        public = self.client.get(self.url())
        self.assertEqual(public.status_code, 200)
        self.assertNotIn("Secret sauce", [row["value"] for row in public.json()["answers"]])
        detail = self.client.get(f"/events/{DEMO_EVENT_SLUG}/projects/{self.project.public_id}")
        self.assertNotContains(detail, "Secret sauce")
        self.client.force_login(self.member)
        seen = self.client.get(self.url()).json()
        self.assertIn("Secret sauce", [row["value"] for row in seen["answers"]])
        page = self.client.get(f"/events/{DEMO_EVENT_SLUG}/projects/{self.project.public_id}")
        self.assertContains(page, "Secret sauce")

    def test_an_organizer_sees_private_answers(self):
        self.project = self.create_draft()
        self.call("put", self.url("/answers"), {self.private.public_id: "Secret sauce"})
        self.client.force_login(self.organizer)
        seen = self.client.get(self.url()).json()
        self.assertIn("Secret sauce", [row["value"] for row in seen["answers"]])

    # --- images ----------------------------------------------------------

    def upload(self, name: str = "shot.png", content: bytes = PNG, content_type: str = "image/png"):
        return self.client.post(self.url("/images"), {
            "image": SimpleUploadedFile(name, content, content_type), "caption": "A screenshot"})

    def test_an_image_is_stored_under_a_random_name(self):
        self.project = self.create_draft()
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.content)
        stored = response.json()["url"]
        self.assertNotIn("shot.png", stored)
        self.assertTrue(stored.endswith(".png"))

    def test_an_svg_is_refused_even_when_named_like_a_png(self):
        self.project = self.create_draft()
        response = self.upload("sneaky.png", SVG, "image/png")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_image")
        self.assertEqual(self.project.images.count(), 0)

    def test_a_readable_but_refused_format_is_named_in_the_error(self):
        # Pillow opens a TIFF happily; the policy still says no, and says which
        # formats it does accept.
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (1, 1)).save(buffer, format="TIFF")
        self.project = self.create_draft()
        response = self.upload("scan.tiff", buffer.getvalue(), "image/tiff")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "unsupported_image_type")
        self.assertIn("jpeg, png, webp or gif", response.json()["error"]["message"])

    def test_an_oversize_image_is_refused(self):
        self.project = self.create_draft()
        big = PNG + b"\x00" * (5 * 1024 * 1024)
        response = self.upload("big.png", big)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "image_too_large")

    def test_a_seventh_image_is_refused(self):
        self.project = self.create_draft()
        for index in range(6):
            self.assertEqual(self.upload(f"shot{index}.png").status_code, 201)
        response = self.upload("shot7.png")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "too_many_images")
        self.assertEqual(self.project.images.count(), 6)

    def test_an_image_is_deleted_by_its_position(self):
        self.project = self.create_draft()
        self.upload()
        self.upload()
        response = self.call("delete", self.url("/images/1"))
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual([image.position for image in self.project.images.all()], [1])
        self.assertEqual(self.call("delete", self.url("/images/9")).status_code, 404)

    def test_a_drafts_image_is_404_for_the_public(self):
        self.project = self.create_draft()
        stored = self.upload().json()["url"]
        self.client.logout()
        self.assertEqual(self.client.get(stored).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(stored).status_code, 200)

    def test_another_team_cannot_add_an_image(self):
        self.project = self.create_draft()
        self.submit(self.project)
        self.client.force_login(self.outsider)
        self.assertEqual(self.upload().status_code, 403)

    # --- the two pages ---------------------------------------------------

    def test_the_editor_shows_the_fields_the_receipt_and_the_checklist(self):
        self.project = self.create_draft()
        self.submit(self.project)
        self.project.refresh_from_db()
        receipt = self.project.revisions.get().digest[:8]
        response = self.client.get(f"/events/{DEMO_EVENT_SLUG}/submission")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Before you submit")
        self.assertContains(response, "receipt " + receipt)
        self.assertContains(response, "revision 1")
        self.assertContains(response, self.project.title)
        self.assertContains(response, "base_updated_at")

    def test_the_editor_offers_the_draft_to_somebody_without_a_project(self):
        response = self.client.get(f"/events/{DEMO_EVENT_SLUG}/submission")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Start your draft")
        self.assertContains(response, f"/api/v1/events/{DEMO_EVENT_SLUG}/projects")

    def test_the_editor_sends_an_anonymous_visitor_to_the_login_page(self):
        self.client.logout()
        response = self.client.get(f"/events/{DEMO_EVENT_SLUG}/submission")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_the_editor_is_read_only_after_the_window_closes(self):
        self.project = self.create_draft()
        self.submit(self.project)
        # The window has to stay ordered: move the open bound in with the close.
        self.event.submissions_open_at = now() - timedelta(days=2)
        self.event.submissions_close_at = now() - timedelta(minutes=1)
        self.event.judging_open_at = self.event.submissions_close_at
        self.event.save(update_fields=["submissions_open_at", "submissions_close_at",
                                       "judging_open_at"])
        response = self.client.get(f"/events/{DEMO_EVENT_SLUG}/submission")
        self.assertContains(response, "Submissions closed at")
        self.assertNotContains(response, "Save draft")

    def test_the_project_page_shows_the_write_up_and_no_emails(self):
        self.project = self.create_draft()
        self.submit(self.project)
        self.client.logout()
        response = self.client.get(
            f"/events/{DEMO_EVENT_SLUG}/projects/{self.project.public_id}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.project.title)
        self.assertContains(response, "What it does, in a paragraph.")
        self.assertNotContains(response, self.owner.email)
        self.assertNotContains(response, self.member.email)

    def test_the_project_page_is_404_for_a_draft_the_caller_does_not_own(self):
        self.project = self.create_draft()
        self.client.force_login(self.outsider)
        response = self.client.get(
            f"/events/{DEMO_EVENT_SLUG}/projects/{self.project.public_id}")
        self.assertEqual(response.status_code, 404)

    def test_the_team_page_and_the_project_page_agree_on_the_project(self):
        self.project = self.create_draft()
        self.submit(self.project)
        page = self.client.get(f"/events/{DEMO_EVENT_SLUG}/team")
        self.assertContains(page, self.project.title)
        self.assertContains(page, "Submitted")


@override_settings(DEMO_MODE=True)
class GalleryScopeTests(TestCase):
    """The gallery shows submitted projects from public events, and nothing else."""

    @classmethod
    def setUpTestData(cls):
        bootstrap()
        cls.event = Event.objects.get(slug="sample-hack-2026")
        cls.track = cls.event.tracks.first()
        cls.user = make_user("reader@example.org", "Rae Reader")
        cls.team = Team.objects.create(event=cls.event, name="Gallery Team",
                                       created_by=cls.user)
        TeamMember.objects.create(team=cls.team, user=cls.user, event=cls.event, is_owner=True)

    def gallery(self, **params):
        response = self.client.get("/api/v1/projects", params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_the_gallery_is_anonymous_json_and_paginated(self):
        body = self.gallery()
        self.assertEqual(sorted(body), ["count", "next", "previous", "results"])
        self.assertNotIn("id", body["results"][0])
        self.assertEqual(body["results"][0]["event"], "sample-hack-2026")

    def test_the_gallery_is_sorted_by_title_by_default(self):
        titles = [row["title"] for row in self.gallery()["results"]]
        self.assertEqual(titles, sorted(titles))
        newest = [row["title"] for row in self.gallery(sort="newest")["results"]]
        self.assertNotEqual(newest, titles)

    def test_an_unknown_sort_is_400(self):
        response = self.client.get("/api/v1/projects", {"sort": "sideways"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid")

    def test_the_gallery_excludes_a_draft(self):
        draft = Project.objects.create(event=self.event, team=self.team, title="Hidden Draft",
                                       status=ProjectStatus.DRAFT)
        self.assertNotIn(draft.public_id, [row["public_id"] for row in self.gallery()["results"]])

    def test_the_gallery_excludes_withdrawn_disqualified_and_superseded(self):
        withdrawn = Project.objects.get(public_id="prj_01")
        withdrawn.status = ProjectStatus.WITHDRAWN
        withdrawn.save(update_fields=["status"])
        disqualified = Project.objects.get(public_id="prj_02")
        disqualified.status = ProjectStatus.DISQUALIFIED
        disqualified.save(update_fields=["status"])
        listed = {row["public_id"] for row in self.gallery()["results"]}
        self.assertNotIn("prj_01", listed)
        self.assertNotIn("prj_02", listed)
        superseded = Project.objects.filter(status=ProjectStatus.SUPERSEDED).first()
        self.assertIsNotNone(superseded)
        self.assertNotIn(superseded.public_id, listed)

    def test_the_gallery_excludes_a_private_event(self):
        self.event.gallery_public = False
        self.event.save(update_fields=["gallery_public"])
        self.assertNotIn("prj_01", {row["public_id"] for row in self.gallery()["results"]})

    def test_search_filtering_by_title_summary_and_description(self):
        self.assertTrue(self.gallery(q="glass signal")["results"])
        self.assertEqual(self.gallery(q="nothing at all like this")["count"], 0)

    def test_the_track_filter_uses_the_public_track_id(self):
        body = self.gallery(track=self.track.public_id)
        self.assertTrue(body["results"])
        self.assertEqual({row["track"] for row in body["results"]}, {self.track.public_id})

    def test_the_tag_filter_matches_the_whole_tag_only(self):
        project = Project.objects.create(
            event=self.event, team=self.team, title="Tagged Project", track=self.track,
            tech_tags=["python", "django"], status=ProjectStatus.SUBMITTED,
            last_submitted_at=now())
        listed = {row["public_id"] for row in self.gallery(tag="python")["results"]}
        self.assertIn(project.public_id, listed)
        # A substring must not match: "go" is not "django".
        self.assertNotIn(project.public_id, {row["public_id"] for row in self.gallery(tag="go")["results"]})

    def test_the_event_filter_scopes_the_gallery(self):
        from core.bootstrap import DEMO_EVENT_SLUG
        self.assertTrue(self.gallery(event="sample-hack-2026")["results"])
        self.assertTrue(self.gallery(event=DEMO_EVENT_SLUG)["results"])

    def test_an_unknown_event_filter_is_404(self):
        response = self.client.get("/api/v1/projects", {"event": "no-such-event"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "event_not_found")

    def test_the_gallery_is_a_constant_number_of_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/v1/projects")
        self.assertEqual(response.status_code, 200)
        # Anonymous, so: the count and the page. Nothing per row.
        self.assertEqual(len(queries.captured_queries), 2)

    # --- the HTML the checker reads --------------------------------------

    def test_the_server_rendered_gallery_carries_the_fixture_titles_sorted(self):
        response = self.client.get("/projects")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        listed = [row["title"] for row in self.gallery(page_size=100)["results"]]
        first_three = sorted(listed)[:3]
        self.assertIn("Glass Signal", listed)
        positions = [body.index(title) for title in first_three]
        self.assertEqual(positions, sorted(positions), first_three)
        self.assertEqual(response.context["page_obj"].paginator.per_page, 48)

    def test_the_duplicate_fixture_title_appears_once(self):
        # prj_07 was superseded by prj_41 for the same team and title.
        self.assertEqual(Project.objects.filter(title="Dry Harbour").count(), 2)
        superseded = Project.objects.get(public_id="prj_07")
        self.assertEqual(superseded.status, ProjectStatus.SUPERSEDED)
        body = self.client.get("/projects").content.decode()
        self.assertIn("/projects/prj_41", body)
        self.assertNotIn("/projects/prj_07", body)
        listed = {row["public_id"] for row in self.gallery()["results"]}
        self.assertIn("prj_41", listed)
        self.assertNotIn("prj_07", listed)

    def test_the_event_scoped_gallery_page_only_shows_that_event(self):
        from core.bootstrap import DEMO_EVENT_SLUG
        response = self.client.get(f"/events/{DEMO_EVENT_SLUG}/projects")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Signal Garden")
        self.assertNotContains(response, "Glass Signal")
