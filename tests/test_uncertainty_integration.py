import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from events.models import Event, EventRole, Prize, Role, Track
from judging.models import (
    Assignment,
    Criterion as JudgingCriterion,
    CriterionScore,
    Review,
    ReviewExclusion,
    ReviewStatus,
    Rubric,
)
from projects.models import Project, ProjectStatus
from results import consequences, services
from results.models import ResultPublication
from teams.models import Team


class RankUncertaintyIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organizer = User.objects.create_user("organizer@uncertainty.test", "password")
        cls.judges = [
            User.objects.create_user(f"judge{index}@uncertainty.test", "password")
            for index in (1, 2)
        ]
        past = timezone.now() - timedelta(days=5)
        cls.event = Event.objects.create(
            slug="uncertainty-test",
            name="Uncertainty Test",
            submissions_close_at=past,
            judging_open_at=past,
            judging_close_at=past + timedelta(days=1),
            ranking_method="normalized",
            shrinkage_lambda=Decimal("2"),
            created_by=cls.organizer,
        )
        EventRole.objects.create(
            event=cls.event, user=cls.organizer, role=Role.ORGANIZER, public_id="org_unc"
        )
        cls.track = Track.objects.create(event=cls.event, name="Open", position=1)
        cls.judge_roles = []
        for index, user in enumerate(cls.judges, start=1):
            role = EventRole.objects.create(
                event=cls.event, user=user, role=Role.JUDGE, public_id=f"jdg_unc{index}"
            )
            role.tracks.add(cls.track)
            cls.judge_roles.append(role)
        cls.projects = []
        for index in (1, 2, 3):
            team = Team.objects.create(event=cls.event, name=f"Team {index}")
            cls.projects.append(Project.objects.create(
                event=cls.event,
                team=team,
                track=cls.track,
                public_id=f"prj_unc{index}",
                title=f"Project {index}",
                status=ProjectStatus.SUBMITTED,
                revision=1,
            ))
        rubric = Rubric.objects.create(event=cls.event, version=1)
        cls.criteria = [
            JudgingCriterion.objects.create(
                rubric=rubric,
                key=key,
                name=key.title(),
                weight=Decimal("1.000"),
                min_score=1,
                max_score=5,
                position=index,
            )
            for index, key in enumerate(("quality", "innovation"))
        ]
        cls.reviews = []
        project_values = {
            1: {1: 5, 2: 3},
            2: {1: 3, 2: 4},
        }
        for project_index, per_judge in project_values.items():
            project = cls.projects[project_index - 1]
            for judge_index, role in enumerate(cls.judge_roles, start=1):
                assignment = Assignment.objects.create(
                    event=cls.event, judge=role, project=project
                )
                review = Review.objects.create(
                    event=cls.event,
                    assignment=assignment,
                    judge=role,
                    project=project,
                    public_id=f"rev_unc{project_index}{judge_index}",
                    status=ReviewStatus.SUBMITTED,
                    submitted_at=past,
                    rubric_version=1,
                    project_revision=1,
                )
                for criterion in cls.criteria:
                    CriterionScore.objects.create(
                        review=review,
                        criterion=criterion,
                        value=per_judge[judge_index],
                    )
                cls.reviews.append(review)
        Prize.objects.create(
            event=cls.event,
            public_id="prz_unc",
            name="Overall",
            value="Award",
            places=2,
            position=1,
        )
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.organizer)

    def _set_all_scores(self, project_index: int, value: int):
        CriterionScore.objects.filter(
            review__project=self.projects[project_index - 1]
        ).update(value=value)

    def test_preview_uncertainty_fields_and_method_availability(self):
        started = perf_counter()
        preview = services.preview(self.event)
        self.assertLess(perf_counter() - started, 3.0)
        uncertainty = preview["uncertainty"]
        self.assertTrue(uncertainty["available"])
        self.assertEqual(uncertainty["top_k"], 2)
        ranked = [row for row in preview["rows"] if row["status"] == "ranked"]
        self.assertEqual(len(ranked), sum(
            len(group) for group in uncertainty["groups"]
        ))
        unranked = next(row for row in preview["rows"] if row["status"] != "ranked")
        for row in ranked:
            self.assertLessEqual(row["rank_low"], row["rank_high"])
            for key in ("p_first", "p_top", "p_above_next"):
                if row[key] is not None:
                    self.assertGreaterEqual(row[key], 0)
                    self.assertLessEqual(row[key], 1)
        for key in (
            "rank_low", "rank_high", "score_low", "score_high",
            "p_first", "p_top", "p_above_next",
        ):
            self.assertIsNone(unranked[key])

        for method in ("raw", "pairwise"):
            self.event.ranking_method = method
            self.event.save(update_fields=["ranking_method"])
            unavailable = services.preview(self.event)["uncertainty"]
            self.assertFalse(unavailable["available"])
            self.assertEqual(
                unavailable["reason"],
                "Rank intervals are computed for the normalized ranking only.",
            )

    def test_publication_inputs_and_verify_do_not_store_uncertainty(self):
        publication = services.publish(
            self.organizer, self.event, acknowledge_unranked=True
        )
        self.assertNotIn("uncertainty", publication.inputs)
        self.assertTrue(all("uncertainty" not in row for row in publication.rows))
        self.assertEqual(
            services.verify_publication(publication)["verdict"], "identical"
        )

    def test_public_uncertainty_uses_cached_published_inputs(self):
        publication = services.publish(
            self.organizer, self.event, acknowledge_unranked=True
        )
        before = services.public_results(self.event)["uncertainty"]
        ReviewExclusion.objects.create(
            review=self.reviews[0], reason="Test-only change", created_by=self.organizer
        )
        live_preview = services.preview(self.event)
        self.assertNotEqual(live_preview["uncertainty"], before)
        after = services.public_results(self.event)["uncertainty"]
        self.assertEqual(after, before)

        api = APIClient()
        response = api.get(f"/api/v1/events/{self.event.slug}/results")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["uncertainty"], before)
        self.assertNotIn("sigma", response.data["uncertainty"])
        self.assertNotIn("seed", response.data["uncertainty"])
        page = api.get(f"/events/{self.event.slug}/results")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Ranks are estimates from a few reviews each.")
        self.assertEqual(ResultPublication.objects.get(pk=publication.pk).input_digest,
                         publication.input_digest)

    def test_publish_consequences_warn_only_when_top_pair_is_tied(self):
        self._set_all_scores(1, 4)
        self._set_all_scores(2, 4)
        tied = consequences.consequences(self.event, "publish")
        self.assertTrue(tied["top_tied"])
        self.assertIn("order held in", tied["top_tie_warning"])
        self.assertIn("shared award", tied["top_tie_warning"])
        self.assertTrue(tied["certainty"])

        self._set_all_scores(1, 5)
        self._set_all_scores(2, 1)
        clear = consequences.consequences(self.event, "publish")
        self.assertFalse(clear["top_tied"])
        self.assertEqual(clear["top_tie_warning"], "")

    def test_pages_show_uncertainty_column_and_public_tie_labels(self):
        self._set_all_scores(1, 4)
        self._set_all_scores(2, 4)
        self.client.force_login(self.organizer)
        organizer_page = self.client.get(f"/manage/{self.event.slug}/results")
        self.assertEqual(organizer_page.status_code, 200)
        self.assertContains(organizer_page, "Certainty")
        self.assertContains(organizer_page, "Assumptions and limits")
        self.assertContains(organizer_page, "docs/UNCERTAINTY.md")
        self.assertContains(organizer_page, "Statistically tied with the next project")

        services.publish(self.organizer, self.event, acknowledge_unranked=True)
        public_page = APIClient().get(f"/events/{self.event.slug}/results")
        self.assertEqual(public_page.status_code, 200)
        self.assertContains(public_page, "Statistically tied")
        self.assertContains(
            public_page,
            "Projects marked as tied could swap places if different judges had reviewed them.",
        )

    def test_certainty_cell_shows_top_k_share_as_a_percentage(self):
        """p_top is a 0-1 share; the page must print it as a percentage."""
        self._set_all_scores(1, 5)
        self._set_all_scores(2, 1)
        preview = services.preview(self.event)
        top_k = preview["uncertainty"]["top_k"]
        rows = [row for row in preview["rows"] if row.get("p_top") is not None]
        self.assertTrue(rows)
        self.client.force_login(self.organizer)
        page = self.client.get(f"/manage/{self.event.slug}/results")
        for row in rows:
            percent = round(row["p_top"] * 100)
            self.assertContains(page, f"top {top_k} in {percent}%")
        best = max(rows, key=lambda row: row["p_top"])
        self.assertGreater(round(best["p_top"] * 100), 1)

    def test_preview_endpoint_query_bound_and_schema(self):
        # Keep the endpoint within 25 queries; uncertainty reuses scored fit data.
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(f"/api/v1/events/{self.event.slug}/results/preview")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertLessEqual(len(queries), 25)
        self.assertIn("uncertainty", response.data)
        self.assertIn("rank_low", response.data["rows"][0])

    def test_fixture_uncertainty_refits_complete_in_under_three_seconds(self):
        fixture_path = Path(__file__).resolve().parent.parent / "fixtures.json"
        with fixture_path.open(encoding="utf-8") as fixture_file:
            fixture = json.load(fixture_file)
        past = timezone.now() - timedelta(days=5)
        fixture_event = Event.objects.create(
            slug="fixture-uncertainty-performance",
            name="Fixture uncertainty performance",
            submissions_close_at=past,
            judging_open_at=past,
            judging_close_at=past + timedelta(days=1),
            ranking_method="normalized",
            shrinkage_lambda=Decimal("100"),
            created_by=self.organizer,
        )
        track = Track.objects.create(event=fixture_event, name="Fixture", position=1)
        project_ids = sorted({row["project"] for row in fixture["scores"]})
        teams = Team.objects.bulk_create([
            Team(event=fixture_event, name=project_id) for project_id in project_ids
        ])
        team_by_project = dict(zip(project_ids, teams))
        projects = Project.objects.bulk_create([
            Project(
                event=fixture_event,
                team=team_by_project[project_id],
                track=track,
                public_id=project_id,
                title=project_id,
                status=(
                    ProjectStatus.SUPERSEDED
                    if project_id == "prj_07" else ProjectStatus.SUBMITTED
                ),
                revision=1,
            )
            for project_id in project_ids
        ])
        project_by_id = {project.public_id: project for project in projects}
        judge_ids = sorted({row["judge"] for row in fixture["scores"]})
        judges = [
            User.objects.create_user(
                f"fixture-{index}@uncertainty.test", "password"
            )
            for index, _judge_id in enumerate(judge_ids)
        ]
        roles = EventRole.objects.bulk_create([
            EventRole(
                event=fixture_event,
                user=user,
                role=Role.JUDGE,
                public_id=judge_id,
            )
            for judge_id, user in zip(judge_ids, judges)
        ])
        role_by_id = {role.public_id: role for role in roles}
        rubric = Rubric.objects.create(event=fixture_event, version=1)
        criteria = {
            criterion.key: criterion
            for criterion in (
                JudgingCriterion.objects.create(
                    rubric=rubric,
                    key=key,
                    name=key.title(),
                    weight=Decimal("1.000"),
                    min_score=1,
                    max_score=5,
                    position=index,
                )
                for index, key in enumerate(("functionality", "quality", "innovation"))
            )
        }
        assignments = [
            Assignment(
                event=fixture_event,
                judge=role_by_id[row["judge"]],
                project=project_by_id[row["project"]],
            )
            for row in fixture["scores"]
        ]
        Assignment.objects.bulk_create(assignments)
        submitted_at = past
        reviews = Review.objects.bulk_create([
            Review(
                event=fixture_event,
                assignment=assignment,
                judge=assignment.judge,
                project=assignment.project,
                public_id=f"rev_fixture_{index:03d}",
                status=ReviewStatus.SUBMITTED,
                submitted_at=submitted_at,
                rubric_version=1,
                project_revision=1,
            )
            for index, assignment in enumerate(assignments)
        ])
        CriterionScore.objects.bulk_create([
            CriterionScore(
                review=review,
                criterion=criteria[key],
                value=value,
            )
            for review, row in zip(reviews, fixture["scores"])
            for key, value in row["criteria"].items()
        ])
        started = perf_counter()
        preview = services.preview(fixture_event)
        self.assertLess(perf_counter() - started, 3.0)
        self.assertTrue(preview["uncertainty"]["available"])
        self.assertEqual(
            sum(row["status"] == "ranked" for row in preview["rows"]), 40
        )
