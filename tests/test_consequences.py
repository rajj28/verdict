from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from core.errors import ApiError
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
from judging import services as judging_services
from projects.models import Project, ProjectStatus
from results import consequences, services as results_services
from results.models import ResultPublication
from teams.models import Team


class ConsequencesTests(TestCase):
    def setUp(self):
        self.organizer = User.objects.create_user("organizer@consequences.test", "password")
        self.judge1 = User.objects.create_user("judge1@consequences.test", "password")
        self.judge2 = User.objects.create_user("judge2@consequences.test", "password")
        self.participant = User.objects.create_user("participant@consequences.test", "password")
        self.foreign_organizer = User.objects.create_user("foreign@consequences.test", "password")
        past = timezone.now() - timedelta(days=5)
        self.event = Event.objects.create(
            slug="consequences-test",
            name="Consequences Test",
            submissions_close_at=past,
            judging_open_at=past,
            judging_close_at=past + timedelta(days=1),
            ranking_method="raw",
            shrinkage_lambda=Decimal("2"),
            created_by=self.organizer,
        )
        EventRole.objects.create(
            event=self.event, user=self.organizer, role=Role.ORGANIZER, public_id="org_cons"
        )
        self.foreign_event = Event.objects.create(
            slug="consequences-foreign",
            name="Foreign Event",
            submissions_close_at=past,
            judging_open_at=past,
            judging_close_at=past + timedelta(days=1),
            created_by=self.foreign_organizer,
        )
        EventRole.objects.create(
            event=self.foreign_event, user=self.foreign_organizer,
            role=Role.ORGANIZER, public_id="org_foreign",
        )
        self.track = Track.objects.create(event=self.event, name="Open", position=1)
        self.judge_roles = [
            EventRole.objects.create(
                event=self.event, user=user, role=Role.JUDGE,
                public_id=f"jdg_cons{index}",
            )
            for index, user in enumerate((self.judge1, self.judge2), start=1)
        ]
        for role in self.judge_roles:
            role.tracks.add(self.track)
        EventRole.objects.create(
            event=self.event, user=self.participant, role=Role.PARTICIPANT,
            public_id="par_cons",
        )
        teams = [Team.objects.create(event=self.event, name=f"Team {i}") for i in (1, 2)]
        self.leader = Project.objects.create(
            event=self.event, team=teams[0], track=self.track, public_id="prj_cons1",
            title="Paper Trails", status=ProjectStatus.SUBMITTED, revision=1,
        )
        self.runner_up = Project.objects.create(
            event=self.event, team=teams[1], track=self.track, public_id="prj_cons2",
            title="Tide Table", status=ProjectStatus.SUBMITTED, revision=1,
        )
        rubric = Rubric.objects.create(event=self.event, version=1)
        self.criteria = [
            JudgingCriterion.objects.create(
                rubric=rubric, key=key, name=key.title(), weight=Decimal("1.000"),
                min_score=1, max_score=5, position=index,
            )
            for index, key in enumerate(("quality", "innovation"))
        ]
        self.reviews = {}
        for index, role in enumerate(self.judge_roles, start=1):
            for project, values in (
                (self.leader, (5, 5)),
                (self.runner_up, (4, 4)),
            ):
                assignment = Assignment.objects.create(
                    event=self.event, judge=role, project=project,
                )
                review = Review.objects.create(
                    event=self.event, assignment=assignment, judge=role, project=project,
                    public_id=f"rev_cons{index}{project.public_id[-1]}",
                    status=ReviewStatus.SUBMITTED, submitted_at=past,
                    rubric_version=1, project_revision=1,
                )
                for criterion, value in zip(self.criteria, values):
                    CriterionScore.objects.create(review=review, criterion=criterion, value=value)
                self.reviews[(index, project.public_id)] = review
        self.prize = Prize.objects.create(
            event=self.event, public_id="prz_cons1", name="Best overall", value="Trophy",
            position=1,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.organizer)
        self.consequences_url = f"/api/v1/events/{self.event.slug}/results/consequences"

    def _set_score(self, review: Review, value: int):
        CriterionScore.objects.filter(review=review).update(value=value)

    def _write(self, method: str, url: str, data: dict):
        return getattr(self.client, method)(url, data, format="json")

    def test_what_if_without_change_matches_preview(self):
        current = results_services.preview(self.event)
        hypothetical = consequences.what_if(self.event)
        self.assertEqual(current["rows"], hypothetical["rows"])
        self.assertEqual(current["awards"], hypothetical["awards"])
        self.assertEqual(current["input_digest"], hypothetical["input_digest"])

    def test_cached_preview_matches_fresh_computation_field_for_field(self):
        cached = results_services.preview(self.event)
        included, excluded = results_services._build_input_lists(self.event)
        eligible = list(
            Project.objects.filter(event=self.event, status=ProjectStatus.SUBMITTED)
            .values_list("public_id", flat=True)
        )
        fresh = results_services._preview_from_uncached(
            self.event,
            included,
            excluded,
            eligible,
            project_status_overrides=None,
            include_robustness=True,
            comparisons=results_services._comparison_inputs(self.event),
            criteria=results_services.engine_criteria(self.event),
            lam_val=results_services._lam_for_event(self.event),
            rubric_version=results_services._rubric_version(self.event),
            prize_specs=results_services._prize_specs(self.event),
        )
        self.assertEqual(cached, fresh)
        cached["rows"][0]["title"] = "mutated by caller"
        self.assertNotEqual(
            results_services.preview(self.event)["rows"][0]["title"],
            "mutated by caller",
        )

    def test_hypothetical_consequence_skips_robustness_but_keeps_uncertainty(self):
        with mock.patch.object(
            results_services.engine,
            "robustness",
            wraps=results_services.engine.robustness,
        ) as robustness:
            hypothetical = consequences.what_if(
                self.event, disqualify=(self.leader.public_id,)
            )
        robustness.assert_not_called()
        self.assertIn("uncertainty", hypothetical)
        self.assertIn("available", hypothetical["uncertainty"])

    def test_preview_cache_misses_when_reviews_prizes_or_project_status_change(self):
        with results_services._PREVIEW_CACHE_LOCK:
            results_services._PREVIEW_CACHE.clear()
        with mock.patch.object(
            results_services,
            "_preview_from_uncached",
            wraps=results_services._preview_from_uncached,
        ) as compute:
            first = results_services.preview(self.event)
            results_services.preview(self.event)
            self.assertEqual(compute.call_count, 1)

            self._set_score(self.reviews[(1, self.leader.public_id)], 3)
            after_review = results_services.preview(self.event)
            self.assertNotEqual(first["input_digest"], after_review["input_digest"])
            self.assertEqual(compute.call_count, 2)

            Prize.objects.filter(pk=self.prize.pk).update(name="New prize name")
            after_prize = results_services.preview(self.event)
            self.assertNotEqual(after_review["input_digest"], after_prize["input_digest"])
            self.assertEqual(compute.call_count, 3)

            Project.objects.filter(pk=self.leader.pk).update(status=ProjectStatus.WITHDRAWN)
            after_status = results_services.preview(self.event)
            self.assertNotEqual(after_prize["input_digest"], after_status["input_digest"])
            self.assertEqual(compute.call_count, 4)
            leader_row = next(
                row for row in after_status["rows"]
                if row["project_id"] == self.leader.public_id
            )
            self.assertEqual(leader_row["status"], "withdrawn")

    def test_disqualifying_leader_is_exact_and_read_only(self):
        before = results_services.preview(self.event)
        prior = {
            "statuses": list(Project.objects.values_list("public_id", "status")),
            "exclusions": ReviewExclusion.objects.count(),
            "audits": AuditEvent.objects.count(),
            "publications": ResultPublication.objects.count(),
        }
        preview = consequences.consequences(self.event, "disqualify", self.leader.public_id)
        self.assertEqual(preview["winner_after"]["title"], "Tide Table")
        self.assertEqual(preview["n_award_changes"], 1)
        self.assertEqual(preview["award_changes"][0]["before"][0]["title"], "Paper Trails")
        self.assertEqual(preview["award_changes"][0]["after"][0]["title"], "Tide Table")
        self.assertIn("Paper Trails", preview["sentence"])
        self.assertIn("Tide Table", preview["sentence"])
        after = results_services.preview(self.event)
        self.assertEqual(before["rows"], after["rows"])
        self.assertEqual(prior["statuses"], list(Project.objects.values_list("public_id", "status")))
        self.assertEqual(prior["exclusions"], ReviewExclusion.objects.count())
        self.assertEqual(prior["audits"], AuditEvent.objects.count())
        self.assertEqual(prior["publications"], ResultPublication.objects.count())

    def test_excluding_and_reincluding_a_deciding_review_changes_then_restores_ranks(self):
        deciding = self.reviews[(2, self.leader.public_id)]
        self._set_score(deciding, 1)
        before = results_services.preview(self.event)
        self.assertEqual(before["rows"][0]["project_id"], self.runner_up.public_id)
        excluded = consequences.what_if(
            self.event, exclude_reviews=(deciding.public_id,)
        )
        self.assertEqual(excluded["rows"][0]["project_id"], self.leader.public_id)
        ReviewExclusion.objects.create(review=deciding, reason="Test", created_by=self.organizer)
        restored = consequences.what_if(
            self.event, include_reviews=(deciding.public_id,)
        )
        self.assertEqual(restored["rows"], before["rows"])
        self.assertEqual(restored["awards"], before["awards"])

    def test_publish_consequences_report_first_publish_then_official_changes(self):
        first = consequences.consequences(self.event, "publish")
        self.assertIn("First publication:", first["sentence"])
        self.assertEqual(first["n_rank_changes"], 0)
        self.assertEqual(first["n_award_changes"], 0)

        deciding = self.reviews[(2, self.leader.public_id)]
        self._set_score(deciding, 1)
        publication = results_services.publish(self.organizer, self.event)
        unchanged = consequences.consequences(self.event, "publish")
        self.assertEqual(unchanged["n_rank_changes"], 0)
        self.assertEqual(unchanged["n_award_changes"], 0)
        judging_services.exclude_review(self.organizer, deciding, "Decision check")
        changed = consequences.consequences(self.event, "publish")
        self.assertGreater(changed["n_rank_changes"], 0)
        self.assertGreater(changed["n_award_changes"], 0)
        self.assertEqual(publication.public_id, ResultPublication.objects.get().public_id)

    def test_consequences_permissions_and_query_bound(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.post(self.consequences_url, {
            "action": "publish",
        }, format="json").status_code, 401)
        for user in (self.judge1, self.participant, self.foreign_organizer):
            client = APIClient()
            client.force_authenticate(user)
            response = client.post(self.consequences_url, {"action": "publish"}, format="json")
            self.assertIn(response.status_code, (403, 404))
        with CaptureQueriesContext(connection) as queries:
            response = self.client.post(
                self.consequences_url, {"action": "publish"}, format="json"
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertLessEqual(len(queries), 45)
        self.assertIn("basis_digest", response.data)

    def test_results_page_renders_consequence_controls(self):
        self.client.force_login(self.organizer)
        response = self.client.get(f"/manage/{self.event.slug}/results")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-consequence-action=\"disqualify\"")
        self.assertContains(response, "data-publish-form")
        self.assertContains(response, "consequence-dialog")

    def test_sentence_names_real_prizes_only(self):
        """Without prizes the sentence says 'first place', never an invented award."""
        from results.consequences import _sentence
        winner_a = {"project": "prj_a", "title": "Alpha"}
        winner_b = {"project": "prj_b", "title": "Beta"}
        ranks = [{"project": "prj_a", "title": "Alpha", "before": "1", "after": "2"}]
        no_prizes = _sentence("disqualify", "prj_a", ranks, [], winner_a, winner_b)
        self.assertIn("0 awards: first place moves from Alpha to Beta", no_prizes)
        self.assertNotIn("Best overall", no_prizes)
        awards = [{"prize": "prz_1", "prize_name": "Most useful",
                   "before": [{"project": "prj_a", "title": "Alpha"}],
                   "after": [{"project": "prj_b", "title": "Beta"}]}]
        with_prize = _sentence("disqualify", "prj_a", ranks, awards, winner_a, winner_b)
        self.assertIn("1 award: Most useful moves from Alpha to Beta", with_prize)

    def test_consequence_dialog_is_not_inside_any_tab_pane(self):
        """A modal inside a hidden tab pane can never be shown.

        The dialog opens from the Ranking and Data issues tabs (disqualify,
        exclude, put back) as well as from Publish, so it must live outside
        every tab pane.
        """
        from html.parser import HTMLParser

        class Finder(HTMLParser):
            VOID = {"br", "img", "input", "meta", "link", "hr", "source", "col", "wbr"}

            def __init__(self):
                super().__init__()
                self.stack, self.ancestors = [], None

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if attrs.get("id") == "consequence-dialog":
                    self.ancestors = [a.get("class") or "" for a in self.stack]
                if tag not in self.VOID:
                    self.stack.append(attrs)

            def handle_endtag(self, tag):
                if tag not in self.VOID and self.stack:
                    self.stack.pop()

        self.client.force_login(self.organizer)
        page = self.client.get(f"/manage/{self.event.slug}/results").content.decode()
        finder = Finder()
        finder.feed(page)
        self.assertIsNotNone(finder.ancestors, "consequence dialog not rendered")
        self.assertFalse(
            [cls for cls in finder.ancestors if "tab-pane" in cls.split()],
            "the consequence dialog sits inside a tab pane",
        )

    def test_consequences_reject_unknown_and_inapplicable_targets(self):
        with self.assertRaises(ApiError) as missing:
            consequences.consequences(self.event, "disqualify", "prj_missing")
        self.assertEqual(missing.exception.status_code, 404)
        with self.assertRaises(ApiError) as foreign:
            consequences.consequences(
                self.event, "disqualify",
                Project.objects.create(
                    event=self.foreign_event, team=Team.objects.create(
                        event=self.foreign_event, name="Foreign team"
                    ),
                    public_id="prj_foreign", title="Foreign",
                    status=ProjectStatus.SUBMITTED,
                ).public_id,
            )
        self.assertEqual(foreign.exception.status_code, 404)
        with self.assertRaises(ApiError) as not_excluded:
            consequences.consequences(
                self.event, "include_review",
                self.reviews[(1, self.leader.public_id)].public_id,
            )
        self.assertEqual(not_excluded.exception.status_code, 409)
        Project.objects.filter(pk=self.leader.pk).update(status=ProjectStatus.DISQUALIFIED)
        with self.assertRaises(ApiError) as inapplicable:
            consequences.consequences(self.event, "disqualify", self.leader.public_id)
        self.assertEqual(inapplicable.exception.status_code, 409)

    def test_publish_stale_digest_blocks_write_then_fresh_digest_succeeds(self):
        stale = consequences.consequences(self.event, "publish")["basis_digest"]
        self._set_score(self.reviews[(1, self.leader.public_id)], 3)
        url = f"/api/v1/events/{self.event.slug}/results/publish"
        response = self._write("post", url, {"expected_digest": stale})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "stale_preview")
        self.assertEqual(ResultPublication.objects.count(), 0)
        fresh = results_services.preview(self.event)["input_digest"]
        response = self._write("post", url, {"expected_digest": fresh})
        self.assertEqual(response.status_code, 201, response.content)

    def test_disqualify_stale_digest_blocks_write_then_fresh_digest_succeeds(self):
        old = consequences.consequences(self.event, "disqualify", self.leader.public_id)
        self._set_score(self.reviews[(1, self.leader.public_id)], 2)
        url = (
            f"/api/v1/events/{self.event.slug}/projects/{self.leader.public_id}/disqualify"
        )
        response = self._write("post", url, {
            "reason": "Policy violation", "expected_digest": old["basis_digest"],
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "stale_preview")
        self.leader.refresh_from_db()
        self.assertEqual(self.leader.status, ProjectStatus.SUBMITTED)
        fresh = results_services.preview(self.event)["input_digest"]
        response = self._write("post", url, {
            "reason": "Policy violation", "expected_digest": fresh,
        })
        self.assertEqual(response.status_code, 200, response.content)

    def test_exclude_stale_digest_blocks_write_then_fresh_digest_succeeds(self):
        review = self.reviews[(1, self.leader.public_id)]
        old = consequences.consequences(self.event, "exclude_review", review.public_id)
        self._set_score(self.reviews[(2, self.leader.public_id)], 2)
        url = f"/api/v1/events/{self.event.slug}/reviews/{review.public_id}/exclusion"
        response = self._write("post", url, {
            "reason": "Outlier", "expected_digest": old["basis_digest"],
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "stale_preview")
        self.assertFalse(ReviewExclusion.objects.filter(review=review).exists())
        fresh = results_services.preview(self.event)["input_digest"]
        response = self._write("post", url, {"reason": "Outlier", "expected_digest": fresh})
        self.assertEqual(response.status_code, 201, response.content)

    def test_include_stale_digest_blocks_write_then_fresh_digest_succeeds(self):
        review = self.reviews[(1, self.leader.public_id)]
        judging_services.exclude_review(self.organizer, review, "Outlier")
        old = consequences.consequences(self.event, "include_review", review.public_id)
        self._set_score(self.reviews[(2, self.leader.public_id)], 2)
        url = f"/api/v1/events/{self.event.slug}/reviews/{review.public_id}/exclusion"
        response = self._write("delete", url, {"expected_digest": old["basis_digest"]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "stale_preview")
        self.assertTrue(ReviewExclusion.objects.filter(review=review).exists())
        fresh = results_services.preview(self.event)["input_digest"]
        response = self._write("delete", url, {"expected_digest": fresh})
        self.assertEqual(response.status_code, 204)

    def test_writes_without_expected_digest_keep_existing_behavior(self):
        url = (
            f"/api/v1/events/{self.event.slug}/projects/{self.leader.public_id}/disqualify"
        )
        response = self._write("post", url, {"reason": "Policy violation"})
        self.assertEqual(response.status_code, 200, response.content)
