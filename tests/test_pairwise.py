"""Live pairwise selection, isolated judging, retraction and official publication."""
from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from accounts.models import User
from audit.models import AuditEvent
from core.errors import ApiError
from events.models import CustomQuestion, Event, EventRole, Role, Track
from judging import pairs, services
from judging.models import Assignment, Comparison, Criterion, Rubric
from projects.models import Answer, Project
from results import services as results
from teams.models import Team


class PairSelectionTests(SimpleTestCase):
    def test_selection_is_order_independent_seeded_and_coverage_first(self):
        selected = pairs.select_pair(["a", "b", "c", "d"], [], {"a": 20, "b": 20}, seed="event:judge")
        self.assertEqual(selected["pair"], ("c", "d"))
        self.assertEqual(selected, pairs.select_pair(["d", "c", "b", "a"], [],
                                                     {"a": 20, "b": 20}, seed="event:judge"))

    def test_never_repeats_and_stops_when_all_pairs_exhausted(self):
        seen = []
        while True:
            state = pairs.select_pair(["a", "b", "c"], seen, {}, seed="event:judge")
            if state["pair"] is None:
                break
            self.assertNotIn(state["pair"], seen)
            seen.append(state["pair"])
        self.assertEqual(len(seen), 3)
        self.assertEqual(state["progress"]["reason"], "pairs_exhausted")

    def test_stops_at_per_project_target_before_exhaustion(self):
        state = pairs.select_pair(["a", "b", "c", "d"], [("a", "b"), ("c", "d")], {}, minimum=1)
        self.assertIsNone(state["pair"])
        self.assertEqual(state["progress"]["reason"], "coverage_reached")


class PairwiseTests(TestCase):
    def setUp(self):
        self.stamp = timezone.now()
        self.organizer = User.objects.create_user("org@pair.test", "strong-password")
        self.judge = User.objects.create_user("judge@pair.test", "strong-password")
        self.other = User.objects.create_user("other@pair.test", "strong-password")
        self.event = Event.objects.create(
            slug="pair-event", name="Pair event", created_by=self.organizer,
            submissions_close_at=self.stamp - timedelta(days=2),
            judging_open_at=self.stamp - timedelta(days=1),
            judging_close_at=self.stamp + timedelta(days=1), judging_mode="pairwise",
            ranking_method="pairwise", shrinkage_lambda=2,
        )
        EventRole.objects.create(event=self.event, user=self.organizer, role=Role.ORGANIZER, public_id="org_pair")
        self.role = EventRole.objects.create(event=self.event, user=self.judge, role=Role.JUDGE, public_id="jdg_pair")
        self.other_role = EventRole.objects.create(event=self.event, user=self.other, role=Role.JUDGE, public_id="jdg_other")
        self.track = Track.objects.create(event=self.event, name="Open")
        self.other_track = Track.objects.create(event=self.event, name="Private")
        self.role.tracks.add(self.track)
        self.other_role.tracks.add(self.track)
        self.projects = []
        for i in range(4):
            team = Team.objects.create(event=self.event, name=f"Team {i}")
            project = Project.objects.create(event=self.event, team=team, track=self.track,
                                             title=f"Project {i}", status="submitted")
            self.projects.append(project)
            if i < 3:
                Assignment.objects.create(event=self.event, judge=self.role, project=project)
                Assignment.objects.create(event=self.event, judge=self.other_role, project=project)
        rubric = Rubric.objects.create(event=self.event)
        Criterion.objects.create(rubric=rubric, key="quality", name="Quality", weight=1)
        self.client.force_login(self.judge)
        self.base = f"/api/v1/events/{self.event.slug}/judge"

    def compare(self, left=0, right=1, winner=0, actor=None):
        return services.save_comparison(actor or self.judge, self.event, self.projects[left],
                                        self.projects[right], None if winner is None else self.projects[winner])

    def close(self):
        Event.objects.filter(pk=self.event.pk).update(judging_close_at=self.stamp)
        self.event.refresh_from_db()

    def test_next_api_and_console_include_only_assigned_private_evidence(self):
        question = CustomQuestion.objects.create(event=self.event, prompt="Private evidence", is_public=False)
        for i, project in enumerate(self.projects):
            Answer.objects.create(project=project, question=question, value=f"secret-{i}")
        response = self.client.get(self.base + "/pairs/next")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("progress", data)
        self.assertNotIn(self.projects[3].public_id, str(data))
        self.assertNotIn("secret-3", str(data))
        page = self.client.get(f"/judge/{self.event.slug}/pairwise")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Left is better")
        self.assertNotContains(page, "secret-3")

    def test_unassigned_project_and_stale_other_track_assignment_are_denied(self):
        with self.assertRaises(ApiError) as error:
            self.compare(0, 3)
        self.assertEqual(error.exception.status_code, 403)
        Project.objects.filter(pk=self.projects[1].pk).update(track=self.other_track)
        with self.assertRaises(ApiError) as error:
            self.compare()
        self.assertEqual(error.exception.status_code, 403)
        data = self.client.get(self.base + "/pairs/next").json()
        self.assertNotIn(self.projects[1].public_id, str(data))

    def test_comparison_collection_is_scoped_to_own_judge(self):
        own = self.compare()
        other = self.compare(actor=self.other)
        response = self.client.get(self.base + "/comparisons")
        self.assertEqual(response.status_code, 200)
        self.assertIn(own.public_id, str(response.json()))
        self.assertNotIn(other.public_id, str(response.json()))
        self.assertEqual(self.client.get(self.base + "/comparisons", {"judge": self.other_role.public_id}).status_code, 403)

    def test_duplicate_reversed_pair_is_409_and_database_constraint_prevents_race(self):
        self.compare()
        with self.assertRaises(ApiError) as error:
            self.compare(1, 0, 1)
        self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Comparison.objects.create(event=self.event, judge=self.role,
                                      left=self.projects[0], right=self.projects[1], winner=self.projects[0])

    def test_abstention_is_recorded_and_counted_but_not_a_win(self):
        comparison = self.compare(winner=None)
        self.assertIsNone(comparison.winner_id)
        self.assertEqual(services.pairwise_state(self.judge, self.event)["progress"]["comparisons"], 1)
        preview = results.preview(self.event)
        self.assertEqual(preview["live_pairwise"]["abstentions"], 1)
        self.assertTrue(all(row["status"] == "unranked_no_reviews" for row in preview["rows"]))

    def test_first_comparison_locks_rule_and_is_audited(self):
        comparison = self.compare()
        self.event.refresh_from_db()
        self.assertIsNotNone(self.event.scoring_locked_at)
        self.assertTrue(AuditEvent.objects.filter(action="judging.comparison_created",
                                                  target_id=comparison.public_id).exists())

    def test_close_boundary_denies_next_save_and_undo(self):
        comparison = self.compare()
        self.close()
        with patch("events.policy.now", return_value=self.stamp):
            for operation in (
                lambda: services.next_pair(self.judge, self.event),
                lambda: self.compare(0, 2),
                lambda: services.undo_comparison(self.judge, self.event, comparison.public_id),
            ):
                with self.assertRaises(ApiError) as error:
                    operation()
                self.assertEqual(error.exception.status_code, 403)

    def test_undo_retains_audit_record_and_allows_comparing_again(self):
        comparison = self.compare()
        services.undo_comparison(self.judge, self.event, comparison.public_id)
        comparison.refresh_from_db()
        self.assertIsNotNone(comparison.retracted_at)
        replacement = self.compare(winner=1)
        self.assertNotEqual(replacement.public_id, comparison.public_id)
        self.assertTrue(AuditEvent.objects.filter(action="judging.comparison_retracted",
                                                  target_id=comparison.public_id).exists())

    def test_undo_is_own_latest_only_and_half_open_thirty_seconds(self):
        first = self.compare()
        latest = self.compare(0, 2)
        for actor, target in ((self.other, latest), (self.judge, first)):
            with self.assertRaises(ApiError):
                services.undo_comparison(actor, self.event, target.public_id)
        with patch("judging.services.now", return_value=latest.created_at + timedelta(seconds=30)):
            with self.assertRaises(ApiError) as error:
                services.undo_comparison(self.judge, self.event, latest.public_id)
        self.assertEqual(error.exception.code, "undo_expired")

    def test_live_rank_publication_and_stored_inputs_reverify(self):
        self.compare(0, 1, 0)
        self.compare(0, 2, 0)
        self.compare(1, 2, 1)
        preview = results.preview(self.event)
        by_id = {row["project_id"]: row for row in preview["rows"]}
        self.assertEqual(by_id[self.projects[0].public_id]["rank"], "1")
        self.assertEqual(by_id[self.projects[2].public_id]["rank"], "3")
        self.assertEqual(by_id[self.projects[3].public_id]["status"], "unranked_no_reviews")
        self.close()
        publication = results.publish(self.organizer, self.event, acknowledge_unranked=True)
        self.assertEqual(len(publication.inputs["comparisons"]), 3)
        self.assertEqual(results.verify_publication(publication)["verdict"], "identical")
        Comparison.objects.filter(pk=Comparison.objects.first().pk).update(winner=None)
        self.assertFalse(results.verify_publication(publication)["digest_match"])

    def test_disconnected_live_groups_cannot_publish_overall_winner(self):
        Assignment.objects.create(event=self.event, judge=self.role, project=self.projects[3])
        self.compare(0, 1, 0)
        self.compare(2, 3, 2)
        preview = results.preview(self.event)
        self.assertEqual(preview["live_pairwise"]["n_components"], 2)
        self.assertFalse(preview["live_pairwise"]["comparable"])
        self.close()
        with self.assertRaises(ApiError) as error:
            results.publish(self.organizer, self.event, acknowledge_unranked=True)
        self.assertEqual(error.exception.code, "pairwise_disconnected")

    def test_organizer_only_csv_export_includes_abstentions_and_retractions(self):
        comparison = self.compare(winner=None)
        services.undo_comparison(self.judge, self.event, comparison.public_id)
        url = f"/api/v1/events/{self.event.slug}/exports/pairwise.csv"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.organizer)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, comparison.public_id)
        self.assertContains(response, "retracted_at")

    def test_draft_titles_never_enter_result_rows_or_publication(self):
        self.compare()
        draft_team = Team.objects.create(event=self.event, name="Private team")
        draft = Project.objects.create(event=self.event, team=draft_team, title="Hidden draft secret")
        preview = results.preview(self.event)
        self.assertNotIn(draft.public_id, str(preview["rows"]))
        self.close()
        publication = results.publish(self.organizer, self.event, acknowledge_unranked=True)
        self.assertNotIn(draft.title, str(publication.rows))
        self.assertNotIn(draft.title, str(results.public_results(self.event)))
