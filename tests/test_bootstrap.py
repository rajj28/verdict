"""core.bootstrap: repeated boots must not duplicate or reset anything."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import ApiToken, User
from accounts.tokens import authenticate_token
from core.bootstrap import DEMO_EVENT_SLUG, DEMO_PASSWORD, banner, bootstrap
from events.models import CustomQuestion, Event, EventRole, Prize, Role, Track
from judging.models import Criterion, Review, Rubric
from projects.models import Project, ProjectStatus
from teams.models import Team, TeamMember

DEMO_TOKEN_EXPECTATIONS = (
    ("vd_demo_admin_c0ffee5eed01", "admin@verdict.local"),
    ("vd_demo_organizer_7f2a91c4e0b3", "organizer@verdict.local"),
    ("vd_demo_judge_a_91bc5d2e8f10", "diego.herrera@example.org"),
    ("vd_demo_judge_b_44de0a7c3b92", "jonas.vogel@example.org"),
    ("vd_demo_participant_2e88f1d4a6c5", "priya1@example.org"),
)


def snapshot_counts() -> dict:
    return {
        "users": User.objects.count(),
        "events": Event.objects.count(),
        "tracks": Track.objects.count(),
        "prizes": Prize.objects.count(),
        "questions": CustomQuestion.objects.count(),
        "teams": Team.objects.count(),
        "members": TeamMember.objects.count(),
        "projects": Project.objects.count(),
        "reviews": Review.objects.count(),
        "roles": EventRole.objects.count(),
        "rubrics": Rubric.objects.count(),
        "criteria": Criterion.objects.count(),
        "tokens": ApiToken.objects.count(),
    }


class BootstrapDemoTests(TestCase):
    @override_settings(DEMO_MODE=True)
    def setUp(self):
        self.first = bootstrap()

    @override_settings(DEMO_MODE=True)
    def test_first_boot_seeds_the_fixture_event_and_the_demo_event(self):
        self.assertTrue(self.first.imported)
        self.assertTrue(self.first.demo_event_created)
        self.assertEqual(
            sorted(Event.objects.values_list("slug", flat=True)),
            ["demo-hack", "sample-hack-2026"],
        )
        self.assertEqual(Project.objects.filter(event__slug="sample-hack-2026").count(), 41)
        self.assertEqual(Project.objects.filter(event__slug=DEMO_EVENT_SLUG).count(), 5)

    @override_settings(DEMO_MODE=True)
    def test_second_boot_changes_nothing(self):
        before = snapshot_counts()
        second = bootstrap()
        self.assertFalse(second.imported)
        self.assertFalse(second.demo_event_created)
        self.assertEqual(snapshot_counts(), before)

    @override_settings(DEMO_MODE=True)
    def test_running_the_management_command_twice_is_safe(self):
        before = snapshot_counts()
        for _ in range(2):
            call_command("bootstrap", stdout=StringIO())
        self.assertEqual(snapshot_counts(), before)

    @override_settings(DEMO_MODE=True)
    def test_demo_event_shape(self):
        event = Event.objects.get(slug=DEMO_EVENT_SLUG)
        self.assertTrue(event.submissions_open_at < event.submissions_close_at)
        self.assertEqual((event.submissions_close_at - event.submissions_open_at).days, 14)
        self.assertIsNone(event.scoring_locked_at)
        self.assertEqual(event.tracks.count(), 3)
        self.assertEqual(event.prizes.count(), 2)
        self.assertEqual(event.questions.count(), 2)
        self.assertEqual(event.questions.filter(is_public=True).count(), 1)
        self.assertEqual(event.questions.filter(is_active=True).count(), 2)
        self.assertEqual(event.roles.filter(role=Role.JUDGE).count(), 3)
        for judge in event.roles.filter(role=Role.JUDGE):
            self.assertEqual(judge.tracks.count(), 2)
        self.assertEqual(event.roles.filter(role=Role.PARTICIPANT).count(), 5)
        # Judging only opens once the organizer closes submissions, so no reviews exist.
        self.assertEqual(Review.objects.filter(event=event).count(), 0)
        self.assertFalse(event.projects.filter(status=ProjectStatus.DRAFT).exists())

    @override_settings(DEMO_MODE=True)
    def test_demo_event_rubric_has_four_unequal_weights(self):
        rubric = Event.objects.get(slug=DEMO_EVENT_SLUG).rubric
        criteria = list(rubric.criteria.order_by("position"))
        self.assertEqual(len(criteria), 4)
        self.assertEqual(len({c.weight for c in criteria}), 4)

    @override_settings(DEMO_MODE=True)
    def test_organizer_organizes_both_events(self):
        organizer = User.objects.get(email="organizer@verdict.local")
        self.assertTrue(organizer.is_host)
        self.assertFalse(organizer.is_admin)
        for event in Event.objects.all():
            self.assertTrue(
                event.roles.filter(user=organizer, role=Role.ORGANIZER).exists(),
                f"{event.slug} has no organizer role",
            )

    @override_settings(DEMO_MODE=True)
    def test_admin_is_admin_and_host(self):
        admin = User.objects.get(email="admin@verdict.local")
        self.assertTrue(admin.is_admin)
        self.assertTrue(admin.is_host)
        self.assertFalse(admin.is_staff)

    @override_settings(DEMO_MODE=True)
    def test_demo_tokens_authenticate_to_the_right_users(self):
        for plaintext, email in DEMO_TOKEN_EXPECTATIONS:
            user = authenticate_token(plaintext)
            self.assertIsNotNone(user, f"{plaintext} does not authenticate")
            self.assertEqual(user.email, email)
            self.assertTrue(ApiToken.objects.filter(user=user, is_demo=True).exists())

    @override_settings(DEMO_MODE=True)
    def test_every_demo_token_is_flagged_and_counted(self):
        self.assertEqual(ApiToken.objects.filter(is_demo=True).count(), 5)
        self.assertEqual(len(self.first.tokens), 5)

    @override_settings(DEMO_MODE=True)
    def test_seeded_users_share_the_demo_password(self):
        self.assertTrue(User.objects.get(email="priya1@example.org").check_password(DEMO_PASSWORD))
        self.assertTrue(User.objects.get(email="organizer@verdict.local").check_password(DEMO_PASSWORD))
        self.assertEqual(
            User.objects.filter(password=User.objects.get(email="priya1@example.org").password).count(),
            121,
        )

    @override_settings(DEMO_MODE=True)
    def test_a_revoked_demo_token_is_reactivated_on_the_next_boot(self):
        plaintext = "vd_demo_judge_a_91bc5d2e8f10"
        # A judge revoking a demo token while exploring must not break run.py later.
        ApiToken.objects.filter(key_hash__in=[ApiToken.objects.get(user__email="diego.herrera@example.org")
                                               .key_hash]).update(revoked_at="2026-09-01T00:00:00Z")
        self.assertIsNone(authenticate_token(plaintext))
        bootstrap()
        self.assertIsNotNone(authenticate_token(plaintext))

    @override_settings(DEMO_MODE=True)
    def test_a_deactivated_demo_user_is_reactivated_on_the_next_boot(self):
        User.objects.filter(email="jonas.vogel@example.org").update(is_active=False)
        bootstrap()
        self.assertTrue(User.objects.get(email="jonas.vogel@example.org").is_active)
        self.assertIsNotNone(authenticate_token("vd_demo_judge_b_44de0a7c3b92"))

    @override_settings(DEMO_MODE=True)
    def test_banner_lists_every_demo_login(self):
        text = banner(self.first)
        for plaintext, email in DEMO_TOKEN_EXPECTATIONS:
            self.assertIn(plaintext, text)
            self.assertIn(email, text)
        self.assertIn(DEMO_PASSWORD, text)
        self.assertIn("41 projects", text)

    @override_settings(DEMO_MODE=True)
    def test_management_command_prints_the_banner(self):
        out = StringIO()
        call_command("bootstrap", stdout=out)
        self.assertIn("VERDICT is running at http://localhost:8080", out.getvalue())


class BootstrapWithoutDemoModeTests(TestCase):
    @override_settings(DEMO_MODE=False)
    def test_no_demo_tokens_exist(self):
        report = bootstrap()
        self.assertTrue(report.imported)
        self.assertEqual(report.tokens, [])
        self.assertEqual(ApiToken.objects.filter(is_demo=True).count(), 0)
        self.assertEqual(ApiToken.objects.count(), 0)
        for plaintext, _email in DEMO_TOKEN_EXPECTATIONS:
            self.assertIsNone(authenticate_token(plaintext))

    @override_settings(DEMO_MODE=False)
    def test_seeded_users_get_unusable_passwords(self):
        bootstrap()
        for email in ("priya1@example.org", "organizer@verdict.local", "admin@verdict.local"):
            user = User.objects.get(email=email)
            self.assertFalse(user.has_usable_password(), f"{email} can still be logged into")
            self.assertFalse(user.check_password(DEMO_PASSWORD))

    @override_settings(DEMO_MODE=False)
    def test_both_events_are_still_seeded(self):
        bootstrap()
        self.assertEqual(
            sorted(Event.objects.values_list("slug", flat=True)),
            ["demo-hack", "sample-hack-2026"],
        )
        self.assertEqual(Project.objects.filter(event__slug="sample-hack-2026").count(), 41)
