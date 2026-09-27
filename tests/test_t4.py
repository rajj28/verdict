"""End-to-end tests for webhooks, certificates, signed records, and embeds."""
import hashlib
import hmac
import json
import threading
from datetime import timedelta
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from audit.services import record as audit_record
from events.models import Event, EventRole, Role, Track, Prize
from interop.models import JudgeParticipationRecord, WebhookDelivery, WebhookEndpoint
from interop.certificates import certificate_index
from interop.signing import public_key_document, record_document, verify_record_signature
from interop.webhooks import _send, _signature, validate_target
from judging.models import Assignment, Review, ReviewStatus
from projects.models import Project, ProjectStatus
from results.models import ResultPublication
from teams.models import Team, TeamMember


class T4Tests(TestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(
            "organizer@example.test", "testpass123", display_name="Organizer"
        )
        self.participant = User.objects.create_user(
            "participant@example.test", "testpass123", display_name="Participant"
        )
        self.other = User.objects.create_user("other@example.test", "testpass123", display_name="Other")
        self.judge = User.objects.create_user("judge@example.test", "testpass123", display_name="Judge")
        past = timezone.now() - timedelta(days=1)
        self.event = Event.objects.create(
            slug="t4-event",
            name="T4 Event",
            submissions_close_at=past - timedelta(days=2),
            judging_open_at=past - timedelta(days=1),
            judging_close_at=past,
            created_by=self.organizer,
        )
        self.org_role = EventRole.objects.create(
            event=self.event, user=self.organizer, role=Role.ORGANIZER, public_id="org_t4"
        )
        self.member_role = EventRole.objects.create(
            event=self.event, user=self.participant, role=Role.PARTICIPANT, public_id="par_t4"
        )
        self.judge_role = EventRole.objects.create(
            event=self.event, user=self.judge, role=Role.JUDGE, public_id="jdg_t4"
        )
        self.track = Track.objects.create(event=self.event, name="Open")
        self.judge_role.tracks.add(self.track)
        self.team = Team.objects.create(event=self.event, name="Team One", created_by=self.participant)
        TeamMember.objects.create(
            team=self.team, user=self.participant, event=self.event, is_owner=True
        )
        self.project = Project.objects.create(
            event=self.event,
            team=self.team,
            track=self.track,
            public_id="prj_t4",
            title="Public Project",
            summary="A public submission",
            status=ProjectStatus.SUBMITTED,
            first_submitted_at=past,
            last_submitted_at=past,
        )
        assignment = Assignment.objects.create(
            event=self.event, judge=self.judge_role, project=self.project
        )
        self.review = Review.objects.create(
            event=self.event,
            assignment=assignment,
            judge=self.judge_role,
            project=self.project,
            status=ReviewStatus.SUBMITTED,
            submitted_at=past,
        )
        self.prize = Prize.objects.create(
            event=self.event, public_id="prz_t4", name="Grand prize"
        )
        self.publication = ResultPublication.objects.create(
            event=self.event,
            method="normalized",
            awards=[{
                "project_id": self.project.public_id,
                "project": self.project.title,
                "prize_id": self.prize.public_id,
                "prize": "Grand prize",
                "place": 1,
            }],
        )

    def test_webhook_signs_exact_json_body_and_retries_with_backoff(self):
        payload = b'{"type":"project.submitted"}'
        self.assertEqual(
            _signature("shared-secret", payload),
            "sha256=" + hmac.new(b"shared-secret", payload, hashlib.sha256).hexdigest(),
        )

        endpoint = WebhookEndpoint.objects.create(
            event=self.event,
            url="https://example.invalid/hooks",
            secret="shared-secret",
            event_types=["*"],
            created_by=self.organizer,
        )
        delivery = WebhookDelivery.objects.create(
            event=self.event, endpoint=endpoint, event_type="test.event", payload={"type": "test.event"}
        )

        class FakeResponse:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class FakeOpener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return FakeResponse()

        opener = FakeOpener()
        with patch("interop.webhooks.validate_target", return_value=[(2, 1, 6, ("93.184.216.34", 443))]), patch(
            "interop.webhooks.urllib.request.build_opener", return_value=opener
        ):
            status, _, error = _send(endpoint, delivery)
        self.assertEqual(status, 204)
        self.assertEqual(error, "")
        sent_body = json.dumps(
            delivery.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self.assertEqual(
            opener.request.get_header("X-verdict-signature"),
            _signature(endpoint.secret, sent_body),
        )
        self.assertEqual(opener.timeout, 5)

        retry_time = timezone.now()
        with patch("interop.webhooks.now", return_value=retry_time), patch(
            "interop.webhooks._send", return_value=(503, 12, "Receiver returned HTTP 503.")
        ), patch("interop.webhooks.start_delivery"):
            from interop.webhooks import _delivery_loop

            _delivery_loop(delivery.pk)
        delivery.refresh_from_db()
        self.assertEqual(delivery.attempt, 1)
        self.assertEqual(delivery.status, WebhookDelivery.Status.PENDING)
        self.assertEqual(delivery.next_attempt_at, retry_time + timedelta(minutes=1))

    @override_settings(WEBHOOKS_ALLOW_PRIVATE=True)
    def test_pinned_http_delivery_reaches_local_receiver_and_verifies_signature(self):
        received = {}

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received["body"] = body
                received["signature"] = self.headers.get("X-Verdict-Signature")
                self.send_response(204)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Receiver)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = WebhookEndpoint(
                event=self.event,
                url=f"http://127.0.0.1:{server.server_port}/hooks",
                secret="local-demo-secret",
            )
            delivery = WebhookDelivery(
                event=self.event, endpoint=endpoint, event_type="test.event", public_id="whd_test",
                payload={"type": "test.event"},
            )
            status, _, error = _send(endpoint, delivery)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(status, 204)
        self.assertEqual(error, "")
        self.assertEqual(received["signature"], _signature(endpoint.secret, received["body"]))

    def test_audit_actions_enqueue_after_commit_without_network_blocking_write(self):
        endpoint = WebhookEndpoint.objects.create(
            event=self.event,
            url="https://example.invalid/hooks",
            secret="shared-secret",
            event_types=["project.submitted"],
            created_by=self.organizer,
        )
        with patch("interop.webhooks.start_delivery") as start_delivery:
            with self.captureOnCommitCallbacks(execute=True):
                audit_record(
                    self.participant,
                    "project.submitted",
                    event=self.event,
                    target=self.project,
                    summary="Project submitted.",
                    data={"private_score": 99},
                )
        delivery = WebhookDelivery.objects.get(endpoint=endpoint)
        self.assertEqual(delivery.event_type, "project.submitted")
        self.assertNotIn("private_score", json.dumps(delivery.payload))
        start_delivery.assert_called_once_with(delivery.pk)

    @override_settings(WEBHOOKS_ALLOW_PRIVATE=False)
    def test_loopback_webhook_destination_is_refused(self):
        with self.assertRaises(ValueError):
            validate_target("http://127.0.0.1:8765/hook")

    def test_organizer_only_webhook_management(self):
        client = APIClient()
        client.force_authenticate(self.other)
        response = client.post(
            f"/api/v1/events/{self.event.slug}/webhooks",
            {"url": "https://example.invalid/hooks", "event_types": ["*"]},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

        html = Client()
        html.force_login(self.other)
        self.assertEqual(html.get(f"/manage/{self.event.slug}/webhooks").status_code, 403)
        html.force_login(self.organizer)
        self.assertEqual(html.get(f"/manage/{self.event.slug}/webhooks").status_code, 200)

        WebhookEndpoint.objects.create(
            event=self.event, url="https://one.example/hooks", secret="secret-one",
            event_types=["*"], created_by=self.organizer,
        )
        WebhookEndpoint.objects.create(
            event=self.event, url="https://two.example/hooks", secret="secret-two",
            event_types=["project.submitted"], created_by=self.organizer,
        )
        client.force_authenticate(self.organizer)
        with self.assertNumQueries(4):
            listing = client.get(f"/api/v1/events/{self.event.slug}/webhooks")
        self.assertEqual(len(listing.data["endpoints"]), 2)

    @override_settings(WEBHOOKS_ALLOW_PRIVATE=True)
    def test_webhook_endpoint_secret_is_shown_only_on_creation_and_disable_is_api_only(self):
        client = APIClient()
        client.force_authenticate(self.organizer)
        created = client.post(
            f"/api/v1/events/{self.event.slug}/webhooks",
            {"url": "http://127.0.0.1:8765/hooks", "event_types": ["*"]},
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.data["secret"])
        endpoint = WebhookEndpoint.objects.get(public_id=created.data["public_id"])
        listing = client.get(f"/api/v1/events/{self.event.slug}/webhooks")
        self.assertNotIn("secret", listing.data["endpoints"][0])
        disabled = client.delete(
            f"/api/v1/events/{self.event.slug}/webhooks/{endpoint.public_id}"
        )
        self.assertEqual(disabled.status_code, 200)
        endpoint.refresh_from_db()
        self.assertFalse(endpoint.is_active)

    def test_certificate_owner_and_organizer_can_view_but_another_user_cannot(self):
        path = f"/events/{self.event.slug}/certificates/participation/{self.project.public_id}"
        owner_client = Client()
        owner_client.force_login(self.participant)
        response = owner_client.get(path)
        self.assertEqual(response.status_code, 200)
        code = response.context["verification_code"]
        self.assertTrue(response.context["code_valid"])
        self.assertContains(response, code)
        self.assertEqual(owner_client.get(f"{path}?code=wrong").context["code_valid"], False)
        verification = Client().get(response.context["verification_url"].removeprefix("http://testserver"))
        self.assertEqual(verification.status_code, 200)
        self.assertTrue(verification.json()["valid"])

        winner_id = f"{self.publication.public_id}.{self.prize.public_id}.1"
        winner_path = f"/events/{self.event.slug}/certificates/winner/{winner_id}"
        self.assertEqual(owner_client.get(winner_path).status_code, 200)

        judge_client = Client()
        judge_client.force_login(self.judge)
        judge_path = f"/events/{self.event.slug}/certificates/judge/{self.judge_role.public_id}"
        self.assertEqual(judge_client.get(judge_path).status_code, 200)
        self.assertEqual(owner_client.get(judge_path).status_code, 403)

        other_client = Client()
        other_client.force_login(self.other)
        self.assertEqual(other_client.get(path).status_code, 403)
        organizer_client = Client()
        organizer_client.force_login(self.organizer)
        self.assertEqual(organizer_client.get(path).status_code, 200)

    def test_signed_judge_record_is_public_verifiable_tamper_evident_and_revocable(self):
        client = APIClient()
        client.force_authenticate(self.organizer)
        issue = client.post(f"/api/v1/events/{self.event.slug}/judge-records", {}, format="json")
        self.assertEqual(issue.status_code, 200)
        self.assertEqual(issue.data["count"], 1)
        record_row = JudgeParticipationRecord.objects.get(event=self.event, judge=self.judge_role)
        document = record_document(record_row)
        key_doc = public_key_document()
        key = next(row for row in key_doc["keys"] if row["kid"] == record_row.kid)
        self.assertTrue(
            verify_record_signature(document["record"], document["signature"], key["public_key"])
        )
        self.assertNotIn("scores", document["record"])
        self.assertNotIn("project", document["record"])
        self.assertNotIn("email", json.dumps(document))

        public = Client().get(f"/records/{record_row.record_id}?format=json")
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json(), document)
        verify = APIClient().post("/api/v1/records/verify", document, format="json")
        self.assertTrue(verify.data["valid"], verify.data)
        tampered = json.loads(json.dumps(document))
        tampered["record"]["judge"]["display_name"] = "Someone else"
        invalid = APIClient().post("/api/v1/records/verify", tampered, format="json")
        self.assertFalse(invalid.data["valid"])

        revoke = client.post(
            f"/api/v1/events/{self.event.slug}/judge-records/{record_row.record_id}/revoke",
            {},
            format="json",
        )
        self.assertEqual(revoke.status_code, 200)
        self.assertFalse(APIClient().post("/api/v1/records/verify", document, format="json").data["valid"])

    def test_record_issue_waits_until_judging_closes(self):
        self.event.judging_close_at = timezone.now() + timedelta(days=1)
        self.event.save(update_fields=["judging_close_at"])
        client = APIClient()
        client.force_authenticate(self.organizer)
        response = client.post(f"/api/v1/events/{self.event.slug}/judge-records", {}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "judging_open")

    def test_certificate_management_projection_has_constant_query_count(self):
        with self.assertNumQueries(4):
            rows = certificate_index(self.event)
        self.assertEqual(sum(row["kind"] == "participation" for row in rows), 1)
        self.assertEqual(sum(row["kind"] == "judge" for row in rows), 1)
        self.assertEqual(sum(row["kind"] == "winner" for row in rows), 1)

    def test_embed_is_public_cookie_free_and_has_route_scoped_frame_policy(self):
        private_team = Team.objects.create(event=self.event, name="Draft team", created_by=self.other)
        Project.objects.create(
            event=self.event, team=private_team, track=self.track, public_id="prj_draft",
            title="Private draft", status=ProjectStatus.DRAFT,
        )
        client = Client()
        with self.assertNumQueries(4):
            response = client.get(f"/embed/{self.event.slug}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Security-Policy"].split("frame-ancestors ")[1].split(";")[0], "*")
        self.assertNotIn("X-Frame-Options", response.headers)
        self.assertNotIn("Set-Cookie", response.headers)
        self.assertContains(response, "Public Project")
        self.assertNotContains(response, "Private draft")
        self.assertEqual(client.get("/verify").status_code, 200)
        self.assertIn("frame-ancestors 'none'", client.get("/verify")["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", client.get(f"/embed/{self.event.slug}.js")["Content-Security-Policy"])
        self.assertEqual(client.get(f"/embed/{self.event.slug}.js").status_code, 200)
