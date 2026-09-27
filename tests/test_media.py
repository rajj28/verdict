"""Media is behind the same policy as the data it belongs to (BUILD-SEC section 16).

Uploads are never served straight off the filesystem: core.views.media resolves the
owning project and asks projects.policy.visible_project. Everyone else gets a 404,
not a 403, so the response never confirms that the file exists.
"""
import tempfile
from pathlib import Path

from accounts.models import User
from core.bootstrap import bootstrap
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from events.models import Event, EventRole
from judging.models import Assignment
from projects.models import Project, ProjectImage, ProjectStatus
from teams.models import Team, TeamMember

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
       b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00"
       b"\x00\x00IEND\xaeB`\x82")


@override_settings(DEMO_MODE=True)
class MediaAuthorizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        bootstrap()
        cls.event = Event.objects.get(slug="sample-hack-2026")
        cls.submitted = Project.objects.get(public_id="prj_01")
        # A draft is private to its team, so it is the interesting case here.
        owner = User.objects.create_user("media-owner@example.org", "verdict-demo",
                                        display_name="Media Owner")
        team = Team.objects.create(event=cls.event, name="Media Test Team", created_by=owner)
        TeamMember.objects.create(team=team, user=owner, event=cls.event, is_owner=True)
        cls.draft = Project.objects.create(
            event=cls.event, team=team, title="Media Test Draft", status=ProjectStatus.DRAFT
        )
        cls.owner = owner
        cls.outsider = User.objects.get(email="lena2@example.org")

    def setUp(self):
        # ignore_cleanup_errors: FileResponse may still hold an open handle.
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.override = override_settings(MEDIA_ROOT=Path(self._tmp.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self._tmp.cleanup)
        self.public_name = self._attach(self.submitted, "public.png")
        self.private_name = self._attach(self.draft, "draft.png")

    def _attach(self, project: Project, filename: str) -> str:
        project.thumbnail.save(filename, SimpleUploadedFile(filename, PNG, "image/png"),
                               save=True)
        return project.thumbnail.name

    def _get(self, name: str, user=None):
        if user is not None:
            # Media is served by a plain Django view, so it is the browser session
            # that authenticates here: an <img> tag never sends an Authorization header.
            self.client.force_login(user)
        return self.client.get(f"/media/{name}", follow=False)

    def test_a_public_projects_image_is_public(self):
        response = self._get(self.public_name)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")

    def test_a_private_projects_image_is_404_for_visitors(self):
        self.assertEqual(self._get(self.private_name).status_code, 404)
        self.assertEqual(self._get(self.private_name, self.outsider).status_code, 404)

    def test_the_team_sees_its_own_private_image(self):
        self.assertEqual(self._get(self.private_name, self.owner).status_code, 200)

    def test_an_organizer_sees_a_private_image(self):
        organizer = User.objects.get(email="organizer@verdict.local")
        self.assertEqual(self._get(self.private_name, organizer).status_code, 200)

    def test_an_assigned_judge_sees_a_private_image(self):
        judge = EventRole.objects.get(event=self.event, public_id="jdg_01")
        Assignment.objects.create(event=self.event, judge=judge, project=self.draft)
        self.assertEqual(self._get(self.private_name, judge.user).status_code, 200)

    def test_a_judge_without_an_assignment_does_not(self):
        other = EventRole.objects.get(event=self.event, public_id="jdg_02")
        self.assertEqual(self._get(self.private_name, other.user).status_code, 404)

    def test_an_unknown_name_is_404(self):
        self.assertEqual(self._get("thumbnails/nope.png").status_code, 404)

    def test_path_traversal_is_refused(self):
        response = self.client.get("/media/../../manage.py", follow=False)
        self.assertEqual(response.status_code, 404)

    def test_a_gallery_image_is_authorized_through_its_project(self):
        image = ProjectImage(image=SimpleUploadedFile("shot.png", PNG, "image/png"))
        image.project = self.draft
        image.save()
        self.assertEqual(self._get(image.image.name, self.owner).status_code, 200)
        self.assertEqual(self._get(image.image.name, self.outsider).status_code, 404)
