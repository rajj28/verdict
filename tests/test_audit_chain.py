"""Tamper evidence, rollback, policy, legacy migration and PostgreSQL append races."""
import hashlib
import itertools
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import User
from audit.models import AuditChainHead, AuditEvent
from audit.policy import visible_audit_events
from audit.services import GENESIS_HASH, canonical_entry, export_chain, record, verify_chain
from core.errors import ApiError
from events.models import Event, EventRole, Role


_EMAIL_SEQUENCE = itertools.count(1)


def make_event(slug="audit-chain"):
    """Use a separate creator so deleting an audited actor is not protected."""
    owner = User.objects.create_user(
        f"owner-{slug}-{next(_EMAIL_SEQUENCE)}@audit.test", "test-password")
    return Event.objects.create(slug=slug, name=slug, created_by=owner,
                                submissions_close_at=timezone.now() + timedelta(days=1))


class AuditChainTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.actor = User.objects.create_user("audit@example.test", "test-password")

    def add(self, text="entry", event=None):
        return record(self.actor, "test.action", event=event or self.event,
                      summary=text, data={"z": 2, "a": ["é", 1]})

    def raw(self, sql, params):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)

    def test_append_links_heads_and_independent_serialization(self):
        first = self.add("first")
        second = self.add("second")
        self.assertEqual(first.previous_hash, GENESIS_HASH)
        self.assertEqual(second.previous_hash, first.entry_hash)
        payload = canonical_entry(second, second.chain.public_id)
        independently_hashed = hashlib.sha256(json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
        self.assertEqual(second.entry_hash, independently_hashed)
        result = verify_chain(self.event)
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 2)
        self.assertEqual(result["head_hash"], second.entry_hash)

    def test_event_and_global_chains_are_independent(self):
        local = self.add()
        global_row = record(None, "system.test", summary="global")
        other = self.add(event=make_event("another"))
        self.assertEqual({local.sequence, global_row.sequence, other.sequence}, {1})
        self.assertEqual(len({local.chain_id, global_row.chain_id, other.chain_id}), 3)
        self.assertEqual(verify_chain()["checked"], 1)

    def test_empty_event_reports_event_scope_without_writing(self):
        self.assertEqual(verify_chain(self.event)["checkpoint"]["scope"], "event")
        self.assertFalse(AuditChainHead.objects.exists())

    def test_transaction_rollback_removes_entry_and_new_head(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.add()
                raise RuntimeError("business change failed")
        self.assertFalse(AuditEvent.objects.exists())
        self.assertFalse(AuditChainHead.objects.exists())

    def test_existing_head_rollback_keeps_original_prefix(self):
        first = self.add()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.add("rolled back")
                raise RuntimeError
        self.assertEqual(verify_chain(self.event)["head_hash"], first.entry_hash)
        self.assertEqual(self.add("next").sequence, 2)

    def test_model_and_queryset_mutations_are_refused(self):
        entry = self.add()
        for operation in (
            lambda: entry.save(), lambda: entry.delete(),
            lambda: AuditEvent.objects.filter(pk=entry.pk).update(summary="changed"),
            lambda: AuditEvent.objects.filter(pk=entry.pk).delete(),
            lambda: AuditEvent.objects.create(action="bypass", summary="bypass"),
            lambda: AuditEvent.objects.bulk_create([AuditEvent(action="bypass", summary="bypass")]),
        ):
            with self.assertRaises(ValueError):
                operation()
        self.assertTrue(verify_chain(self.event)["ok"])

    def test_raw_sql_content_edit_is_detected(self):
        entry = self.add()
        self.raw("UPDATE audit_audit_event SET summary = %s WHERE id = %s", ["altered", entry.pk])
        self.assertFalse(verify_chain(self.event)["ok"])

    def test_raw_sql_tail_deletion_is_detected_against_head(self):
        self.add("first")
        tail = self.add("tail")
        self.raw("DELETE FROM audit_audit_event WHERE id = %s", [tail.pk])
        result = verify_chain(self.event)
        self.assertFalse(result["ok"])
        self.assertIn("tail deletion", " ".join(result["errors"]))

    def test_raw_sql_middle_deletion_and_head_edit_are_detected(self):
        self.add("first")
        middle = self.add("middle")
        tail = self.add("tail")
        self.raw("DELETE FROM audit_audit_event WHERE id = %s", [middle.pk])
        self.raw("UPDATE audit_chain_head SET sequence = 2 WHERE id = %s", [tail.chain_id])
        self.assertFalse(verify_chain(self.event)["ok"])

    def test_actor_rename_and_deletion_preserve_identity_and_hash(self):
        entry = self.add()
        actor_id = self.actor.public_id
        self.actor.display_name = "renamed"
        self.actor.save(update_fields=["display_name"])
        self.actor.delete()
        entry.refresh_from_db()
        self.assertIsNone(entry.actor_id)
        self.assertEqual(entry.actor_public_id, actor_id)
        self.assertTrue(verify_chain(self.event)["ok"])

    def test_deleted_event_chain_is_retained_and_not_reused_by_slug(self):
        entry = self.add()
        chain_id = entry.chain.public_id
        self.event.delete()
        entry.refresh_from_db()
        self.assertIsNone(entry.event_id)
        self.assertEqual(entry.event_slug, "audit-chain")
        replacement = make_event("audit-chain")
        self.assertEqual(verify_chain(replacement)["checked"], 0)
        self.assertEqual(verify_chain(chain_id=chain_id)["checked"], 1)
        self.assertEqual(verify_chain()["checked"], 0)

    def test_export_is_private_canonical_data_with_limit_fallback(self):
        entry = self.add()
        document = export_chain(self.event)
        self.assertTrue(document["entries_included"])
        self.assertEqual(document["entries"][0]["entry_hash"], entry.entry_hash)
        self.assertNotIn("actor_id", document["entries"][0]["payload"])
        with patch("audit.services.EXPORT_LIMIT", 0):
            document = export_chain(self.event)
        self.assertFalse(document["entries_included"])
        self.assertEqual(document["checkpoint"]["sequence"], 1)
        self.assertNotIn("entries", document)

    def test_verification_query_count_does_not_grow_per_entry(self):
        self.add()
        with self.assertNumQueries(4):
            verify_chain(self.event)
        for i in range(5):
            self.add(str(i))
        with self.assertNumQueries(4):
            verify_chain(self.event)

    def test_every_verification_carries_the_external_checkpoint_limitation(self):
        self.add()
        for result in (verify_chain(self.event), export_chain(self.event)):
            self.assertIn("not proof that no history ever existed", result["limitation"])
            self.assertIn("outside the database", result["limitation"])
            self.assertTrue(result["retained_head_present"])

    def test_an_empty_chain_is_consistent_but_retains_no_evidence(self):
        result = verify_chain(make_event("audit-empty"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 0)
        self.assertFalse(result["retained_head_present"])
        self.assertIn("not proof that no history ever existed", result["limitation"])
        self.assertEqual(export_chain(make_event("audit-empty-2"))["limitation"],
                         result["limitation"])


class AuditAccessTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.organizer = User.objects.create_user("organizer@audit.test", "test-password")
        self.outsider = User.objects.create_user("outsider@audit.test", "test-password")
        self.admin = User.objects.create_user("admin@audit.test", "test-password", is_admin=True)
        EventRole.objects.create(event=self.event, user=self.organizer, role=Role.ORGANIZER)
        self.entry = record(self.organizer, "test.action", event=self.event, summary="private note")

    def query_count(self, path: str) -> int:
        with CaptureQueriesContext(connection) as captured:
            self.client.get(path)
        return len(captured)

    def test_audit_list_exposes_public_entry_ids_without_per_row_queries(self):
        self.client.force_login(self.organizer)
        path = f"/api/v1/events/{self.event.slug}/audit"
        first = self.client.get(path)
        queries = self.query_count(path)
        other_event = make_event("audit-elsewhere")
        for index in range(6):
            record(self.organizer, "test.action", event=self.event, summary=f"extra {index}")
        record(self.organizer, "test.action", event=other_event, summary="other event only")
        second = self.client.get(path)
        self.assertEqual(queries, self.query_count(path))
        row = first.json()["results"][0]
        self.assertNotIn("id", row)
        self.assertEqual(row["entry_id"], f"{self.entry.chain.public_id}:{self.entry.sequence}")
        self.assertEqual(row["actor_public_id"], self.organizer.public_id)
        rows = second.json()["results"]
        self.assertEqual(len(rows), 7)
        self.assertNotIn("other event only", [item["summary"] for item in rows])

    def test_audit_list_and_verify_responses_are_private(self):
        self.client.force_login(self.organizer)
        for path in (f"/api/v1/events/{self.event.slug}/audit",
                     f"/api/v1/events/{self.event.slug}/audit/verify",
                     f"/api/v1/events/{self.event.slug}/audit/checkpoint"):
            self.assertEqual(self.client.get(path)["Cache-Control"], "private, no-store")

    def test_actor_filter_uses_the_snapshot_after_the_actor_is_deleted(self):
        actor = User.objects.create_user("temp-actor@audit.test", "test-password")
        record(actor, "test.action", event=self.event, summary="by a soon-deleted actor")
        scoped = visible_audit_events(self.organizer, self.event, actor_public_id=actor.public_id)
        self.assertEqual([row.summary for row in scoped], ["by a soon-deleted actor"])
        actor.delete()
        scoped = visible_audit_events(self.organizer, self.event, actor_public_id=actor.public_id)
        self.assertEqual([row.summary for row in scoped], ["by a soon-deleted actor"])
        self.client.force_login(self.organizer)
        response = self.client.get(
            f"/api/v1/events/{self.event.slug}/audit?actor={actor.public_id}"
        )
        self.assertEqual(
            [row["summary"] for row in response.json()["results"]],
            ["by a soon-deleted actor"],
        )

    def test_the_policy_helper_refuses_callers_who_are_not_organizers(self):
        for user, status in ((self.outsider, 403), (None, 401)):
            with self.assertRaises(ApiError) as ctx:
                visible_audit_events(user, self.event)
            self.assertEqual(ctx.exception.status_code, status)

    def test_event_organizer_can_verify_and_download_but_outsider_cannot(self):
        for suffix in ("verify", "checkpoint"):
            path = f"/api/v1/events/{self.event.slug}/audit/{suffix}"
            self.assertEqual(self.client.get(path).status_code, 401)
            self.client.force_login(self.outsider)
            self.assertEqual(self.client.get(path).status_code, 403)
            self.client.force_login(self.organizer)
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
            self.assertEqual(response["Cache-Control"], "private, no-store")
            self.assertIn("not proof that no history ever existed", response.json()["limitation"])
            if suffix == "checkpoint":
                self.assertIn("attachment", response["Content-Disposition"])
            self.client.logout()

    def test_global_and_archived_lookup_are_admin_only(self):
        paths = ["/api/v1/admin/audit/verify", "/api/v1/admin/audit/checkpoint",
                 f"/api/v1/admin/audit/{self.entry.chain.public_id}/verify"]
        for path in paths:
            self.client.force_login(self.organizer)
            self.assertEqual(self.client.get(path).status_code, 403)
            self.client.force_login(self.admin)
            self.assertEqual(self.client.get(path).status_code, 200)

    def test_page_explains_external_checkpoint_limit(self):
        self.client.force_login(self.organizer)
        response = self.client.get(f"/manage/{self.event.slug}/audit")
        self.assertContains(response, "Retained chain verifies")
        self.assertContains(response, "not tamper-proof")
        self.assertContains(response, "Save integrity checkpoint")


class LegacyAuditMigrationTests(TransactionTestCase):
    def test_legacy_entries_are_backfilled_in_order_and_identified(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("audit", "0001_initial")])
        old_apps = executor.loader.project_state([("audit", "0001_initial")]).apps
        OldEntry = old_apps.get_model("audit", "AuditEvent")
        OldEntry.objects.create(action="legacy.first", summary="first")
        OldEntry.objects.create(action="legacy.second", summary="second")
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([("audit", "0002_audit_chain")])
            result = verify_chain()
            self.assertTrue(result["ok"])
            self.assertEqual(result["checked"], 2)
            self.assertEqual(result["checkpoint"]["legacy_entries"], 2)
        finally:
            MigrationExecutor(connection).migrate([("audit", "0002_audit_chain")])


@skipUnless(connection.vendor == "postgresql", "PostgreSQL locking test")
class ConcurrentAuditTests(TransactionTestCase):
    def test_simultaneous_first_appends_create_one_serial_chain(self):
        event = make_event("concurrent-audit")
        barrier = Barrier(4)

        def append(index):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                for ordinal in range(3):
                    record(None, "race.append", event=event, summary=f"{index}:{ordinal}")
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(append, range(4)))
        self.assertEqual(AuditChainHead.objects.filter(scope_key=f"event:{event.pk}").count(), 1)
        result = verify_chain(event)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["checked"], 12)
