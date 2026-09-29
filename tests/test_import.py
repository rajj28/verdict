"""interop.importer: the fixture must land as one auditable, provenance-keeping event."""
import json
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import User
from audit.models import AuditEvent
from core.errors import ApiError
from events.models import Event, EventRole, Role, Track
from interop.importer import DEFAULT_SLUG, import_fixture
from judging.models import (
    Assignment,
    AssignmentBatch,
    AssignmentMethod,
    Criterion,
    CriterionScore,
    Review,
)
from projects.models import Project, ProjectRevision, ProjectStatus
from teams.models import Team, TeamMember


def fixture_data() -> dict:
    with open(settings.FIXTURES_PATH, encoding="utf-8") as handle:
        return json.load(handle)


class ImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("admin@example.org", "verdict-demo", is_admin=True)
        cls.data = fixture_data()
        cls.report = import_fixture(cls.data, actor=cls.admin, fixture_path=settings.FIXTURES_PATH)
        cls.event = Event.objects.get(source_id="evt_01")

    def projects(self):
        return Project.objects.filter(event=self.event)

    def test_event_windows_come_from_the_fixture(self):
        self.assertEqual(self.event.slug, DEFAULT_SLUG)
        self.assertEqual(self.event.name, "Sample Hack 2026")
        self.assertIsNone(self.event.submissions_open_at)
        self.assertEqual(self.event.submissions_close_at.isoformat(), "2026-03-01T18:00:00+00:00")
        # Judging opens when submissions close, and is still open for unfinished batches.
        self.assertEqual(self.event.judging_open_at, self.event.submissions_close_at)
        self.assertIsNone(self.event.judging_close_at)
        self.assertEqual(self.event.created_by, self.admin)

    def test_imported_ids_are_the_fixture_ids(self):
        self.assertTrue(self.projects().filter(public_id="prj_01").exists())
        self.assertTrue(Track.objects.filter(public_id="trk_04", event=self.event).exists())
        self.assertTrue(Team.objects.filter(public_id="tm_01").exists())
        self.assertTrue(Project.objects.filter(public_id="prj_01", source_id="prj_01").exists())

    def test_counts_match_the_fixture(self):
        self.assertEqual(self.projects().count(), 41)
        self.assertEqual(Team.objects.filter(event=self.event).count(), 40)
        self.assertEqual(EventRole.objects.filter(event=self.event, role=Role.JUDGE).count(), 30)
        self.assertEqual(Review.objects.filter(event=self.event, status="submitted").count(), 126)
        self.assertEqual(CriterionScore.objects.filter(review__event=self.event).count(), 378)
        self.assertEqual(Assignment.objects.filter(event=self.event).count(), 126)
        self.assertEqual(self.report.counts["projects"], 41)
        self.assertEqual(self.report.counts["teams"], 40)
        self.assertEqual(self.report.counts["reviews"], 126)

    def test_exactly_one_project_is_superseded(self):
        superseded = list(self.projects().filter(status=ProjectStatus.SUPERSEDED))
        self.assertEqual(len(superseded), 1)
        self.assertEqual(superseded[0].public_id, "prj_07")
        self.assertEqual(superseded[0].superseded_by.public_id, "prj_41")
        self.assertIn("prj_41", superseded[0].status_reason)
        self.assertEqual(self.projects().get(public_id="prj_41").status, ProjectStatus.SUBMITTED)
        # The team keeps exactly one active project, which the DB also enforces.
        self.assertEqual(
            self.projects().filter(team__public_id="tm_07",
                                   status__in=["draft", "submitted"]).count(), 1)

    def test_team_names_are_not_unique(self):
        # The fixture reuses names inside one event; identity is public_id.
        repeated = Team.objects.filter(event=self.event, name="StillTrail")
        self.assertEqual(sorted(t.public_id for t in repeated), ["tm_03", "tm_30", "tm_40"])
        self.assertEqual(Team.objects.filter(event=self.event, name="AmberSwitch").count(), 2)

    def test_judge_roles_keep_their_fixture_id_and_tracks(self):
        judge = EventRole.objects.get(event=self.event, public_id="jdg_24")
        self.assertEqual(judge.role, Role.JUDGE)
        self.assertEqual(judge.user.email, "diego.herrera@example.org")
        self.assertTrue(judge.tracks.exists())
        self.assertTrue(EventRole.objects.filter(event=self.event, public_id="jdg_07").exists())

    def test_first_member_of_each_team_is_the_owner(self):
        for team in Team.objects.filter(event=self.event):
            memberships = list(team.memberships.order_by("joined_at", "id"))
            self.assertEqual([m.is_owner for m in memberships].count(True), 1)
            self.assertTrue(memberships[0].is_owner)

    def test_one_team_per_person_per_event(self):
        for membership in TeamMember.objects.filter(event=self.event):
            self.assertEqual(
                TeamMember.objects.filter(event=self.event, user=membership.user).count(), 1)

    def test_projects_are_submitted_with_one_revision_and_a_receipt(self):
        project = self.projects().get(public_id="prj_01")
        self.assertEqual(project.status, ProjectStatus.SUBMITTED)
        self.assertEqual(project.revision, 1)
        self.assertEqual(project.first_submitted_at, project.last_submitted_at)
        self.assertEqual(project.first_submitted_at.isoformat(), "2026-02-27T04:08:00+00:00")
        revision = project.revisions.get()
        self.assertEqual(revision.number, 1)
        self.assertEqual(revision.snapshot["title"], "Glass Signal")
        self.assertEqual(len(revision.digest), 64)
        self.assertEqual(ProjectRevision.objects.filter(project__event=self.event).count(), 41)

    def test_rubric_is_functionality_quality_innovation(self):
        rubric = self.event.rubric
        criteria = list(rubric.criteria.order_by("position"))
        self.assertEqual([c.key for c in criteria], ["functionality", "quality", "innovation"])
        for criterion in criteria:
            self.assertEqual(criterion.min_score, 1)
            self.assertEqual(criterion.max_score, 5)
            self.assertEqual(criterion.weight, 1)
        self.assertEqual(Criterion.objects.filter(rubric=rubric).count(), 3)

    def test_scoring_is_locked_at_import(self):
        self.assertIsNotNone(self.event.scoring_locked_at)
        self.assertEqual(Event.objects.get(pk=self.event.pk).scoring_locked_at,
                         self.event.scoring_locked_at)

    def test_reviews_come_from_an_import_batch(self):
        batch = AssignmentBatch.objects.get(event=self.event)
        self.assertEqual(batch.method, AssignmentMethod.IMPORT)
        self.assertEqual(Assignment.objects.filter(batch=batch).count(), 126)
        review = Review.objects.get(event=self.event, judge__public_id="jdg_08",
                                    project__public_id="prj_01")
        self.assertEqual(review.source, "import")
        self.assertEqual(review.comment, "Runs clean.")
        self.assertEqual(review.scores.count(), 3)
        self.assertEqual(review.project_revision, 1)

    def test_report_lists_the_constant_scorer(self):
        self.assertEqual(self.report.constant_scorers, ["jdg_07"])
        self.assertEqual(self.report.as_dict()["constant_scorers"], ["jdg_07"])
        # jdg_07's three reviews are all the same numbers, so they add no ordering.
        values = {
            tuple(sorted((score.criterion.key, score.value) for score in review.scores.all()))
            for review in Review.objects.filter(judge__public_id="jdg_07").prefetch_related("scores")
        }
        self.assertEqual(len(values), 1)

    def test_report_lists_under_reviewed_projects(self):
        reviewed: dict[str, int] = {}
        for row in self.data["scores"]:
            reviewed[row["project"]] = reviewed.get(row["project"], 0) + 1
        expected = sorted(pid for pid, count in reviewed.items() if count < 3)
        self.assertEqual(self.report.under_reviewed, expected)
        self.assertEqual(len(expected), 8)
        for public_id in expected:
            count = Review.objects.filter(project__public_id=public_id).count()
            self.assertLess(count, self.event.reviews_per_project)

    def test_report_records_fixture_checksum_and_size(self):
        self.assertEqual(len(self.report.fixture_sha256), 64)
        self.assertGreater(self.report.fixture_bytes, 0)
        import hashlib

        with open(settings.FIXTURES_PATH, "rb") as handle:
            raw = handle.read()
        self.assertEqual(self.report.fixture_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.report.fixture_bytes, len(raw))

    def test_import_writes_an_audit_event_with_the_report(self):
        event = AuditEvent.objects.get(action="import.completed", event=self.event)
        self.assertEqual(event.target_type, "events.event")
        self.assertEqual(event.target_id, "sample-hack-2026")
        self.assertEqual(event.data["counts"], self.report.counts)
        self.assertEqual(event.data["superseded"][0]["project"], "prj_07")
        self.assertIn("41 projects", event.summary)

    def test_seeded_users_share_one_password_hash(self):
        seeded = User.objects.filter(email__endswith="@example.org").exclude(pk=self.admin.pk)
        self.assertEqual(seeded.count(), 121)
        self.assertEqual(seeded.values("password").distinct().count(), 1)
        judge = User.objects.get(email="diego.herrera@example.org")
        self.assertEqual(judge.display_name, "Diego Herrera")
        member = User.objects.get(email="priya1@example.org")
        self.assertEqual(member.display_name, "priya1")

    def test_a_second_import_of_the_same_fixture_is_refused(self):
        with self.assertRaises(ApiError) as caught:
            import_fixture(fixture_data(), actor=self.admin)
        self.assertEqual(caught.exception.code, "already_imported")
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.projects().count(), 41)

    def test_a_taken_slug_is_refused(self):
        other = fixture_data()
        other["event"]["id"] = "evt_02"
        with self.assertRaises(ApiError) as caught:
            import_fixture(other, slug=DEFAULT_SLUG, actor=self.admin)
        self.assertEqual(caught.exception.code, "slug_taken")
        self.assertEqual(caught.exception.status_code, 409)

    def test_import_rolls_back_completely_on_failure(self):
        broken = fixture_data()
        broken["event"]["id"] = "evt_02"
        broken["projects"].append({"id": "prj_99", "team": "tm_01", "track": "trk_01",
                                   "title": "Collides", "summary": "", "repo_url": "",
                                   "submitted_at": "2026-02-27T04:08:00Z"})
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                import_fixture(broken, slug="second-event", actor=self.admin)
        self.assertFalse(Event.objects.filter(slug="second-event").exists())
        self.assertFalse(Project.objects.filter(title="Collides").exists())


class RubricImportTests(TestCase):
    """An imported event is scored on the document's own rubric, and never half-scored."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("admin@example.org", "verdict-demo", is_admin=True)

    @staticmethod
    def document(tag: str, criteria: dict, **extra) -> dict:
        """One track, judge, team, project and score row, with ids unique to ``tag``."""
        data = {
            "event": {"id": f"evt_{tag}", "name": f"Rubric {tag}",
                      "submissions_close": "2026-03-01T18:00:00Z"},
            "tracks": [{"id": f"trk_{tag}", "name": "Main"}],
            "judges": [{"id": f"jdg_{tag}", "name": "Rae Judge", "email": f"rae-{tag}@example.org",
                        "tracks": [f"trk_{tag}"]}],
            "teams": [{"id": f"tm_{tag}", "name": f"Team {tag}", "members": [f"m-{tag}@example.org"]}],
            "projects": [{"id": f"prj_{tag}", "team": f"tm_{tag}", "track": f"trk_{tag}",
                          "title": f"Probe {tag}", "summary": "", "repo_url": "",
                          "submitted_at": "2026-02-27T04:08:00Z"}],
            "scores": [{"judge": f"jdg_{tag}", "project": f"prj_{tag}", "criteria": criteria,
                        "comment": ""}],
        }
        data.update(extra)
        return data

    def imported(self, data: dict):
        report = import_fixture(data, slug=data["event"]["id"].replace("_", "-"), actor=self.admin)
        event = Event.objects.get(source_id=data["event"]["id"])
        return report, event, list(Criterion.objects.filter(rubric__event=event).order_by("position"))

    def assert_results_compute(self, event, project_id: str):
        from results.services import preview

        rows = preview(event)["rows"]
        self.assertEqual([row["project_id"] for row in rows], [project_id])

    def test_a_declared_rubric_is_kept(self):
        rubric = {"criteria": [{"key": "impact", "name": "Impact", "weight": "2.500",
                                "min_score": 0, "max_score": 10}]}
        report, event, criteria = self.imported(self.document("r1", {"impact": 7}, rubric=rubric))
        self.assertEqual([(c.key, c.weight, c.min_score, c.max_score) for c in criteria],
                         [("impact", Decimal("2.500"), 0, 10)])
        self.assertEqual(report.counts["reviews"], 1)
        # Not clamped to the default 1-5 range.
        self.assertEqual(CriterionScore.objects.get(review__event=event).value, 7)
        self.assert_results_compute(event, "prj_r1")

    def test_scores_on_other_criteria_define_the_rubric(self):
        report, event, criteria = self.imported(self.document("r2", {"quality": 4}))
        self.assertEqual([c.key for c in criteria], ["quality"])
        self.assertEqual(report.counts["reviews"], 1)
        self.assert_results_compute(event, "prj_r2")

    def test_a_row_missing_a_criterion_is_skipped_with_a_note(self):
        rubric = {"criteria": [{"key": "quality", "name": "Quality"},
                               {"key": "impact", "name": "Impact"}]}
        report, event, _ = self.imported(self.document("r3", {"quality": 4}, rubric=rubric))
        self.assertEqual(report.counts["reviews"], 0)
        self.assertFalse(Review.objects.filter(event=event).exists())
        self.assertIn("Skipped score from jdg_r3 for prj_r3: no value for impact.", report.notes)

    def test_an_event_json_round_trip_keeps_a_custom_rubric(self):
        # The export once had no rubric, so a re-import got the default three criteria,
        # its reviews lacked two of them and the results page answered 500.
        from interop.exports import event_json

        rubric = {"criteria": [{"key": "quality", "name": "Quality", "weight": "3.000",
                                "min_score": 1, "max_score": 7, "description": "Craft"}]}
        _, original, before = self.imported(self.document("r4", {"quality": 6}, rubric=rubric))
        exported = json.loads(event_json(original))
        exported["event"]["id"] = "evt_r4_copy"
        report, copy, after = self.imported(exported)
        self.assertEqual([(c.key, c.name, c.description, c.weight, c.min_score, c.max_score)
                          for c in after],
                         [(c.key, c.name, c.description, c.weight, c.min_score, c.max_score)
                          for c in before])
        self.assertEqual(report.counts["reviews"], 1)
        self.assert_results_compute(copy, "prj_r4")

    def test_an_invalid_declared_rubric_is_refused(self):
        rubric = {"criteria": [{"key": "quality", "name": "Quality", "weight": "0"}]}
        with self.assertRaises(ApiError) as caught:
            self.imported(self.document("r5", {"quality": 4}, rubric=rubric))
        self.assertEqual(caught.exception.code, "invalid_fixture")
        self.assertFalse(Event.objects.filter(source_id="evt_r5").exists())
