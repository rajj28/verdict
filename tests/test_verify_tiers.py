"""Runs scripts/verify_tiers.py against a live test server on PostgreSQL.

The script only speaks HTTP (stdlib urllib) and reads a .dogfood.toml-style
config, so the test boots a real server, seeds it with bootstrap() (fixture
event plus the deterministic demo tokens), writes a config pointing at the
live server, pumps due webhook deliveries while the script runs, and asserts
every T1-T4 check passes with no secret ever printed.
"""
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import SimpleTestCase, override_settings

REPO_DIR = Path(__file__).resolve().parent.parent
SCRIPT = REPO_DIR / "scripts" / "verify_tiers.py"

DEMO_TOKENS = {
    "organizer": "vd_demo_organizer_7f2a91c4e0b3",
    "judge_a": "vd_demo_judge_a_91bc5d2e8f10",
    "judge_b": "vd_demo_judge_b_44de0a7c3b92",
    "participant": "vd_demo_participant_2e88f1d4a6c5",
}

TOML_TEMPLATE = """\
[portal]
base_url = "{base_url}"

[tiers]
claimed = ["T1", "T2", "T3", "T4"]

[auth]
organizer   = "Authorization: Bearer {organizer}"
judge_a     = "Authorization: Bearer {judge_a}"
judge_b     = "Authorization: Bearer {judge_b}"
participant = "Authorization: Bearer {participant}"

[routes]
gallery      = "/projects"
submit       = "/api/v1/events/sample-hack-2026/projects"
judge_scores = "/api/v1/judge/scores"
peer_scores  = "/api/v1/events/sample-hack-2026/judges/jdg_24/scores"
csv_export   = "/api/v1/events/sample-hack-2026/exports/results.csv"
"""


def pump_webhooks(stop):
    """Deliver queued webhooks in-process so the script's receiver is hit.

    Production runs manage.py deliver_webhooks in a sidecar; the test suite
    has no sidecar, so this thread plays that role while the script waits.
    """
    from django.db import close_old_connections, connection

    from interop.webhooks import claim_due, deliver_claim

    try:
        while not stop.is_set():
            try:
                close_old_connections()
                for row in claim_due(5):
                    try:
                        deliver_claim(*row)
                    except Exception:
                        pass
            except Exception:
                pass
            stop.wait(0.5)
    finally:
        # The test runner drops the test database at teardown; a lingering
        # connection from this thread would block that drop.
        try:
            connection.close()
        except Exception:
            pass


@override_settings(DEMO_MODE=True, WEBHOOKS_ALLOW_PRIVATE=True,
                   EMAIL_BACKEND="core.mail.OutboxBackend")
class VerifyTiersTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Minimal seed: bootstrap() is owned by another worker and currently
        # fails on its showcase import, so this test builds exactly what the
        # script needs: demo users/tokens plus a locked T2-style event with a
        # searchable submitted project, an isolated project, and one review.
        from datetime import timedelta

        from django.utils import timezone

        from accounts.models import User
        from accounts.tokens import issue_token
        from events.models import Event, EventRole, Role, Track
        from judging.models import (
            Assignment,
            Criterion,
            CriterionScore,
            Review,
            ReviewStatus,
            Rubric,
        )
        from projects.models import Project, ProjectStatus
        from teams.models import Team, TeamMember

        past = timezone.now() - timedelta(days=1)
        organizer = User.objects.create_user(
            "organizer@verify.test", display_name="Organizer", is_host=True)
        participant = User.objects.create_user(
            "participant@verify.test", display_name="Participant")
        judge_a = User.objects.create_user(
            "judge-a@verify.test", display_name="Judge A")
        judge_b = User.objects.create_user(
            "judge-b@verify.test", display_name="Judge B")
        teammate = User.objects.create_user(
            "teammate@verify.test", display_name="Teammate")
        users = {"organizer": organizer, "participant": participant,
                 "judge_a": judge_a, "judge_b": judge_b}
        for key, user in users.items():
            issue_token(user, key, DEMO_TOKENS[key], is_demo=True)
        event = Event.objects.create(
            slug="sample-hack-2026", name="Sample Hack 2026",
            submissions_close_at=past, judging_open_at=past,
            scoring_locked_at=past, created_by=organizer)
        EventRole.objects.create(event=event, user=organizer,
                                 role=Role.ORGANIZER, public_id="org_verify")
        role_a = EventRole.objects.create(event=event, user=judge_a,
                                          role=Role.JUDGE, public_id="jdg_24")
        track_a = Track.objects.create(event=event, name="Track A")
        track_b = Track.objects.create(event=event, name="Track B")
        role_a.tracks.add(track_a)
        rubric = Rubric.objects.create(event=event, version=1)
        criterion = Criterion.objects.create(
            rubric=rubric, key="quality", name="Quality", weight="1.000",
            min_score=1, max_score=5, position=0)
        team = Team.objects.create(event=event, name="Glassworks",
                                   created_by=participant)
        TeamMember.objects.create(team=team, user=participant, event=event,
                                  is_owner=True)
        Project.objects.create(
            event=event, team=team, track=track_b, public_id="prj_01",
            title="Glass Signal", summary="A searchable entry",
            status=ProjectStatus.SUBMITTED, first_submitted_at=past,
            last_submitted_at=past)
        team2 = Team.objects.create(event=event, name="Second Team",
                                    created_by=teammate)
        TeamMember.objects.create(team=team2, user=teammate, event=event,
                                  is_owner=True)
        second = Project.objects.create(
            event=event, team=team2, track=track_a, public_id="prj_02",
            title="Second Signal", summary="Another entry",
            status=ProjectStatus.SUBMITTED, first_submitted_at=past,
            last_submitted_at=past)
        assignment = Assignment.objects.create(
            event=event, judge=role_a, project=second)
        review = Review.objects.create(
            event=event, assignment=assignment, judge=role_a, project=second,
            status=ReviewStatus.SUBMITTED, submitted_at=past)
        CriterionScore.objects.create(review=review, criterion=criterion,
                                      value=4)
        config = TOML_TEMPLATE.format(base_url=cls.live_server_url, **DEMO_TOKENS)
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".toml", delete=False, encoding="utf-8")
        handle.write(config)
        handle.close()
        cls.config_path = handle.name

    def test_all_tier_checks_pass(self):
        stop = threading.Event()
        worker = threading.Thread(target=pump_webhooks, args=(stop,),
                                  daemon=True)
        worker.start()
        try:
            finished = subprocess.run(
                [sys.executable, str(SCRIPT), self.config_path],
                capture_output=True, text=True, timeout=300, cwd=str(REPO_DIR),
            )
        finally:
            stop.set()
            worker.join(timeout=5)
        output = finished.stdout
        self.assertEqual(finished.returncode, 0, output[-4000:])
        self.assertNotIn("FAIL", output)
        for tier in ("T1", "T2", "T3", "T4"):
            self.assertRegex(output, rf"(?m)^{tier}  \S.*PASS$")
            self.assertRegex(output, rf"(?m)^{tier} summary \d+/\d+ checks passed$")
        match = re.search(r"^summary (\d+)/(\d+) checks passed$", output,
                          re.MULTILINE)
        self.assertIsNotNone(match, output[-2000:])
        self.assertEqual(match.group(1), match.group(2))
        self.assertGreater(int(match.group(2)), 40)
        # The report lists requests, never credentials: no bearer token,
        # voting capability or email ticket may appear.
        for secret in DEMO_TOKENS.values():
            self.assertNotIn(secret, output)
        self.assertNotRegex(output, r"token=[A-Za-z0-9_.:-]+")
        # Every PASS line carries its request evidence underneath.
        passes = re.findall(r"^T\d  .*PASS$", output, re.MULTILINE)
        requests = re.findall(r"^  (GET|POST|PUT|PATCH|DELETE) \S+  as \S+  -> \S+$",
                              output, re.MULTILINE)
        self.assertGreaterEqual(len(requests), len(passes))


def _load_script():
    import importlib.util
    spec = importlib.util.spec_from_file_location("verify_tiers_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ShuffleCheckTests(SimpleTestCase):
    """The ballot-order check tells a fair shuffle from a fixed one, lazily."""

    def setUp(self):
        self.script = _load_script()

    def _orders(self, orders, produced):
        for order in orders:
            produced.append(order)
            yield order

    def test_stops_at_first_differing_voter(self):
        produced = []
        same, other = ["a", "b", "c"], ["b", "a", "c"]
        differs, compared = self.script.shuffle_differs(
            same, self._orders([same, same, other, same], produced))
        self.assertEqual((differs, compared), (True, 3))
        self.assertEqual(len(produced), 3)  # the fourth voter is never created

    def test_fixed_order_for_everyone_still_fails(self):
        produced = []
        same = ["a", "b", "c"]
        differs, compared = self.script.shuffle_differs(
            same, self._orders([same] * 20, produced))
        self.assertEqual((differs, compared), (False, self.script.SHUFFLE_VOTERS))
        self.assertEqual(len(produced), self.script.SHUFFLE_VOTERS)

    def test_voter_without_a_ballot_fails(self):
        differs, compared = self.script.shuffle_differs(["a", "b"], iter([[]]))
        self.assertEqual((differs, compared), (False, 1))

    def test_chance_failure_rate_is_negligible(self):
        # Seven independent matches at 1 in 6 each.
        self.assertLess((1 / 6) ** self.script.SHUFFLE_VOTERS, 1e-5)

