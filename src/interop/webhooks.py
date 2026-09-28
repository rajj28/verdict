"""Durable webhook outbox consumed by ``manage.py deliver_webhooks``.

Delivery is at least once: a crash after receipt but before recording success can
repeat a POST. Receivers deduplicate X-Verdict-Delivery within an event; retries
retain that ID and payload, while an organizer's explicit replay gets a new ID.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, connections
from django.db.models import Q
from django.db.models.signals import post_save
from django.dispatch import receiver

from audit.models import AuditEvent
from core.clock import now
from interop.models import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger("verdict")
RETRY_SECONDS = (60, 300, 1800, 7200)
REQUEST_TIMEOUT = 5
LEASE_SECONDS = 60
MAX_WORKERS = 8


def _private_allowed() -> bool:
    return getattr(settings, "WEBHOOKS_ALLOW_PRIVATE", False) or (
        __import__("os").environ.get("WEBHOOKS_ALLOW_PRIVATE") == "1"
    )


def validate_target(url: str) -> list[tuple[int, tuple]]:
    """Reject unsafe or ambiguous destinations before storing or sending."""
    if not isinstance(url, str) or len(url) > 1000:
        raise ValueError("Webhook URL must be a valid URL no longer than 1000 characters.")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Webhook URL must use http or https and include a host.")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("Webhook URL cannot contain credentials or a fragment.")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"metadata", "metadata.google.internal", "metadata.google"}:
        raise ValueError("Webhook host resolves to a metadata service.")
    try:
        resolved = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
                                      type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"Webhook host could not be resolved: {exc}") from exc
    if not resolved:
        raise ValueError("Webhook host did not resolve to an address.")
    addresses = []
    for family, socktype, protocol, _, sockaddr in resolved:
        ip = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
        if not _private_allowed() and not ip.is_global:
            raise ValueError("Webhook destinations cannot be loopback, private, link-local, or reserved addresses.")
        addresses.append((family, socktype, protocol, sockaddr))
    return addresses


def _safe_payload(row: AuditEvent) -> dict:
    event = row.event
    return {
        "type": row.action,
        "created_at": row.created_at.isoformat(),
        "event": {"slug": event.slug, "name": event.name} if event else None,
        "target": {"type": row.target_type, "public_id": row.target_id},
        "summary": row.summary,
    }


@receiver(post_save, sender=AuditEvent, dispatch_uid="interop.audit_webhook_events")
def create_audit_deliveries(sender, instance: AuditEvent, created: bool, **kwargs) -> None:
    """Mirror exactly the audit action taxonomy without copying private audit data."""
    if not created or instance.event_id is None:
        return
    endpoints = WebhookEndpoint.objects.filter(event_id=instance.event_id, is_active=True)
    deliveries = []
    payload = _safe_payload(instance)
    for endpoint in endpoints:
        if "*" not in endpoint.event_types and instance.action not in endpoint.event_types:
            continue
        deliveries.append(WebhookDelivery(
            event=instance.event,
            endpoint=endpoint,
            event_type=instance.action,
            payload=payload,
        ))
    # Stored inside the caller's transaction: workers can only see committed rows.
    WebhookDelivery.objects.bulk_create(deliveries)


def _signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, address, **kwargs):
        self._address = address
        super().__init__(host, **kwargs)

    def connect(self):
        family, socktype, protocol, sockaddr = self._address
        sock = socket.socket(family, socktype, protocol)
        try:
            if self.timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(self.timeout)
            if self.source_address:
                sock.bind(self.source_address)
            sock.connect(sockaddr)
            if self._tunnel_host:
                self._tunnel()
        except BaseException:
            sock.close()
            raise
        self.sock = sock


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, **kwargs):
        self._address = address
        super().__init__(host, **kwargs)

    def connect(self):
        family, socktype, protocol, sockaddr = self._address
        sock = socket.socket(family, socktype, protocol)
        try:
            if self.timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(self.timeout)
            if self.source_address:
                sock.bind(self.source_address)
            sock.connect(sockaddr)
            if self._tunnel_host:
                self._tunnel()
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, address):
        super().__init__()
        self.address = address

    def http_open(self, request):
        family_address = self.address
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPConnection(host, family_address, **kwargs), request
        )


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, address):
        super().__init__()
        self.address = address

    def https_open(self, request):
        family_address = self.address
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPSConnection(host, family_address, **kwargs), request,
            context=self._context, check_hostname=self._check_hostname,
        )


def _send(endpoint: WebhookEndpoint, delivery: WebhookDelivery) -> tuple[int | None, int, str]:
    addresses = validate_target(endpoint.url)
    parsed = urllib.parse.urlsplit(endpoint.url)
    handler = _PinnedHTTPSHandler(addresses[0]) if parsed.scheme == "https" else _PinnedHTTPHandler(addresses[0])
    body = json.dumps(delivery.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint.url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Verdict-Event": delivery.event_type,
            "X-Verdict-Delivery": delivery.public_id,
            "X-Verdict-Signature": _signature(endpoint.secret, body),
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(), handler)
    started = time.monotonic()
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            status = response.status
            elapsed = int((time.monotonic() - started) * 1000)
            return status, elapsed, "" if 200 <= status < 300 else f"Receiver returned HTTP {status}."
    except urllib.error.HTTPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        code = exc.code
        exc.close()
        return code, elapsed, f"Receiver returned HTTP {code}."


def claim_due(limit: int) -> list[tuple[int, str]]:
    """Atomically claim a bounded batch, including leases abandoned by a crash.

    Each conditional UPDATE is a compare-and-set on the current lease and due
    time. It works across processes on PostgreSQL without retaining locks or
    transactions while doing network I/O.
    """
    if not 1 <= limit <= MAX_WORKERS:
        raise ValueError(f"Claim limit must be between 1 and {MAX_WORKERS}.")
    at = now()
    available = WebhookDelivery.objects.filter(
        status=WebhookDelivery.Status.PENDING,
        # Keep lease predicates on the UPDATE target itself. A joined queryset
        # update can put them inside a snapshot subquery on PostgreSQL, allowing
        # two waiting claimants to overwrite each other's lease.
        endpoint_id__in=WebhookEndpoint.objects.filter(is_active=True).values("pk"),
    ).filter(
        Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=at),
    ).filter(
        Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=at),
    )
    claims = []
    for delivery_id in list(available.order_by("created_at", "pk").values_list("pk", flat=True)[:limit]):
        token = secrets.token_hex(16)
        if available.filter(pk=delivery_id).update(
            lease_token=token, lease_expires_at=at + timedelta(seconds=LEASE_SECONDS),
        ):
            claims.append((delivery_id, token))
    return claims


def deliver_claim(delivery_id: int, token: str) -> None:
    """Attempt one owned, due delivery; persist backoff instead of sleeping.

    Disabling invalidates outstanding leases. A POST already in flight cannot be
    recalled, but its completion cannot overwrite a cancellation or a newer claim.
    """
    try:
        delivery = WebhookDelivery.objects.select_related("endpoint").filter(
            pk=delivery_id, lease_token=token, lease_expires_at__gt=now(),
            status=WebhookDelivery.Status.PENDING,
        ).first()
        if delivery is None:
            return
        owned = WebhookDelivery.objects.filter(
            pk=delivery_id, lease_token=token, status=WebhookDelivery.Status.PENDING,
        )
        if not delivery.endpoint.is_active:
            owned.update(
                status=WebhookDelivery.Status.FAILED, error="Endpoint disabled; delivery cancelled.",
                next_attempt_at=None, lease_token="", lease_expires_at=None,
            )
            return
        # close_old_connections() can retain healthy persistent connections.
        # Explicitly close this worker thread's connections before DNS/HTTP.
        connections.close_all()
        try:
            status_code, elapsed, error = _send(delivery.endpoint, delivery)
        except Exception as exc:
            status_code, elapsed, error = None, None, str(exc)[:500] or type(exc).__name__
            logger.warning("Webhook delivery %s failed: %s", delivery_id, exc)
        attempt = delivery.attempt + 1
        at = now()
        changes = dict(
            attempt=attempt, status_code=status_code, response_ms=elapsed, error=error[:500],
            lease_token="", lease_expires_at=None, next_attempt_at=None,
        )
        if not error:
            changes.update(status=WebhookDelivery.Status.DELIVERED, delivered_at=at)
        elif attempt > len(RETRY_SECONDS):
            changes["status"] = WebhookDelivery.Status.FAILED
        else:
            changes["next_attempt_at"] = at + timedelta(seconds=RETRY_SECONDS[attempt - 1])
        owned.update(**changes)
    finally:
        connections.close_all()


class DeliveryWorker:
    """One scheduler and at most MAX_WORKERS active attempts, with no task backlog."""

    def __init__(self, max_workers: int = 2):
        if not 1 <= max_workers <= MAX_WORKERS:
            raise ValueError(f"Worker count must be between 1 and {MAX_WORKERS}.")
        self.max_workers = max_workers
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _collect(futures) -> set:
        pending = set()
        for future in futures:
            if not future.done():
                pending.add(future)
                continue
            try:
                future.result()
            except Exception:
                # The stored lease expires even if the result could not be saved.
                logger.exception("Webhook attempt failed; its lease will expire for recovery.")
        return pending

    def run(self, *, once: bool = False, poll_interval: float = 1.0) -> None:
        if not 0.1 <= poll_interval <= 60:
            raise ValueError("Poll interval must be between 0.1 and 60 seconds.")
        pending = set()
        try:
            with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="verdict-webhook") as pool:
                while not self._stop.is_set():
                    pending = self._collect(pending)
                    capacity = self.max_workers - len(pending)
                    try:
                        claims = claim_due(capacity) if capacity else []
                    except DatabaseError:
                        if once:
                            raise
                        logger.exception("Webhook scheduler could not read its outbox; will retry.")
                        claims = []
                    finally:
                        connections.close_all()
                    for claim in claims:
                        pending.add(pool.submit(deliver_claim, *claim))
                    if once:
                        break
                    self._stop.wait(poll_interval)
            self._collect(pending)
        finally:
            connections.close_all()


def enqueue_test(endpoint: WebhookEndpoint) -> WebhookDelivery:
    """Create a signed test event using the same delivery machinery."""
    delivery = WebhookDelivery.objects.create(
        event=endpoint.event,
        endpoint=endpoint,
        event_type="webhook.test",
        payload={
            "type": "webhook.test",
            "event": {"slug": endpoint.event.slug, "name": endpoint.event.name},
            "summary": "Organizer-requested webhook test.",
            "created_at": now().isoformat(),
        },
    )
    return delivery


def replay_delivery(delivery: WebhookDelivery) -> WebhookDelivery:
    """Create a fresh attempt record rather than rewriting delivery history."""
    replay = WebhookDelivery.objects.create(
        event=delivery.event,
        endpoint=delivery.endpoint,
        event_type=delivery.event_type,
        payload=delivery.payload,
    )
    return replay
