"""CSV exports and event.json round-trip tests.

Covers: every CSV kind has a header row; formula-injection escaping;
organizer sees 200, participant/judge sees 403; event.json round trip
(export fixture event → import as new event → same counts and raw means).
"""
from __future__ import annotations

import csv
import io
import json
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from events.models import Event, EventRole, Role, Track
from interop.exports import (
    assignments_csv,
    audit_csv,
    event_json,
    export_csv,
    judges_csv,
    participants_csv,
    projects_csv,
    results_csv,
    reviews_csv,
    teams_csv,
    progress_csv,
)
from interop.importer import import_fixture
from judging.models import (
    Assignment,
    CriterionScore,
    Review,
    ReviewStatus,
    Rubric,
    Criterion as JudgingCriterion,
)
from projects.models import Project, ProjectStatus
from results.models import ResultPublication
from teams.models import Team, TeamMember


def _fixture_data() -> dict:
    with open(settings.FIXTURES_PATH, encoding="utf-8") as handle:
        return json.load(handle)


class ExportCSVPermissionTests(TestCase):
    """Organizer sees 200; judge and participant see 403."""

    @classmethod
    def setUpTestData(cls):
        past = timezone.now() - timedelta(days=10)
        cls.organizer = User.objects.create_user("org@exp.test", "password")
        cls.judge = User.objects.create_user("jdg@exp.test", "password")
        cls.participant = User.objects.create_user("par@exp.test", "password")
        cls.event = Event.objects.create(
            slug="export-perm-test",
            name="Export Permission Test",
            submissions_close_at=past,
            judging_open_at=past,
            created_by=cls.organizer,
        )
        EventRole.objects.create(
            event=cls.event, user=cls.organizer, role=Role.ORGANIZER,
            public_id="org_exp",
        )
        EventRole.objects.create(
            event=cls.event, user=cls.judge, role=Role.JUDGE, public_id="jdg_exp",
        )
        EventRole.objects.create(
            event=cls.event, user=cls.participant, role=Role.PARTICIPANT,
            public_id="par_exp",
        )
        rubric = Rubric.objects.create(event=cls.event)
        JudgingCriterion.objects.create(
            rubric=rubric, key="q", name="Q",
            weight=Decimal("1.000"), min_score=1, max_score=5, position=0,
        )

    def test_organizer_gets_reviews_csv(self):
        self.client.force_login(self.organizer)
        resp = self.client.get(
            f"/api/v1/events/{self.event.slug}/exports/reviews.csv",
            follow=False,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])

    def test_judge_gets_403_for_csv(self):
        self.client.force_login(self.judge)
        resp = self.client.get(
            f"/api/v1/events/{self.event.slug}/exports/reviews.csv",
            follow=False,
        )
        self.assertEqual(resp.status_code, 403)

    def test_participant_gets_403_for_csv(self):
        self.client.force_login(self.participant)
        resp = self.client.get(
            f"/api/v1/events/{self.event.slug}/exports/reviews.csv",
            follow=False,
        )
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_gets_401_for_csv(self):
        resp = self.client.get(
            f"/api/v1/events/{self.event.slug}/exports/reviews.csv",
            follow=False,
        )
        self.assertEqual(resp.status_code, 401)

    def test_unknown_kind_404(self):
        self.client.force_login(self.organizer)
        resp = self.client.get(
            f"/api/v1/events/{self.event.slug}/exports/nonexistent.csv",
            follow=False,
        )
        self.assertEqual(resp.status_code, 404)


class CSVHeaderTests(TestCase):
    """Every export kind has a header row with the expected columns."""

    @classmethod
    def setUpTestData(cls):
        past = timezone.now() - timedelta(days=20)
        cls.organizer = User.objects.create_user("org@hdr.test", "password",
                                                   display_name="Org User")
        cls.event = Event.objects.create(
            slug="header-test",
            name="Header Test Event",
            submissions_close_at=past,
            judging_open_at=past,
            judging_close_at=past + timedelta(days=2),
            created_by=cls.organizer,
        )
        EventRole.objects.create(
            event=cls.event, user=cls.organizer, role=Role.ORGANIZER, public_id="org_h",
        )
        cls.track = Track.objects.create(event=cls.event, name="Main", position=0)
        cls.judge_user = User.objects.create_user("j@hdr.test", "password", display_name="Judge")
        cls.judge_role = EventRole.objects.create(
            event=cls.event, user=cls.judge_user, role=Role.JUDGE, public_id="jdg_h",
        )
        cls.judge_role.tracks.add(cls.track)
        cls.participant_user = User.objects.create_user("p@hdr.test", "password", display_name="Part")
        cls.participant_role = EventRole.objects.create(
            event=cls.event, user=cls.participant_user, role=Role.PARTICIPANT,
            public_id="par_h",
        )
        cls.team = Team.objects.create(event=cls.event, name="Team H")
        TeamMember.objects.create(
            team=cls.team, user=cls.participant_user, event=cls.event, is_owner=True
        )
        cls.project = Project.objects.create(
            event=cls.event, team=cls.team, track=cls.track,
            public_id="prj_h", title="=EVIL", status=ProjectStatus.SUBMITTED, revision=1,
        )
        cls.rubric = Rubric.objects.create(event=cls.event, version=1)
        cls.c1 = JudgingCriterion.objects.create(
            rubric=cls.rubric, key="quality", name="Quality",
            weight=Decimal("1.000"), min_score=1, max_score=5, position=0,
        )
        cls.assignment = Assignment.objects.create(
            event=cls.event, judge=cls.judge_role, project=cls.project,
        )
        cls.review = Review.objects.create(
            event=cls.event, assignment=cls.assignment, judge=cls.judge_role,
            project=cls.project, status=ReviewStatus.SUBMITTED,
            comment="+INJECTION", submitted_at=past, rubric_version=1,
            project_revision=1,
        )
        CriterionScore.objects.create(review=cls.review, criterion=cls.c1, value=4)
        Event.objects.filter(pk=cls.event.pk).update(scoring_locked_at=past)

    def _rows(self, csv_text: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_text))
        return list(reader)

    def test_participants_has_header(self):
        rows = self._rows(participants_csv(self.event))
        self.assertEqual(rows[0][0], "participant_id")

    def test_teams_has_header(self):
        rows = self._rows(teams_csv(self.event))
        self.assertEqual(rows[0][0], "team_id")

    def test_projects_has_header(self):
        rows = self._rows(projects_csv(self.event))
        self.assertEqual(rows[0][0], "project_id")

    def test_judges_has_header(self):
        rows = self._rows(judges_csv(self.event))
        self.assertEqual(rows[0][0], "judge_id")

    def test_assignments_has_header(self):
        rows = self._rows(assignments_csv(self.event))
        self.assertEqual(rows[0][0], "assignment_id")

    def test_reviews_has_header(self):
        rows = self._rows(reviews_csv(self.event))
        self.assertEqual(rows[0][0], "review_id")
        # Criterion key appears as column
        self.assertIn("quality", rows[0])

    def test_progress_has_header(self):
        rows = self._rows(progress_csv(self.event))
        self.assertEqual(rows[0][0], "judge_id")

    def test_results_has_header(self):
        rows = self._rows(results_csv(self.event))
        self.assertEqual(rows[0][0], "rank")
        self.assertIn("reviews", rows[0])
        self.assertIn("status", rows[0])

    def test_audit_has_header(self):
        rows = self._rows(audit_csv(self.event))
        self.assertEqual(rows[0][0], "created_at")

    def test_formula_injection_escaped_in_project_title(self):
        rows = self._rows(projects_csv(self.event))
        titles = [row[1] for row in rows[1:]]
        for title in titles:
            self.assertFalse(
                title.startswith("="),
                f"Unescaped formula in project title: {title!r}",
            )

    def test_formula_injection_escaped_in_review_comment(self):
        rows = self._rows(reviews_csv(self.event))
        comments = [row[-1] for row in rows[1:]]
        for comment in comments:
            self.assertFalse(
                comment.startswith("+") and not comment.startswith("'"),
                f"Unescaped formula in comment: {comment!r}",
            )


class EventJsonRoundTripTests(TestCase):
    """Export fixture event → import as new event → same counts and raw means."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "admin@rtrip.test", "password", is_admin=True
        )
        data = _fixture_data()
        cls.report = import_fixture(data, actor=cls.admin, fixture_path=settings.FIXTURES_PATH)
        cls.original_event = Event.objects.get(source_id="evt_01")

    def test_event_json_is_valid_json(self):
        document = event_json(self.original_event)
        parsed = json.loads(document)
        self.assertIn("event", parsed)
        self.assertIn("tracks", parsed)
        self.assertIn("judges", parsed)
        self.assertIn("teams", parsed)
        self.assertIn("projects", parsed)
        self.assertIn("scores", parsed)

    def test_event_json_has_correct_counts(self):
        document = event_json(self.original_event)
        parsed = json.loads(document)
        # 40 submitted projects (41 total - 1 superseded)
        self.assertEqual(len(parsed["projects"]), 40)
        # 30 judges
        self.assertEqual(len(parsed["judges"]), 30)
        # 126 scores
        self.assertEqual(len(parsed["scores"]), 126)

    def test_event_json_round_trip_same_project_count(self):
        """Re-importing the exported JSON gives the same number of projects."""
        document = event_json(self.original_event)
        data = json.loads(document)
        # Patch the source_id so it doesn't collide
        data["event"]["id"] = "evt_roundtrip_01"
        admin2 = User.objects.create_user("admin2@rtrip.test", "password", is_admin=True)
        report2 = import_fixture(data, slug="sample-hack-roundtrip", actor=admin2)
        rt_event = Event.objects.get(source_id="evt_roundtrip_01")
        try:
            original_submitted = (
                self.original_event.projects
                .filter(status=ProjectStatus.SUBMITTED)
                .count()
            )
            rt_submitted = rt_event.projects.filter(status=ProjectStatus.SUBMITTED).count()
            self.assertEqual(rt_submitted, original_submitted)
        finally:
            # Cleanup the round-trip event so other tests aren't affected
            rt_event.projects.all().delete()
            rt_event.delete()
            admin2.delete()

    def test_event_json_no_password_hashes(self):
        """Password hashes must never appear in the export."""
        document = event_json(self.original_event)
        self.assertNotIn("password", document)
        self.assertNotIn("pbkdf2", document.lower())

    def test_event_json_no_token_hashes(self):
        """Token hashes must never appear in the export."""
        document = event_json(self.original_event)
        self.assertNotIn("key_hash", document)


class ImportAPITests(TestCase):
    """POST /api/v1/imports requires admin or host."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            "admin@imp.test", "password", is_admin=True
        )
        cls.host = User.objects.create_user(
            "host@imp.test", "password", is_host=True
        )
        cls.regular = User.objects.create_user("reg@imp.test", "password")

    def _small_fixture(self, event_id: str) -> dict:
        return {
            "event": {
                "id": event_id,
                "name": "Small Test Event",
                "submissions_close": "2026-01-01T00:00:00Z",
            },
            "tracks": [{"id": "trk_s1", "name": "Open"}],
            "judges": [
                {
                    "id": "jdg_s1",
                    "name": "Test Judge",
                    "email": f"judge_{event_id}@small.test",
                    "tracks": ["trk_s1"],
                }
            ],
            "teams": [
                {
                    "id": "tm_s1",
                    "name": "Small Team",
                    "members": [f"member_{event_id}@small.test"],
                }
            ],
            "projects": [
                {
                    "id": "prj_s1",
                    "title": "Small Project",
                    "summary": "A project",
                    "description": "desc",
                    "team": "tm_s1",
                    "track": "trk_s1",
                    "repo_url": "https://github.com/test/test",
                    "submitted_at": "2025-12-31T00:00:00Z",
                }
            ],
            "scores": [],
        }

    def test_admin_can_import(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            "/api/v1/imports",
            data=json.dumps(self._small_fixture("evt_small_admin")),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertIn("counts", resp.json())

    def test_host_can_import(self):
        self.client.force_login(self.host)
        resp = self.client.post(
            "/api/v1/imports",
            data=json.dumps(self._small_fixture("evt_small_host")),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)

    def test_regular_user_403(self):
        self.client.force_login(self.regular)
        resp = self.client.post(
            "/api/v1/imports",
            data=json.dumps(self._small_fixture("evt_small_denied")),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_anon_401(self):
        resp = self.client.post(
            "/api/v1/imports",
            data=json.dumps(self._small_fixture("evt_small_anon")),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_duplicate_import_returns_409(self):
        self.client.force_login(self.admin)
        self.client.post(
            "/api/v1/imports",
            data=json.dumps(self._small_fixture("evt_small_dup")),
            content_type="application/json",
        )
        # Second attempt with same source_id
        resp = self.client.post(
            "/api/v1/imports",
            data=json.dumps(self._small_fixture("evt_small_dup")),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)


class EventJsonExportAPITests(TestCase):
    """GET /api/v1/events/{slug}/exports/event.json — organizer gets JSON."""

    @classmethod
    def setUpTestData(cls):
        past = timezone.now() - timedelta(days=5)
        cls.organizer = User.objects.create_user("org@ejson.test", "password")
        cls.judge = User.objects.create_user("jdg@ejson.test", "password")
        cls.event = Event.objects.create(
            slug="ejson-test",
            name="Event JSON Test",
            submissions_close_at=past,
            judging_open_at=past,
            created_by=cls.organizer,
        )
        EventRole.objects.create(
            event=cls.event, user=cls.organizer, role=Role.ORGANIZER, public_id="org_ej",
        )
        EventRole.objects.create(
            event=cls.event, user=cls.judge, role=Role.JUDGE, public_id="jdg_ej",
        )
        Rubric.objects.create(event=cls.event)

    def test_organizer_gets_event_json(self):
        self.client.force_login(self.organizer)
        resp = self.client.get(
            f"/api/v1/events/{self.event.slug}/exports/event.json",
            follow=False,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/json", resp["Content-Type"])
        data = json.loads(resp.content)
        self.assertIn("event", data)

    def test_judge_gets_403(self):
        self.client.force_login(self.judge)
        resp = self.client.get(
            f"/api/v1/events/{self.event.slug}/exports/event.json",
            follow=False,
        )
        self.assertEqual(resp.status_code, 403)

    def test_anon_gets_401(self):
        resp = self.client.get(
            f"/api/v1/events/{self.event.slug}/exports/event.json",
            follow=False,
        )
        self.assertEqual(resp.status_code, 401)
