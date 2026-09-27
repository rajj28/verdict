"""Acceptance parity: the Django test client replays run.py's seven checks.

run.py makes seven requests with the demo bearer tokens from .dogfood.toml. These
tests send the same requests through the same code paths, with follow=False and
exact status codes, so a trailing-slash redirect or a DRF content-negotiation
fallback cannot pass CI and fail the real checker.
"""
import json
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.throttling import SimpleRateThrottle

from core.bootstrap import DEMO_EVENT_SLUG, bootstrap
from core.pagination import VerdictPagination
from events.models import Event
from projects.models import Project, ProjectStatus

ORGANIZER = "Bearer vd_demo_organizer_7f2a91c4e0b3"
JUDGE_A = "Bearer vd_demo_judge_a_91bc5d2e8f10"
JUDGE_B = "Bearer vd_demo_judge_b_44de0a7c3b92"
PARTICIPANT = "Bearer vd_demo_participant_2e88f1d4a6c5"

FIXTURE_SLUG = "sample-hack-2026"
GALLERY = "/projects"
SUBMIT = f"/api/v1/events/{FIXTURE_SLUG}/projects"
JUDGE_SCORES = "/api/v1/judge/scores"
PEER_SCORES = f"/api/v1/events/{FIXTURE_SLUG}/judges/jdg_24/scores"
CSV_EXPORT = f"/api/v1/events/{FIXTURE_SLUG}/exports/results.csv"
FIXTURE_TITLES = ("Glass Signal", "Small Meadow", "Cold Chain")


def bearer(token: str) -> dict:
    return {"HTTP_AUTHORIZATION": token}


@override_settings(DEMO_MODE=True)
class AcceptanceParityTests(TestCase):
    """One test per run.py check, plus the refusals the check only samples."""

    @classmethod
    def setUpTestData(cls):
        cls.report = bootstrap()
        cls.fixture_event = Event.objects.get(slug=FIXTURE_SLUG)

    def get(self, path: str, token: str | None = None, data=None):
        headers = bearer(token) if token else {}
        if data is not None:
            return self.client.post(path, data=json.dumps(data), content_type="application/json",
                                    follow=False, **headers)
        return self.client.get(path, follow=False, **headers)

    # --- T1 -------------------------------------------------------------

    def test_t1_gallery_is_public(self):
        response = self.get(GALLERY)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])

    def test_t1_a_fixture_project_is_in_the_body(self):
        body = self.get(GALLERY).content.decode()
        self.assertTrue(any(title in body for title in FIXTURE_TITLES),
                        "no fixture title in the gallery body")

    def test_t1_closed_event_refuses_submissions_with_window_closed(self):
        response = self.get(SUBMIT, PARTICIPANT,
                            data={"title": "dogfood-late-submission-probe", "summary": "probe"})
        self.assertEqual(response.status_code, 403)
        error = response.json()["error"]
        self.assertEqual(error["code"], "window_closed")
        self.assertIn("Submissions for Sample Hack 2026 closed at", error["message"])
        # The window is checked before the body, so no project was created.
        self.assertFalse(Project.objects.filter(title="dogfood-late-submission-probe").exists())

    # --- T2 -------------------------------------------------------------

    def test_t2_judge_sees_own_scores(self):
        response = self.get(JUDGE_SCORES, JUDGE_A)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["judge"]["public_id"], "jdg_24")
        self.assertTrue(payload["reviews"])
        first = payload["reviews"][0]
        self.assertTrue(
            {"event", "project", "status", "criteria", "weighted_score", "comment",
             "submitted_at"} <= set(first)
        )
        self.assertEqual(set(first["project"]), {"public_id", "title"})
        self.assertTrue(0 <= first["weighted_score"] <= 100)

    def test_t2_judge_cannot_read_a_peer(self):
        response = self.get(PEER_SCORES, JUDGE_B)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "forbidden")

    def test_t2_participant_is_blocked(self):
        response = self.get(JUDGE_SCORES, PARTICIPANT)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_a_judge")

    def test_t2_csv_export_works(self):
        response = self.get(CSV_EXPORT, ORGANIZER)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment;", response["Content-Disposition"])
        first_line = response.content.decode().splitlines()[0]
        self.assertIn(",", first_line)
        self.assertEqual(first_line.split(","),
                         ["rank", "project_id", "title", "team", "track", "reviews", "raw_mean",
                          "status"])

    # --- the refusals behind the sampled checks -------------------------

    def test_anonymous_judge_scores_is_401_with_a_bearer_challenge(self):
        response = self.get(JUDGE_SCORES)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["WWW-Authenticate"], 'Bearer realm="api"')
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")

    def test_judge_b_cannot_ask_for_judge_a_through_the_query_string(self):
        response = self.get(f"{JUDGE_SCORES}?judge=jdg_24", JUDGE_B)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "forbidden")

    def test_judge_can_ask_for_own_scores_by_query(self):
        response = self.get(f"{JUDGE_SCORES}?judge=jdg_24", JUDGE_A)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["judge"]["public_id"], "jdg_24")

    def test_organizer_reads_any_judge_scores(self):
        response = self.get(PEER_SCORES, ORGANIZER)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["judge"]["public_id"], "jdg_24")
        self.assertTrue(response.json()["reviews"])

    def test_organizer_gets_404_for_an_unknown_judge_id(self):
        response = self.get(f"/api/v1/events/{FIXTURE_SLUG}/judges/jdg_zzz/scores", ORGANIZER)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "judge_not_found")

    def test_judge_b_gets_403_not_404_for_an_unknown_judge_id(self):
        # The id is refused before it is resolved, so judge ids cannot be probed.
        response = self.get(f"/api/v1/events/{FIXTURE_SLUG}/judges/jdg_zzz/scores", JUDGE_B)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_peer_scores_is_401(self):
        self.assertEqual(self.get(PEER_SCORES).status_code, 401)

    def test_open_event_refuses_a_participant_without_a_team(self):
        # priya1 is a participant of the fixture event only, so in the open demo
        # event she has no team yet.
        response = self.get(f"/api/v1/events/{DEMO_EVENT_SLUG}/projects", PARTICIPANT,
                            data={"title": "No team yet", "summary": "probe"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_a_participant")

    def test_anonymous_submit_is_401(self):
        response = self.get(SUBMIT, data={"title": "x", "summary": "y"})
        self.assertEqual(response.status_code, 401)

    def test_export_is_organizer_only(self):
        for token in (JUDGE_A, JUDGE_B, PARTICIPANT):
            with self.subTest(token=token):
                response = self.get(CSV_EXPORT, token)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error"]["code"], "forbidden")

    def test_unknown_export_kind_is_404_for_an_organizer(self):
        response = self.get(f"/api/v1/events/{FIXTURE_SLUG}/exports/secrets.csv", ORGANIZER)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "unknown_export")

    def test_anonymous_export_is_401(self):
        self.assertEqual(self.get(CSV_EXPORT).status_code, 401)

    def test_reviews_csv_has_one_column_per_criterion(self):
        response = self.get(f"/api/v1/events/{FIXTURE_SLUG}/exports/reviews.csv", ORGANIZER)
        self.assertEqual(response.status_code, 200)
        header = response.content.decode().splitlines()[0].split(",")
        self.assertEqual(header[:7], ["review_id", "judge_id", "judge_name", "project_id",
                                      "project_title", "track", "status"])
        self.assertEqual(header[7:10], ["functionality", "quality", "innovation"])
        self.assertEqual(header[10:], ["weighted_score", "comment"])

    def test_healthz_reports_ok(self):
        response = self.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})


@override_settings(DEMO_MODE=True)
class GalleryScopingTests(TestCase):
    """The gallery is public, scoped and free of N+1 queries."""

    @classmethod
    def setUpTestData(cls):
        bootstrap()
        cls.fixture_event = Event.objects.get(slug=FIXTURE_SLUG)

    def test_gallery_drops_a_project_that_leaves_the_submitted_status(self):
        project = Project.objects.get(public_id="prj_01")
        self.assertContains(self.client.get("/projects"), project.title)
        project.status = ProjectStatus.WITHDRAWN
        project.save(update_fields=["status"])
        self.assertNotContains(self.client.get("/projects"), project.title)

    def test_gallery_search_filters_by_title(self):
        response = self.client.get("/projects", {"q": "Glass Signal"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Glass Signal")
        self.assertNotContains(response, "Small Meadow")

    def test_gallery_track_filter(self):
        project = Project.objects.filter(event=self.fixture_event, track__isnull=False).first()
        response = self.client.get("/projects", {"track": project.track.public_id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, project.title)

    def test_gallery_is_a_constant_number_of_queries(self):
        with self.assertNumQueries(3):
            # 1 count, 1 page of projects, 1 list of track options.
            self.client.get("/projects")

    def test_gallery_page_is_48_projects(self):
        response = self.client.get("/projects")
        self.assertContains(response, "Glass Signal")  # prj_01, first three titles by title
        self.assertEqual(response.context["page_obj"].paginator.per_page, 48)


@override_settings(DEMO_MODE=True)
class ProjectListApiTests(TestCase):
    """GET on the submit route is the public listing next to the POST."""

    @classmethod
    def setUpTestData(cls):
        bootstrap()

    def get(self, path: str, token: str | None = None):
        return self.client.get(path, follow=False, **(bearer(token) if token else {}))

    def test_the_listing_is_public_and_paginated(self):
        response = self.get(f"/api/v1/events/{FIXTURE_SLUG}/projects")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(sorted(body), ["count", "next", "previous", "results"])
        # 41 imported projects, one of which is superseded and therefore not listed.
        self.assertEqual(body["count"], 40)
        self.assertEqual(len(body["results"]), 40)
        first = body["results"][0]
        self.assertEqual(sorted(first), ["event", "first_submitted_at", "last_submitted_at",
                                          "public_id", "revision", "status", "summary", "team",
                                          "title", "track"])
        self.assertEqual(first["event"], FIXTURE_SLUG)
        self.assertNotIn("id", first)

    def test_the_listing_only_shows_submitted_projects(self):
        Project.objects.filter(event__slug=FIXTURE_SLUG, public_id="prj_01").update(
            status=ProjectStatus.DRAFT
        )
        body = self.get(f"/api/v1/events/{FIXTURE_SLUG}/projects").json()
        self.assertEqual(body["count"], 39)
        self.assertNotIn("prj_01", [row["public_id"] for row in body["results"]])

    def test_a_page_size_is_honoured_but_capped(self):
        self.assertEqual(VerdictPagination.page_size, 50)
        self.assertEqual(VerdictPagination.max_page_size, 100)
        body = self.get(f"/api/v1/events/{FIXTURE_SLUG}/projects?page_size=10").json()
        self.assertEqual(len(body["results"]), 10)
        self.assertIn("page=2", body["next"])
        # 1000 asks for more than the cap allows, and still gets at most 100 rows.
        capped = self.get(f"/api/v1/events/{FIXTURE_SLUG}/projects?page_size=1000").json()
        self.assertLessEqual(len(capped["results"]), VerdictPagination.max_page_size)
        self.assertEqual(capped["count"], 40)

    def test_an_unknown_event_is_404(self):
        response = self.get("/api/v1/events/no-such-event/projects")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "event_not_found")

    def test_the_listing_is_a_constant_number_of_queries(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.get(f"/api/v1/events/{FIXTURE_SLUG}/projects")
        self.assertEqual(response.status_code, 200)
        # 1 event lookup, 1 count for the page, 1 page of projects with its relations.
        self.assertEqual(len(queries.captured_queries), 3)


@override_settings(DEMO_MODE=True)
class ThrottleTests(TestCase):
    """The anonymous budget from BUILD-SEC section 6 answers 429 in our envelope."""

    @classmethod
    def setUpTestData(cls):
        bootstrap()

    def setUp(self):
        # Throttle state lives in the cache, which outlives one test method.
        cache.clear()
        self.addCleanup(cache.clear)

    def test_anonymous_requests_stop_at_their_budget(self):
        # DRF copies DEFAULT_THROTTLE_RATES onto the class at import time, so
        # override_settings cannot move the budget; patching the class can.
        rates = {**SimpleRateThrottle.THROTTLE_RATES, "anon": "2/min"}
        path = f"/api/v1/events/{FIXTURE_SLUG}/projects"
        with mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", rates):
            self.assertEqual(self.client.get(path).status_code, 200)
            self.assertEqual(self.client.get(path).status_code, 200)
            response = self.client.get(path)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "throttled")

    def test_the_configured_budgets_are_the_ones_the_spec_asks_for(self):
        self.assertEqual(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
                         {"anon": "120/min", "user": "1200/min", "login": "10/15min"})
        self.assertEqual(settings.REST_FRAMEWORK["EXCEPTION_HANDLER"],
                         "core.errors.api_exception_handler")
        self.assertEqual(settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"],
                         ["accounts.auth.BearerTokenAuthentication",
                          "rest_framework.authentication.SessionAuthentication"])
