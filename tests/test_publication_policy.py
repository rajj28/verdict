"""Regression tests for the publication, correction and feedback contract."""
from __future__ import annotations

import importlib
import json
from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from core.errors import ApiError
from events import services as event_services
from events.models import Event, EventRole, Prize, Role, Track
from interop.certificates import certificate_data
from judging import services as judging_services
from judging.models import Assignment, Criterion, Review, ReviewStatus, Rubric, CriterionScore
from projects.models import Project, ProjectStatus
from results import services as results_services
from results.models import ResultPublication
from teams.models import Team, TeamMember


class PublicationPolicyTests(TestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(
            "organizer@publication.test", "password", display_name="Organizer"
        )
        self.judge = User.objects.create_user(
            "judge@publication.test", "password", display_name="Judge Private"
        )
        self.participant = User.objects.create_user(
            "participant@publication.test", "password", display_name="Team Member"
        )
        self.other = User.objects.create_user("other@publication.test", "password")
        self.admin = User.objects.create_user(
            "admin@publication.test", "password", is_admin=True
        )
        past = timezone.now() - timedelta(days=1)
        self.event = Event.objects.create(
            slug="publication-policy",
            name="Publication Policy",
            submissions_close_at=past - timedelta(days=1),
            judging_open_at=past - timedelta(days=1),
            judging_close_at=past,
            created_by=self.organizer,
        )
        EventRole.objects.create(
            event=self.event, user=self.organizer, role=Role.ORGANIZER, public_id="org_policy"
        )
        self.judge_role = EventRole.objects.create(
            event=self.event, user=self.judge, role=Role.JUDGE, public_id="jdg_policy"
        )
        self.participant_role = EventRole.objects.create(
            event=self.event, user=self.participant, role=Role.PARTICIPANT, public_id="par_policy"
        )
        self.track = Track.objects.create(event=self.event, name="Open Track")
        self.judge_role.tracks.add(self.track)
        self.rubric = Rubric.objects.create(event=self.event, version=1)
        self.criterion = Criterion.objects.create(
            rubric=self.rubric, key="quality", name="Quality", weight=Decimal("1.000"),
            min_score=1, max_score=5, position=0,
        )
        self.team = Team.objects.create(event=self.event, name="Display Team")
        TeamMember.objects.create(
            team=self.team, user=self.participant, event=self.event, is_owner=True
        )
        self.other_team = Team.objects.create(event=self.event, name="Other Team")
        self.project = Project.objects.create(
            event=self.event, team=self.team, track=self.track, public_id="prj_policy",
            title="Original Project", status=ProjectStatus.SUBMITTED, revision=1,
        )
        self.other_project = Project.objects.create(
            event=self.event, team=self.other_team, track=self.track, public_id="prj_other",
            title="Second Project", status=ProjectStatus.SUBMITTED, revision=1,
        )
        self.reviews = []
        for project, value, suffix in (
            (self.project, 5, "one"), (self.other_project, 2, "two")
        ):
            assignment = Assignment.objects.create(
                event=self.event, judge=self.judge_role, project=project
            )
            review = Review.objects.create(
                event=self.event, assignment=assignment, judge=self.judge_role, project=project,
                public_id=f"rev_{suffix}", status=ReviewStatus.SUBMITTED, comment=f"PRIVATE {suffix}",
                submitted_at=past, rubric_version=1, project_revision=1,
            )
            CriterionScore.objects.create(review=review, criterion=self.criterion, value=value)
            self.reviews.append(review)
        Event.objects.filter(pk=self.event.pk).update(scoring_locked_at=past)
        self.event.refresh_from_db()

    def publish(self, note="Initial release"):
        return results_services.publish(self.organizer, self.event, note=note)

    def test_publication_stores_full_project_snapshot_and_verifies_reproducibly(self):
        publication = self.publish()
        snapshots = {
            row["project_id"]: row for row in publication.inputs["project_snapshot"]
        }
        self.assertEqual(
            {key for key in ("title", "team", "track", "status", "status_reason")
             if key in snapshots["prj_policy"]},
            {"title", "team", "track", "status", "status_reason"},
        )
        self.assertEqual(snapshots["prj_policy"]["title"], "Original Project")
        self.assertEqual(snapshots["prj_policy"]["team"], "Display Team")
        self.assertEqual(snapshots["prj_policy"]["track"], "Open Track")
        self.assertEqual(snapshots["prj_policy"]["status"], ProjectStatus.SUBMITTED)
        self.assertIsNone(snapshots["prj_policy"]["status_reason"])
        self.assertIn("criteria", publication.inputs["params"])
        self.assertEqual(publication.project_snapshot_backfilled, False)

        Project.objects.filter(pk=self.project.pk).update(
            title="Changed later", status_reason="updated after release"
        )
        Team.objects.filter(pk=self.team.pk).update(name="Changed team")
        result = results_services.verify_publication(publication)
        self.assertEqual(result["reproducible"]["verdict"], "reproducible")
        self.assertEqual(
            result["unchanged_since_publication"]["verdict"], "changed since publication"
        )
        differences = result["unchanged_since_publication"]["differences"]
        self.assertIn("project prj_policy title changed after publication", differences)
        self.assertIn("project prj_policy team changed after publication", differences)
        self.assertIn("project prj_policy status_reason changed after publication", differences)

    def test_snapshot_migration_backfills_best_effort_and_marks_publication(self):
        publication = ResultPublication.objects.create(
            event=self.event, public_id="pub_legacy", method="normalized",
            rows=[{
                "project_id": self.project.public_id, "title": self.project.title,
                "team": self.team.name, "track": self.track.name, "status": "ranked",
            }],
            inputs={"included": [], "excluded": []},
        )
        migration = importlib.import_module(
            "results.migrations.0004_publication_project_snapshot"
        )
        with connection.schema_editor() as schema_editor:
            migration.backfill_project_snapshots(apps, schema_editor)
        publication.refresh_from_db()
        self.assertTrue(publication.project_snapshot_backfilled)
        snapshot = publication.inputs["project_snapshot"][0]
        self.assertEqual(snapshot["project_id"], self.project.public_id)
        self.assertEqual(snapshot["title"], self.project.title)
        self.assertEqual(snapshot["team"], self.team.name)
        self.assertEqual(len(publication.input_digest), 64)

    def test_verify_api_returns_two_answers_and_decision_page_explains_them(self):
        publication = self.publish()
        Project.objects.filter(pk=self.project.pk).update(title="Corrected title")
        api = APIClient()
        api.force_authenticate(self.organizer)
        response = api.post(
            f"/api/v1/events/{self.event.slug}/results/publications/{publication.public_id}/verify",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["reproducible_verdict"], "reproducible")
        self.assertEqual(response.data["unchanged_verdict"], "changed since publication")
        self.assertIn("project prj_policy title changed after publication", response.data["difference_text"])

        page = Client()
        page.force_login(self.organizer)
        html = page.get(
            f"/manage/{self.event.slug}/results/publications/{publication.public_id}"
        )
        self.assertEqual(html.status_code, 200)
        self.assertContains(html, "Reproducible")
        self.assertContains(html, "Unchanged since publication")
        self.assertContains(html, "changed inputs may be an authorized correction")

    def test_verify_command_reports_both_verdicts(self):
        publication = self.publish()
        output = []
        call_command("verify_publication", publication.public_id, stdout=CaptureOutput(output))
        text = "\n".join(output)
        self.assertIn("Reproducible: reproducible", text)
        self.assertIn("Unchanged since publication: unchanged since publication", text)
        Project.objects.filter(pk=self.project.pk).update(title="Corrected after publish")
        output.clear()
        call_command("verify_publication", publication.public_id, stdout=CaptureOutput(output))
        changed_text = "\n".join(output)
        self.assertIn("Unchanged since publication: changed since publication", changed_text)
        self.assertIn("project prj_policy title changed after publication", changed_text)

    def test_judging_reopen_after_publication_is_conflicted_and_audited(self):
        self.publish()
        with self.assertRaises(ApiError) as error:
            event_services.update_event(
                self.organizer, self.event,
                {"judging_close_at": timezone.now() + timedelta(days=1)},
            )
        self.assertEqual(error.exception.status_code, 409)
        self.assertTrue(AuditEvent.objects.filter(
            event=self.event, action="event.judging_reopen_rejected"
        ).exists())

    def test_judging_can_be_reopened_before_publication_and_ballots_cannot_be_edited_after_close(self):
        no_publication = Event.objects.create(
            slug="prepublication-window",
            name="Prepublication Window",
            submissions_close_at=timezone.now() - timedelta(days=3),
            judging_open_at=timezone.now() - timedelta(days=2),
            judging_close_at=timezone.now() - timedelta(days=1),
            created_by=self.organizer,
        )
        EventRole.objects.create(
            event=no_publication, user=self.organizer, role=Role.ORGANIZER, public_id="org_prepub"
        )
        opened_until = timezone.now() + timedelta(days=1)
        event_services.update_event(self.organizer, no_publication, {"judging_close_at": opened_until})
        no_publication.refresh_from_db()
        self.assertEqual(no_publication.judging_close_at, opened_until)
        window_audit = AuditEvent.objects.get(event=no_publication, action="event.updated")
        self.assertEqual(
            window_audit.data["judging_close_at"]["new"], opened_until.isoformat()
        )

        original_score = self.reviews[0].scores.get().value
        with self.assertRaises(ApiError) as error:
            judging_services.save_review_draft(
                self.judge, self.event, self.project, {"quality": 1}, "too late"
            )
        self.assertEqual(error.exception.code, "judging_closed")
        self.assertEqual(self.reviews[0].scores.get().value, original_score)
        with self.assertRaises(ApiError):
            judging_services.save_review_draft(
                self.organizer, self.event, self.project, {"quality": 1}, "organizer shortcut"
            )
        with self.assertRaises(ApiError):
            judging_services.save_review_draft(
                self.admin, self.event, self.project, {"quality": 1}, "admin shortcut"
            )

    def test_judge_can_submit_a_ballot_before_close(self):
        Event.objects.filter(pk=self.event.pk).update(
            judging_close_at=timezone.now() + timedelta(hours=1)
        )
        saved = judging_services.submit_review(
            self.judge, self.event, self.project, {"quality": 4}, "private comment"
        )
        self.assertEqual(saved.status, ReviewStatus.SUBMITTED)
        self.assertEqual(saved.scores.get(criterion=self.criterion).value, 4)

    def test_public_results_expose_official_version_and_safe_history(self):
        first = self.publish()
        second = self.publish("Corrected spelling")
        response = Client().get(f"/events/{self.event.slug}/results")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Official version {second.version}")
        self.assertContains(response, f"supersedes version {first.version}")
        self.assertContains(response, "Corrected spelling")
        self.assertContains(response, "Publication history")
        public = results_services.public_results(self.event)
        self.assertEqual(public["version"], 2)
        self.assertEqual(
            set(public["history"][0]),
            {"version", "published_at", "note", "input_digest"},
        )
        self.assertNotIn("judge_rows", json.dumps(public))
        self.assertNotIn(self.judge_role.public_id, json.dumps(public))
        self.assertNotIn("PRIVATE one", json.dumps(public))

    def test_winner_certificate_names_version_and_reports_changed_award(self):
        prize = Prize.objects.create(event=self.event, name="Grand Prize")
        first = self.publish()
        winning_id = first.awards[0]["project_id"]
        certificate_id = f"{first.public_id}.{prize.public_id}.1"
        changed_project = Project.objects.get(event=self.event, public_id=winning_id)
        Project.objects.filter(pk=changed_project.pk).update(
            status=ProjectStatus.DISQUALIFIED, status_reason="Correction"
        )
        verification = results_services.verify_publication(first)
        self.assertIn(
            f"project {winning_id} disqualified after publication",
            verification["unchanged_since_publication"]["differences"],
        )
        second = self.publish("Disqualification correction")
        data = certificate_data(self.event, "winner", certificate_id)
        self.assertIsNotNone(data)
        self.assertEqual(data["publication_version"], first.version)
        self.assertEqual(data["superseded_by_version"], second.version)
        page = Client()
        page.force_login(self.organizer)
        response = page.get(
            f"/events/{self.event.slug}/certificates/winner/{certificate_id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Publication version {first.version}")
        self.assertContains(response, f"Superseded by publication version {second.version}")

    def test_released_feedback_contains_scores_only_and_is_team_scoped(self):
        publication = self.publish()
        api = APIClient()
        api.force_authenticate(self.participant)
        path = f"/api/v1/events/{self.event.slug}/projects/{self.project.public_id}/feedback"
        self.assertEqual(api.get(path).status_code, 403)
        publication.feedback_released_at = timezone.now()
        publication.save(update_fields=["feedback_released_at"])
        response = api.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["comments"], [])
        self.assertIn("quality", response.data["per_criterion"])
        payload = json.dumps(response.data)
        self.assertNotIn("PRIVATE one", payload)
        self.assertNotIn(self.judge_role.public_id, payload)
        self.assertNotIn("Judge Private", payload)

        api.force_authenticate(self.other)
        self.assertEqual(api.get(path).status_code, 403)
        api.force_authenticate(self.judge)
        self.assertEqual(api.get(path).status_code, 403)


class CaptureOutput:
    def __init__(self, lines):
        self.lines = lines

    def write(self, message):
        self.lines.append(str(message))
