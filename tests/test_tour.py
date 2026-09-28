"""Private click-and-play tour boundaries and isolation."""
from time import perf_counter
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from core.tour import SESSION_KEY, _rate_hits, prune_expired
from events.models import Event, EventRole, Role
from projects.models import Project
from judging.models import Review, ReviewStatus
from teams.models import TeamMember


@override_settings(DEMO_MODE=True)
class TourTests(TestCase):
    def setUp(self):
        self.client = Client()
        _rate_hits.clear()

    def start(self):
        response = self.client.post("/api/v1/tour/start", content_type="application/json")
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_start_is_private_full_copy_with_sandbox_only_accounts(self):
        other_user = User.objects.create_user("other-event-user@example.org")
        other_event = Event.objects.create(
            slug="other-event", name="Other event",
            submissions_close_at=timezone.now(), created_by=other_user,
        )
        EventRole.objects.create(
            event=other_event, user=other_user, role=Role.ORGANIZER, public_id="org_other"
        )
        payload = self.start()
        event = Event.objects.get(slug=payload["slug"])
        self.assertTrue(event.source_id.startswith("evt_tour_"))
        self.assertEqual(event.roles.filter(role=Role.JUDGE).count(), 12)
        self.assertEqual(event.teams.count(), 24)
        self.assertEqual(event.projects.count(), 24)
        self.assertEqual(event.roles.filter(role=Role.PARTICIPANT).count(), 24)
        self.assertEqual(event.roles.filter(role=Role.ORGANIZER).count(), 1)
        users = self._sandbox_user_ids(event)
        self.assertEqual(len(users), 37)
        other_event_users = set(other_event.roles.values_list("user_id", flat=True))
        self.assertTrue(users.isdisjoint(other_event_users))
        self.assertEqual(User.objects.filter(pk__in=users, email__endswith="@tour.verdict.local").count(), 37)
        self.assertTrue(all(not user.has_usable_password() for user in User.objects.filter(pk__in=users)))
        self.assertEqual(User.objects.filter(email__endswith="@tour.verdict.local").count(), 37)
        self.assertIsNone(event.judging_close_at)
        self.assertFalse(event.result_publications.exists())
        judge = event.roles.get(role=Role.JUDGE, source_id="jdg_sc01")
        self.assertEqual(event.reviews.count(), 72)
        self.assertEqual(
            Review.objects.filter(event=event, judge=judge, status=ReviewStatus.DRAFT).count(), 2
        )
        self.assertEqual(
            Review.objects.filter(event=event, status=ReviewStatus.SUBMITTED).count(), 70
        )
        self.assertEqual(self.client.get("/tour/" + event.slug).status_code, 200)
        self.assertNotContains(self.client.get("/projects"), event.name)

    def test_second_start_reuses_session_and_two_sessions_are_independent(self):
        first = self.start()
        second = self.client.post("/api/v1/tour/start", content_type="application/json").json()
        self.assertEqual(first["slug"], second["slug"])
        other = Client()
        other_payload = other.post("/api/v1/tour/start", content_type="application/json").json()
        self.assertNotEqual(first["slug"], other_payload["slug"])
        self.assertEqual(Event.objects.filter(source_id__startswith="evt_tour_").count(), 2)
        first_users = self._sandbox_user_ids(Event.objects.get(slug=first["slug"]))
        second_users = self._sandbox_user_ids(Event.objects.get(slug=other_payload["slug"]))
        self.assertTrue(first_users.isdisjoint(second_users))

    @override_settings(DEMO_MODE=False)
    def test_disabled_routes_and_home_card_are_absent(self):
        self.assertEqual(Client().get("/tour").status_code, 404)
        self.assertEqual(Client().post("/api/v1/tour/start").status_code, 404)
        self.assertNotContains(Client().get("/"), "Take the 5-minute tour")

    def test_role_switch_is_bound_to_session(self):
        payload = self.start()
        event = Event.objects.get(slug=payload["slug"])
        expected = {
            Role.ORGANIZER: event.roles.get(
                role=Role.ORGANIZER,
                source_id=f"org-{event.source_id.removeprefix('evt_tour_')}",
            ),
            Role.JUDGE: event.roles.get(role=Role.JUDGE, source_id="jdg_sc01"),
            Role.PARTICIPANT: event.roles.get(
                role=Role.PARTICIPANT,
                source_id=(
                    f"m{event.source_id.removeprefix('evt_tour_')[:13]}0101"
                    "@tour.verdict.local"
                ),
            ),
        }
        for role, event_role in expected.items():
            response = self.client.post(
                "/api/v1/tour/role", {"role": role}, content_type="application/json"
            )
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(self.client.session[SESSION_KEY], payload["slug"])
            self.assertEqual(int(self.client.session["_auth_user_id"]), event_role.user_id)
        other = Client()
        self.assertEqual(
            other.post("/api/v1/tour/role", {"role": "judge"}, content_type="application/json").status_code,
            403,
        )

    def test_sandbox_calibration_uses_planted_fixture_ids_and_truth(self):
        payload = self.start()
        event = Event.objects.get(slug=payload["slug"])
        self.client.post("/api/v1/tour/role", {"role": Role.ORGANIZER}, content_type="application/json")
        response = self.client.get(f"/manage/{event.slug}/calibration")
        self.assertEqual(response.status_code, 200)
        report = response.context["calibration"]
        planted = [row for row in report["judges"] if row["habit"] != "neutral"]
        self.assertEqual(len(planted), 4)
        self.assertTrue(all(row["sign_ok"] for row in planted), planted)
        self.assertContains(response, "1. Judge habits")
        self.assertContains(response, "2. Order recovery")
        self.assertContains(response, "3. Top 3")
        self.assertContains(response, "4. Verdict")
        self.assertContains(response, "5. What this does and does not prove")

    def test_tour_happy_path_scores_closes_previews_publishes_and_verifies(self):
        payload = self.start()
        event = Event.objects.get(slug=payload["slug"])
        self.client.post("/api/v1/tour/role", {"role": Role.JUDGE}, content_type="application/json")
        judge = event.roles.get(role=Role.JUDGE, source_id="jdg_sc01")
        drafts = list(Review.objects.filter(event=event, judge=judge, status=ReviewStatus.DRAFT))
        scores = {"functionality": 3, "quality": 4, "innovation": 3}
        for review in drafts:
            base = f"/api/v1/events/{event.slug}/judge/reviews/{review.project.public_id}"
            draft = self.client.put(
                base, {"scores": scores, "comment": "Tour rehearsal score."}, content_type="application/json"
            )
            self.assertEqual(draft.status_code, 200, draft.content)
            submitted = self.client.post(
                f"{base}/submit", {"scores": scores, "comment": "Tour rehearsal score."},
                content_type="application/json",
            )
            self.assertEqual(submitted.status_code, 200, submitted.content)
        self.assertEqual(Review.objects.filter(event=event, status=ReviewStatus.DRAFT).count(), 0)

        self.client.post("/api/v1/tour/role", {"role": Role.ORGANIZER}, content_type="application/json")
        closed = self.client.post(
            f"/api/v1/events/{event.slug}/close-judging", content_type="application/json"
        )
        self.assertEqual(closed.status_code, 200, closed.content)
        project = Project.objects.filter(event=event).order_by("public_id").first()
        consequence = self.client.post(
            f"/api/v1/events/{event.slug}/results/consequences",
            {"action": "disqualify", "target": project.public_id},
            content_type="application/json",
        )
        self.assertEqual(consequence.status_code, 200, consequence.content)
        disqualified = self.client.post(
            f"/api/v1/events/{event.slug}/projects/{project.public_id}/disqualify",
            {"reason": "Tour consequence rehearsal.", "expected_digest": consequence.json()["basis_digest"]},
            content_type="application/json",
        )
        self.assertEqual(disqualified.status_code, 200, disqualified.content)
        publication = self.client.post(
            f"/api/v1/events/{event.slug}/results/publish",
            {"note": "Tour rehearsal publication."}, content_type="application/json",
        )
        self.assertEqual(publication.status_code, 201, publication.content)
        verified = self.client.post(
            f"/api/v1/events/{event.slug}/results/publications/{publication.json()['pub_id']}/verify",
            content_type="application/json",
        )
        self.assertEqual(verified.status_code, 200, verified.content)
        self.assertEqual(verified.json()["verdict"], "identical")

    def test_reset_and_prune_remove_every_sandbox_user_only(self):
        retained_user = User.objects.create_user("retained@outside.test")
        Event.objects.create(
            slug="retained-event", name="Retained event",
            submissions_close_at=timezone.now(), created_by=retained_user,
        )
        first = self.start()
        first_event = Event.objects.get(slug=first["slug"])
        first_users = self._sandbox_user_ids(first_event)
        outside_users = set(User.objects.exclude(pk__in=first_users).values_list("pk", flat=True))
        outside_events = set(Event.objects.exclude(pk=first_event.pk).values_list("pk", flat=True))
        replacement = self.client.post("/api/v1/tour/reset", content_type="application/json")
        self.assertEqual(replacement.status_code, 201)
        self.assertNotEqual(first["slug"], replacement.json()["slug"])
        self.assertFalse(Event.objects.filter(pk=first_event.pk).exists())
        self.assertFalse(User.objects.filter(pk__in=first_users).exists())
        self.assertEqual(set(User.objects.values_list("pk", flat=True)) - self._sandbox_user_ids(
            Event.objects.get(slug=replacement.json()["slug"])
        ), outside_users)
        second_event = Event.objects.get(slug=replacement.json()["slug"])
        second_users = self._sandbox_user_ids(second_event)
        Event.objects.filter(pk=second_event.pk).update(
            created_at=timezone.now() - timedelta(hours=7)
        )
        self.assertEqual(prune_expired(), 1)
        self.assertFalse(Event.objects.filter(pk=second_event.pk).exists())
        self.assertFalse(User.objects.filter(pk__in=second_users).exists())
        self.assertEqual(set(User.objects.values_list("pk", flat=True)), outside_users)
        self.assertEqual(set(Event.objects.values_list("pk", flat=True)), outside_events)

    def test_sandbox_creation_finishes_under_three_seconds(self):
        started = perf_counter()
        self.start()
        elapsed = perf_counter() - started
        print(f"Tour sandbox creation: {elapsed:.3f}s")
        self.assertLess(elapsed, 3.0, f"Sandbox creation took {elapsed:.3f}s.")

    @staticmethod
    def _sandbox_user_ids(event):
        user_ids = set(event.roles.values_list("user_id", flat=True))
        user_ids.update(TeamMember.objects.filter(event=event).values_list("user_id", flat=True))
        if event.created_by_id:
            user_ids.add(event.created_by_id)
        return user_ids
