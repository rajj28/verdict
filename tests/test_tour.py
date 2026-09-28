"""Private click-and-play tour boundaries and isolation."""
from django.test import Client, TestCase, override_settings

from accounts.models import User
from core.tour import SESSION_KEY, _rate_hits
from events.models import Event, EventRole, Role
from judging.models import Review, ReviewStatus


@override_settings(DEMO_MODE=True)
class TourTests(TestCase):
    def setUp(self):
        self.client = Client()
        _rate_hits.clear()

    def start(self):
        response = self.client.post("/api/v1/tour/start", content_type="application/json")
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_start_is_private_and_has_three_tour_role_accounts(self):
        payload = self.start()
        event = Event.objects.get(slug=payload["slug"])
        self.assertTrue(event.source_id.startswith("evt_tour_"))
        self.assertEqual(event.roles.filter(user__email__endswith="@tour.verdict.local").count(), 3)
        self.assertEqual(
            User.objects.filter(email__endswith="@tour.verdict.local").count(), 3
        )
        self.assertIsNone(event.judging_close_at)
        self.assertFalse(event.result_publications.exists())
        judge = event.roles.get(role=Role.JUDGE)
        self.assertEqual(
            Review.objects.filter(event=event, judge=judge, status=ReviewStatus.DRAFT).count(), 2
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

    @override_settings(DEMO_MODE=False)
    def test_disabled_routes_and_home_card_are_absent(self):
        self.assertEqual(Client().get("/tour").status_code, 404)
        self.assertEqual(Client().post("/api/v1/tour/start").status_code, 404)
        self.assertNotContains(Client().get("/"), "Take the 5-minute tour")

    def test_role_switch_is_bound_to_session(self):
        payload = self.start()
        response = self.client.post(
            "/api/v1/tour/role", {"role": "judge"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session[SESSION_KEY], payload["slug"])
        other = Client()
        self.assertEqual(
            other.post("/api/v1/tour/role", {"role": "judge"}, content_type="application/json").status_code,
            403,
        )

    def test_reset_replaces_sandbox_and_prune_deletes_it(self):
        first = self.start()
        replacement = self.client.post("/api/v1/tour/reset", content_type="application/json")
        self.assertEqual(replacement.status_code, 201)
        self.assertNotEqual(first["slug"], replacement.json()["slug"])
        self.assertFalse(Event.objects.filter(slug=first["slug"]).exists())
