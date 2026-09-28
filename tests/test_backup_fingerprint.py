"""Tests for ``manage.py backup_fingerprint``.

The fingerprint is the proof a restored portal is the portal that was backed up,
so it must be stable, complete and free of anything secret. These tests pin all
three properties, plus the read-only guarantee the backup script relies on.
"""

import json
from datetime import timedelta
from io import StringIO

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import ApiToken, User
from audit import services as audit_services
from audit.models import AuditEvent
from events.models import Event
from results.models import ResultPublication

_EMAIL = "organizer@backup-fingerprint.test"
_PASSWORD = "fingerprint-test-password-9f2c"


def run_fingerprint() -> tuple[str, dict]:
    """Call the command and return its raw stdout plus the parsed document."""
    out = StringIO()
    call_command("backup_fingerprint", stdout=out)
    raw = out.getvalue()
    return raw, json.loads(raw)


class BackupFingerprintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organizer = User.objects.create_user(
            _EMAIL, _PASSWORD, display_name="Fingerprint Organizer"
        )
        cls.token = ApiToken.objects.create(
            user=cls.organizer,
            name="fingerprint token",
            prefix="vdttest",
            key_hash="f" * 64,
        )
        cls.event = Event.objects.create(
            slug="fingerprint-event",
            name="Fingerprint Event",
            created_by=cls.organizer,
            submissions_close_at=timezone.now() + timedelta(days=1),
        )
        cls.entry = audit_services.record(
            cls.organizer, "event.created", event=cls.event,
            target=cls.event, summary="Event created.",
        )
        cls.publication = ResultPublication.objects.create(
            event=cls.event,
            version=1,
            method="borda",
            inputs={"projects": []},
            input_digest="a" * 64,
            rows=[],
            published_by=cls.organizer,
        )

    def test_output_is_json_with_sorted_keys(self):
        raw, document = run_fingerprint()
        self.assertEqual(document["fingerprint_version"], 1)
        # Serialisation must be canonical so two dumps can be compared as bytes.
        self.assertEqual(
            raw, json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        )

    def test_output_is_stable_across_two_calls(self):
        self.assertEqual(run_fingerprint()[0], run_fingerprint()[0])

    def test_row_counts_cover_every_model_in_the_database(self):
        _, document = run_fingerprint()
        expected = {
            model._meta.label_lower
            for model in apps.get_models(include_auto_created=False)
            if not model._meta.proxy
        }
        self.assertEqual(set(document["rows"]), expected)
        self.assertEqual(document["rows"]["accounts.user"], 1)
        self.assertEqual(document["rows_total"], sum(document["rows"].values()))

    def test_row_counts_change_when_a_row_is_added(self):
        before = run_fingerprint()[1]
        Event.objects.create(
            slug="fingerprint-event-2",
            name="Second Event",
            created_by=self.organizer,
            submissions_close_at=timezone.now() + timedelta(days=1),
        )
        after = run_fingerprint()[1]
        self.assertEqual(after["rows"]["events.event"], before["rows"]["events.event"] + 1)
        self.assertEqual(after["rows_total"], before["rows_total"] + 1)
        self.assertNotEqual(after, before)

    def test_audit_head_reports_the_latest_sequence_and_hash(self):
        _, document = run_fingerprint()
        audit = document["audit"]
        self.assertEqual(audit["head_sequence"], self.entry.sequence)
        self.assertEqual(audit["head_hash"], self.entry.entry_hash)
        self.assertEqual(audit["head_chain_id"], self.entry.chain.public_id)
        self.assertEqual(audit["entries"], AuditEvent.objects.count())
        self.assertIn(
            {
                "chain_id": self.entry.chain.public_id,
                "event_slug": self.event.slug,
                "head_hash": self.entry.entry_hash,
                "sequence": self.entry.sequence,
            },
            audit["chains"],
        )

    def test_audit_head_advances_with_the_chain(self):
        before = run_fingerprint()[1]["audit"]
        entry = audit_services.record(
            self.organizer, "event.updated", event=self.event,
            target=self.event, summary="Event updated.",
        )
        after = run_fingerprint()[1]["audit"]
        self.assertGreater(after["head_sequence"], before["head_sequence"])
        self.assertEqual(after["head_hash"], entry.entry_hash)

    def test_publications_carry_public_id_version_and_stored_digest(self):
        _, document = run_fingerprint()
        self.assertEqual(
            document["publications"],
            [
                {
                    "event": self.event.slug,
                    "input_digest": "a" * 64,
                    "public_id": self.publication.public_id,
                    "version": 1,
                }
            ],
        )

    def test_publications_are_listed_in_a_stable_order(self):
        second = ResultPublication.objects.create(
            event=self.event, version=2, method="borda", input_digest="b" * 64,
            published_by=self.organizer,
        )
        first_run = run_fingerprint()[1]["publications"]
        second_run = run_fingerprint()[1]["publications"]
        self.assertEqual(first_run, second_run)
        self.assertEqual([row["version"] for row in first_run], [1, 2])
        self.assertEqual(first_run[1]["public_id"], second.public_id)

    def test_output_carries_no_secrets(self):
        raw, _ = run_fingerprint()
        for value in (
            _EMAIL,
            self.organizer.password,
            settings.SECRET_KEY,
            settings.SECRET_KEY[:16],
            self.token.key_hash,
            self.token.prefix,
            self.organizer.display_name,
        ):
            self.assertNotIn(value, raw)
        # No email-shaped string and no password-hash marker anywhere in the output.
        self.assertNotIn("@", raw)
        self.assertNotIn("pbkdf2", raw)
        # Model labels are the only names in the output, and none of them is a secret.
        self.assertNotIn("email", raw.lower())
        self.assertNotIn("secret", raw.lower())

    def test_command_is_read_only(self):
        before = run_fingerprint()[1]
        counts = {
            model._meta.label_lower: model.objects.count()
            for model in apps.get_models(include_auto_created=False)
        }
        after = run_fingerprint()[1]
        self.assertEqual(before, after)
        self.assertEqual(
            counts,
            {
                model._meta.label_lower: model.objects.count()
                for model in apps.get_models(include_auto_created=False)
            },
        )
        # A read-only command must not append to the chain it is reporting on.
        self.assertEqual(AuditEvent.objects.filter(action="backup_fingerprint").count(), 0)
