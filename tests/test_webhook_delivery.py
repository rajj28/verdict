"""Durable scheduling, ownership, cancellation and resource bounds for webhooks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
import threading
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connections, transaction
from django.db.models.query import QuerySet
from django.test import SimpleTestCase, TransactionTestCase, skipUnlessDBFeature

from accounts.models import User
from audit.services import record
from core.clock import now
from events.models import Event
from interop.models import WebhookDelivery, WebhookEndpoint
from interop.services import disable_endpoint
from interop.webhooks import (
    DeliveryWorker, LEASE_SECONDS, MAX_WORKERS, RETRY_SECONDS,
    claim_due, deliver_claim, enqueue_test, replay_delivery,
)


class WebhookDeliveryTests(TransactionTestCase):
    def setUp(self):
        self.at = now()
        self.user = User.objects.create_user("webhook-admin@example.test", is_admin=True)
        self.event = Event.objects.create(
            slug="webhook-outbox", name="Webhook outbox", created_by=self.user,
            submissions_close_at=self.at + timedelta(days=1),
        )
        self.endpoint = WebhookEndpoint.objects.create(
            event=self.event, url="https://example.invalid/hook", secret="test-only",
            event_types=["project.submitted"], created_by=self.user,
        )

    def delivery(self, **fields):
        return WebhookDelivery.objects.create(
            event=self.event, endpoint=self.endpoint, event_type="project.submitted",
            payload={"type": "project.submitted"}, **fields,
        )

    def test_transaction_rollback_removes_outbox_and_never_sends(self):
        with patch("interop.webhooks._send") as send:
            with self.assertRaisesRegex(ValueError, "rollback"):
                with transaction.atomic():
                    record(self.user, "project.submitted", event=self.event, summary="Submitted.")
                    self.assertEqual(WebhookDelivery.objects.count(), 1)
                    raise ValueError("rollback")
        self.assertEqual(WebhookDelivery.objects.count(), 0)
        send.assert_not_called()

    def test_enqueues_are_durable_without_starting_threads(self):
        with patch("interop.webhooks.threading.Thread") as thread, patch("interop.webhooks._send") as send:
            tested = enqueue_test(self.endpoint)
            replay = replay_delivery(tested)
        self.assertNotEqual(tested.public_id, replay.public_id)
        self.assertEqual(tested.payload, replay.payload)
        self.assertEqual(WebhookDelivery.objects.count(), 2)
        thread.assert_not_called()
        send.assert_not_called()

    def test_claims_only_due_pending_enabled_deliveries(self):
        due = self.delivery()
        self.delivery(next_attempt_at=self.at + timedelta(seconds=1))
        self.delivery(status=WebhookDelivery.Status.DELIVERED)
        self.delivery(status=WebhookDelivery.Status.FAILED)
        self.delivery(lease_token="owned", lease_expires_at=self.at + timedelta(seconds=1))
        other_endpoint = WebhookEndpoint.objects.create(
            event=self.event, url=self.endpoint.url, secret="test-only", is_active=False,
        )
        WebhookDelivery.objects.create(
            event=self.event, endpoint=other_endpoint, event_type="project.submitted",
        )
        with patch("interop.webhooks.now", return_value=self.at):
            claims = claim_due(MAX_WORKERS)
        self.assertEqual([pk for pk, _ in claims], [due.pk])
        due.refresh_from_db()
        self.assertEqual(due.lease_token, claims[0][1])
        self.assertEqual(due.lease_expires_at, self.at + timedelta(seconds=LEASE_SECONDS))

    def test_only_one_live_claim_and_expired_claim_recovers_after_restart(self):
        delivery = self.delivery()
        with patch("interop.webhooks.now", return_value=self.at):
            first = claim_due(1)[0]
            self.assertEqual(claim_due(1), [])
        with patch("interop.webhooks.now", return_value=self.at + timedelta(seconds=LEASE_SECONDS)):
            recovered = claim_due(1)[0]
        self.assertEqual(recovered[0], delivery.pk)
        self.assertNotEqual(first[1], recovered[1])
        with patch("interop.webhooks._send") as send:
            deliver_claim(*first)
        send.assert_not_called()

    def test_expired_claim_is_not_sent_without_reclaiming(self):
        self.delivery()
        with patch("interop.webhooks.now", return_value=self.at):
            claim = claim_due(1)[0]
        with patch("interop.webhooks.now", return_value=self.at + timedelta(seconds=LEASE_SECONDS)), patch(
            "interop.webhooks._send",
        ) as send:
            deliver_claim(*claim)
        send.assert_not_called()

    def test_failure_backoff_persists_for_a_new_worker(self):
        delivery = self.delivery()
        with patch("interop.webhooks.now", return_value=self.at), patch(
            "interop.webhooks._send", return_value=(503, 4, "Receiver returned HTTP 503."),
        ):
            deliver_claim(*claim_due(1)[0])
        delivery.refresh_from_db()
        self.assertEqual(delivery.attempt, 1)
        self.assertEqual(delivery.next_attempt_at, self.at + timedelta(seconds=60))
        self.assertEqual(delivery.lease_token, "")
        with patch("interop.webhooks.now", return_value=self.at + timedelta(seconds=59)), patch(
            "interop.webhooks._send",
        ) as send:
            DeliveryWorker().run(once=True)
        send.assert_not_called()
        seen_ids = []

        def success(endpoint, attempted):
            seen_ids.append(attempted.public_id)
            return 204, 1, ""

        with patch("interop.webhooks.now", return_value=self.at + timedelta(seconds=60)), patch(
            "interop.webhooks._send", side_effect=success,
        ):
            DeliveryWorker().run(once=True)
        delivery.refresh_from_db()
        self.assertEqual(seen_ids, [delivery.public_id])
        self.assertEqual(delivery.status, WebhookDelivery.Status.DELIVERED)
        self.assertEqual(delivery.attempt, 2)
        self.assertIsNone(delivery.next_attempt_at)

    def test_unavailable_receiver_exhausts_retries_without_sleeping(self):
        delivery = self.delivery()
        at = self.at
        with patch("interop.webhooks._send", side_effect=OSError("unavailable")), patch(
            "interop.webhooks.time.sleep",
        ) as sleep:
            for attempt in range(1, len(RETRY_SECONDS) + 2):
                with patch("interop.webhooks.now", return_value=at):
                    deliver_claim(*claim_due(1)[0])
                delivery.refresh_from_db()
                self.assertEqual(delivery.attempt, attempt)
                if attempt <= len(RETRY_SECONDS):
                    at += timedelta(seconds=RETRY_SECONDS[attempt - 1])
                    self.assertEqual(delivery.next_attempt_at, at)
        self.assertEqual(delivery.status, WebhookDelivery.Status.FAILED)
        self.assertIsNone(delivery.next_attempt_at)
        self.assertEqual(claim_due(1), [])
        sleep.assert_not_called()

    def test_network_attempt_closes_connections_before_dns_and_after_result(self):
        self.delivery()
        claim = claim_due(1)[0]
        calls = []
        close_all = connections.close_all

        def close():
            calls.append("close")
            close_all()

        def send(*args):
            calls.append("send")
            self.assertFalse(connections["default"].in_atomic_block)
            self.assertIsNone(connections["default"].connection)
            return 204, 1, ""

        with patch("interop.webhooks.connections.close_all", side_effect=close), patch(
            "interop.webhooks._send", side_effect=send,
        ):
            deliver_claim(*claim)
        self.assertEqual(calls, ["close", "send", "close"])

    def test_disable_cancels_queued_and_claimed_deliveries(self):
        queued = self.delivery(next_attempt_at=self.at + timedelta(hours=1))
        claimed = self.delivery()
        claim = claim_due(1)[0]
        disable_endpoint(self.user, self.endpoint)
        with patch("interop.webhooks._send") as send:
            deliver_claim(*claim)
        self.assertEqual(claim_due(1), [])
        for delivery in (queued, claimed):
            delivery.refresh_from_db()
            self.assertEqual(delivery.status, WebhookDelivery.Status.FAILED)
            self.assertEqual(delivery.attempt, 0)
            self.assertEqual(delivery.lease_token, "")
            self.assertIsNone(delivery.next_attempt_at)
        send.assert_not_called()

    def test_endpoint_is_rechecked_between_claim_and_attempt(self):
        delivery = self.delivery()
        claim = claim_due(1)[0]
        WebhookEndpoint.objects.filter(pk=self.endpoint.pk).update(is_active=False)
        with patch("interop.webhooks._send") as send:
            deliver_claim(*claim)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, WebhookDelivery.Status.FAILED)
        self.assertEqual(delivery.attempt, 0)
        send.assert_not_called()

    def test_late_completion_cannot_overwrite_a_new_lease(self):
        delivery = self.delivery()
        with patch("interop.webhooks.now", return_value=self.at):
            claim = claim_due(1)[0]
        recovered = []

        def stalled_send(*args):
            with patch("interop.webhooks.now", return_value=self.at + timedelta(seconds=LEASE_SECONDS)):
                recovered.extend(claim_due(1))
            return 204, 1, ""

        with patch("interop.webhooks.now", return_value=self.at), patch(
            "interop.webhooks._send", side_effect=stalled_send,
        ):
            deliver_claim(*claim)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, WebhookDelivery.Status.PENDING)
        self.assertEqual(delivery.lease_token, recovered[0][1])
        self.assertEqual(delivery.attempt, 0)

    def test_inflight_completion_cannot_overwrite_disabling(self):
        delivery = self.delivery()
        claim = claim_due(1)[0]

        def send(*args):
            disable_endpoint(self.user, self.endpoint)
            return 204, 1, ""

        with patch("interop.webhooks._send", side_effect=send):
            deliver_claim(*claim)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, WebhookDelivery.Status.FAILED)
        self.assertIsNone(delivery.delivered_at)

    def test_crash_after_receipt_retries_with_same_delivery_id(self):
        delivery = self.delivery()
        with patch("interop.webhooks.now", return_value=self.at):
            claim = claim_due(1)[0]
        with patch("interop.webhooks._send", return_value=(204, 1, "")), patch.object(
            QuerySet, "update", side_effect=DatabaseError("result write unavailable"),
        ):
            with self.assertRaises(DatabaseError):
                deliver_claim(*claim)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, WebhookDelivery.Status.PENDING)
        self.assertEqual(delivery.lease_token, claim[1])
        with patch("interop.webhooks.now", return_value=self.at + timedelta(seconds=LEASE_SECONDS)), patch(
            "interop.webhooks._send", return_value=(204, 1, ""),
        ) as send:
            deliver_claim(*claim_due(1)[0])
        self.assertEqual(send.call_args.args[1].public_id, delivery.public_id)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, WebhookDelivery.Status.DELIVERED)

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_process_connections_cannot_claim_the_same_row(self):
        self.delivery()
        barrier = threading.Barrier(2)
        original_update = QuerySet.update

        def update(queryset, **kwargs):
            if queryset.model is WebhookDelivery and kwargs.get("lease_token"):
                barrier.wait(timeout=10)
            return original_update(queryset, **kwargs)

        def claim():
            try:
                return claim_due(1)
            finally:
                connections.close_all()

        with patch.object(QuerySet, "update", update), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim) for _ in range(2)]
            claims = [future.result(timeout=15) for future in futures]
        self.assertEqual(sum(len(batch) for batch in claims), 1)

    def test_once_command_processes_only_a_bounded_batch(self):
        for _ in range(3):
            self.delivery()
        with patch("interop.webhooks._send", return_value=(204, 1, "")):
            call_command("deliver_webhooks", once=True, workers=1, stdout=StringIO())
        self.assertEqual(WebhookDelivery.objects.filter(status=WebhookDelivery.Status.DELIVERED).count(), 1)
        self.assertEqual(WebhookDelivery.objects.filter(status=WebhookDelivery.Status.PENDING).count(), 2)


class WebhookWorkerBoundTests(SimpleTestCase):
    def test_scheduler_closes_database_before_wait_even_after_database_error(self):
        for result in ([], DatabaseError("database unavailable")):
            worker = DeliveryWorker()
            calls = []

            def wait(interval):
                calls.append("wait")
                worker.stop()

            with patch("interop.webhooks.claim_due", side_effect=[result]), patch(
                "interop.webhooks.connections.close_all", side_effect=lambda: calls.append("close"),
            ), patch.object(worker._stop, "wait", side_effect=wait), patch(
                "interop.webhooks.logger.exception",
            ) as log:
                worker.run()
            self.assertEqual(calls[:2], ["close", "wait"])
            self.assertEqual(log.called, isinstance(result, Exception))

    def test_pool_has_no_backlog_when_all_slots_are_busy(self):
        started = threading.Event()
        release = threading.Event()
        guard = threading.Lock()
        running = []
        worker = DeliveryWorker(max_workers=2)

        def attempt(delivery_id, token):
            with guard:
                running.append(delivery_id)
                if len(running) == 2:
                    started.set()
            release.wait(timeout=10)

        with patch("interop.webhooks.claim_due", return_value=[(1, "a"), (2, "b")]) as claim, patch(
            "interop.webhooks.deliver_claim", side_effect=attempt,
        ), patch("interop.webhooks.connections.close_all") as close:
            thread = threading.Thread(target=worker.run, kwargs={"poll_interval": 0.1})
            thread.start()
            try:
                self.assertTrue(started.wait(timeout=5))
                # Polling with a full pool must never claim or submit more rows.
                threading.Event().wait(0.25)
                self.assertEqual(sorted(running), [1, 2])
                claim.assert_called_once_with(2)
                self.assertTrue(close.called)
            finally:
                worker.stop()
                release.set()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

    def test_command_refuses_unbounded_workers_or_busy_polling(self):
        for workers in (0, MAX_WORKERS + 1):
            with self.assertRaises(CommandError):
                call_command("deliver_webhooks", workers=workers)
        for interval in (0, 61, float("nan")):
            with self.assertRaises(CommandError):
                call_command("deliver_webhooks", poll_interval=interval)
