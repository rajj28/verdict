"""Offline delivery, private event scoping, and configured SMTP failures."""
from datetime import timedelta
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.core import mail, signing
from django.core.mail import EmailMessage
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import include, path
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditEvent
from community.models import Ballot, Voter, VotingAccess, VotingConfig
from community.services import EMAIL_SALT, _secret_hash
from core.clock import now
from core.mail import OutboxBackend, send_event_mail
from core.models import OutboxMessage
from core.outbox_policy import visible_outbox
from events.models import Event, EventRole, Role


@override_settings(EMAIL_BACKEND="core.mail.OutboxBackend")
class OutboxTests(TestCase):
    def setUp(self):
        at = now()
        self.organizer = User.objects.create_user("org@outbox.test")
        self.other_org = User.objects.create_user("other@outbox.test")
        self.participant = User.objects.create_user("participant@outbox.test")
        self.judge = User.objects.create_user("judge@outbox.test")
        self.admin = User.objects.create_user("admin@outbox.test", is_admin=True)
        self.event = Event.objects.create(
            slug="outbox-event", name="Outbox Event", created_by=self.organizer,
            submissions_close_at=at - timedelta(days=1),
            voting_open_at=at - timedelta(hours=1), voting_close_at=at + timedelta(hours=1),
        )
        self.other_event = Event.objects.create(
            slug="other-outbox-event", name="Other Outbox Event", created_by=self.other_org,
            submissions_close_at=at + timedelta(days=1),
        )
        for user, event, role in (
            (self.organizer, self.event, Role.ORGANIZER),
            (self.other_org, self.other_event, Role.ORGANIZER),
            (self.participant, self.event, Role.PARTICIPANT),
            (self.judge, self.event, Role.JUDGE),
        ):
            EventRole.objects.create(user=user, event=event, role=role, public_id=f"{role}_{user.pk}")
        VotingConfig.objects.create(event=self.event, access=VotingAccess.EMAIL)
        self.api = f"/api/v1/events/{self.event.slug}/outbox"
        self.page = f"/manage/{self.event.slug}/outbox"
        self.email_api = f"/api/v1/events/{self.event.slug}/votes/email"

    def queue(self, event=None, *, body="Private capability: secret-token"):
        return send_event_mail(
            event=event, subject="A private message", message=body,
            recipient_list=["recipient@outbox.test"],
        )

    def test_backend_binds_event_per_message_without_cross_event_leakage(self):
        first = EmailMessage("First", "First secret", to=["a@outbox.test"])
        first.verdict_event = self.event
        second = EmailMessage("Second", "Second secret", to=["b@outbox.test"])
        second.verdict_event = self.other_event
        unscoped = EmailMessage("Platform", "Platform secret", to=["c@outbox.test"])
        empty = EmailMessage("No recipient", "Not stored")
        with patch("smtplib.SMTP") as smtp:
            self.assertEqual(OutboxBackend().send_messages([first, second, unscoped, empty]), 3)
        self.assertEqual(OutboxMessage.objects.get(subject="First").event, self.event)
        self.assertEqual(OutboxMessage.objects.get(subject="Second").event, self.other_event)
        self.assertIsNone(OutboxMessage.objects.get(subject="Platform").event)
        smtp.assert_not_called()

    def test_message_creation_audit_has_no_private_body_or_recipients(self):
        self.assertEqual(self.queue(self.event), "queued")
        row = OutboxMessage.objects.get()
        audit = AuditEvent.objects.get(action="mail.queued")
        self.assertEqual(audit.target_id, row.public_id)
        self.assertEqual(audit.data, {"recipient_count": 1})
        text = json.dumps({"summary": audit.summary, "data": audit.data})
        self.assertNotIn("secret-token", text)
        self.assertNotIn("recipient@", text)

    def test_backend_rolls_back_message_if_audit_fails(self):
        with patch("core.mail.audit.services.record", side_effect=ValueError("audit failed")):
            with self.assertRaises(ValueError):
                self.queue(self.event)
        self.assertEqual(OutboxMessage.objects.count(), 0)

    def test_organizer_reads_only_own_event_and_admin_reads_all(self):
        self.queue(self.event, body="own-secret")
        self.queue(self.other_event, body="other-secret")
        self.queue(body="platform-secret")
        client = APIClient()
        client.force_authenticate(self.organizer)
        response = client.get(self.api)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["body"], "own-secret")
        self.assertNotIn("id", response.data["results"][0])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(client.get(f"/api/v1/events/{self.other_event.slug}/outbox").status_code, 403)
        self.assertEqual(client.get("/api/v1/admin/outbox").status_code, 403)
        client.force_authenticate(self.admin)
        response = client.get("/api/v1/admin/outbox")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(client.get(self.api).data["count"], 1)
        client.force_login(self.admin)
        page = client.get("/admin-panel/outbox")
        for secret in ("own-secret", "other-secret", "platform-secret"):
            self.assertContains(page, secret)
        self.assertIn("no-store", page["Cache-Control"])

    def test_anonymous_participant_judge_and_other_organizer_cannot_read_tokens(self):
        self.queue(self.event)
        for user in (None, self.participant, self.judge, self.other_org):
            client = APIClient()
            if user:
                client.force_authenticate(user)
                client.force_login(user)
            for url in (self.api, "/api/v1/admin/outbox", self.page, "/admin-panel/outbox"):
                response = client.get(url)
                self.assertIn(response.status_code, (401, 403))
                self.assertNotIn(b"secret-token", response.content)
        self.organizer.is_active = False
        self.assertFalse(visible_outbox(self.organizer, self.event).exists())

    def test_html_is_private_escaped_and_read_only(self):
        self.queue(self.event, body='<script>alert("capability")</script>')
        self.queue(self.other_event, body="other-event-secret")
        client = Client()
        client.force_login(self.organizer)
        response = client.get(self.page)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("capability")</script>')
        self.assertNotContains(response, "other-event-secret")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(response, "no email was sent")
        self.assertContains(response, "does not independently verify mailbox ownership")
        self.assertEqual(OutboxMessage.objects.count(), 2)

    def test_api_lists_are_paginated_with_constant_query_count(self):
        OutboxMessage.objects.bulk_create([
            OutboxMessage(event=self.event, recipients=["test@outbox.test"], subject=f"Message {i}",
                          body="private", from_email="sender@outbox.test") for i in range(105)
        ])
        client = APIClient()
        client.force_authenticate(self.organizer)
        with self.assertNumQueries(4):
            response = client.get(self.api)
        self.assertEqual(response.data["count"], 105)
        self.assertEqual(len(response.data["results"]), 50)
        with self.assertNumQueries(4):
            second = client.get(self.api + "?page_size=500")
        self.assertEqual(len(second.data["results"]), 100)
        client.force_authenticate(self.admin)
        with self.assertNumQueries(2):
            admin = client.get("/api/v1/admin/outbox?page=2")
        self.assertEqual(len(admin.data["results"]), 50)

    def test_default_offline_email_flow_never_returns_token_to_requester(self):
        with patch("smtplib.SMTP") as smtp:
            response = APIClient().post(self.email_api, {"email": "voter@outbox.test"}, format="json")
        self.assertEqual(response.status_code, 202, response.data)
        self.assertFalse(response.data["sent"])
        self.assertEqual(response.data["delivery"], "queued")
        self.assertIn("no email was sent", response.data["detail"])
        self.assertNotIn("token=", json.dumps(response.data))
        self.assertNotIn("voter@", json.dumps(response.data))
        message = OutboxMessage.objects.get(event=self.event)
        link = message.body.rsplit(" ", 1)[-1]
        ticket = parse_qs(urlparse(link).query)["token"][0]
        self.assertEqual(signing.loads(ticket, salt=EMAIL_SALT)["delivery"], "outbox")
        voted = APIClient().post(self.email_api + "/verify", {"token": ticket}, format="json")
        self.assertEqual(voted.status_code, 201, voted.data)
        self.assertIsNone(Voter.objects.get(event=self.event).verified_at)
        smtp.assert_not_called()
        audit_text = json.dumps(list(AuditEvent.objects.values("summary", "data")))
        self.assertNotIn(ticket, audit_text)
        self.assertNotIn("voter@outbox.test", audit_text)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_existing_locmem_override_and_email_verification_remain_supported(self):
        response = APIClient().post(self.email_api, {"email": "voter@outbox.test"}, format="json")
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.data["sent"])
        self.assertEqual(response.data["delivery"], "sent")
        self.assertEqual(OutboxMessage.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 1)
        ticket = parse_qs(urlparse(mail.outbox[0].body.rsplit(" ", 1)[-1]).query)["token"][0]
        response = APIClient().post(self.email_api + "/verify", {"token": ticket}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(Voter.objects.get(event=self.event).verified_at)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="smtp.example.test")
    def test_unavailable_smtp_returns_domain_error_and_rolls_back(self):
        with patch("django.core.mail.backends.smtp.EmailBackend.send_messages", side_effect=ConnectionRefusedError):
            response = APIClient().post(self.email_api, {"email": "voter@outbox.test"}, format="json")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["error"]["code"], "email_unavailable")
        self.assertEqual(OutboxMessage.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)
        self.assertEqual(Voter.objects.count(), 0)
        self.assertEqual(Ballot.objects.count(), 0)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="smtp.example.test")
    def test_smtp_acceptance_is_reported_without_storing_private_content(self):
        with patch("django.core.mail.backends.smtp.EmailBackend.send_messages", return_value=1) as send:
            response = APIClient().post(self.email_api, {"email": "voter@outbox.test"}, format="json")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["delivery"], "sent")
        self.assertTrue(response.data["sent"])
        self.assertEqual(send.call_args.args[0][0].verdict_event, self.event)
        self.assertEqual(OutboxMessage.objects.count(), 0)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend")
    def test_backend_accepting_no_message_is_not_reported_as_sent(self):
        with patch("django.core.mail.backends.smtp.EmailBackend.send_messages", return_value=0):
            response = APIClient().post(self.email_api, {"email": "voter@outbox.test"}, format="json")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_legacy_ticket_remains_usable_without_claiming_known_delivery(self):
        ticket = signing.dumps(
            {"event": self.event.slug, "email_hash": _secret_hash("legacy@outbox.test")}, salt=EMAIL_SALT,
        )
        response = APIClient().post(self.email_api + "/verify", {"token": ticket}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(Voter.objects.get(event=self.event).verified_at)

    def test_voting_page_displays_server_delivery_result(self):
        response = Client().get(f"/events/{self.event.slug}/vote")
        self.assertContains(response, 'data-response-field="detail"')
        self.assertContains(response, "event organizer delivers the link")


class MailSettingsTests(SimpleTestCase):
    def test_outbox_schema_describes_authenticated_paginated_reads(self):
        from core.outbox_api_urls import urlpatterns

        schema = SchemaGenerator(patterns=[path("api/v1/", include(urlpatterns))]).get_schema(public=True)
        for path_name in ("/api/v1/events/{slug}/outbox", "/api/v1/admin/outbox"):
            operation = schema["paths"][path_name]["get"]
            self.assertIn("401", operation["responses"])
            self.assertIn("403", operation["responses"])
            self.assertTrue(operation["security"])
            self.assertIn("page", [item["name"] for item in operation["parameters"]])
            self.assertIn("PaginatedOutboxMessageList", json.dumps(operation["responses"]["200"]))

    def test_default_and_demo_use_outbox_but_explicit_production_host_enables_smtp(self):
        path = Path(settings.BASE_DIR) / "verdict" / "settings.py"
        for demo, host, expected in (
            ("0", "", "core.mail.OutboxBackend"),
            ("1", "smtp.example.test", "core.mail.OutboxBackend"),
            ("0", "smtp.example.test", "django.core.mail.backends.smtp.EmailBackend"),
        ):
            with self.subTest(demo=demo, host=host), patch.dict(os.environ, {
                "DEMO_MODE": demo, "EMAIL_HOST": host, "SECRET_KEY": "test-settings-only",
            }):
                spec = spec_from_file_location("mail_settings_check", path)
                configured = module_from_spec(spec)
                spec.loader.exec_module(configured)
            self.assertEqual(configured.EMAIL_BACKEND, expected)
            self.assertEqual(configured.EMAIL_TIMEOUT, 10)
