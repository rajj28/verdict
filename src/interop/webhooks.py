"""Audit-driven webhook delivery with asynchronous, bounded retries."""
from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from audit.models import AuditEvent
from core.clock import now
from interop.models import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger("verdict")
RETRY_SECONDS = (60, 300, 1800, 7200)
REQUEST_TIMEOUT = 5


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
    created_rows = WebhookDelivery.objects.bulk_create(deliveries)
    for delivery in created_rows:
        transaction.on_commit(lambda delivery_id=delivery.pk: start_delivery(delivery_id))


def start_delivery(delivery_id: int) -> None:
    """Start delivery without making the caller wait for network I/O."""
    try:
        threading.Thread(
            target=_delivery_loop,
            args=(delivery_id,),
            name=f"verdict-webhook-{delivery_id}",
            daemon=True,
        ).start()
    except RuntimeError:
        logger.exception("Could not start webhook delivery worker for delivery %s", delivery_id)


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


def _delivery_loop(delivery_id: int) -> None:
    close_old_connections()
    try:
        delivery = WebhookDelivery.objects.select_related("endpoint").filter(pk=delivery_id).first()
        if delivery is None or delivery.status != WebhookDelivery.Status.PENDING:
            return
        if delivery.next_attempt_at is not None:
            delay = max(0, (delivery.next_attempt_at - now()).total_seconds())
            if delay:
                time.sleep(delay)
        status_code, elapsed, error = _send(delivery.endpoint, delivery)
    except Exception as exc:
        status_code, elapsed, error = None, None, str(exc)[:500]
        logger.warning("Webhook delivery %s failed: %s", delivery_id, exc)
    try:
        delivery = WebhookDelivery.objects.select_related("endpoint").get(pk=delivery_id)
        delivery.attempt += 1
        delivery.status_code = status_code
        delivery.response_ms = elapsed
        delivery.error = error
        if not error:
            delivery.status = WebhookDelivery.Status.DELIVERED
            delivery.delivered_at = now()
            delivery.next_attempt_at = None
        elif delivery.attempt > len(RETRY_SECONDS):
            delivery.status = WebhookDelivery.Status.FAILED
            delivery.next_attempt_at = None
        else:
            wait = RETRY_SECONDS[delivery.attempt - 1]
            delivery.next_attempt_at = now() + timedelta(seconds=wait)
        delivery.save(update_fields=[
            "attempt", "status_code", "response_ms", "error", "status", "delivered_at", "next_attempt_at",
        ])
        if delivery.status == WebhookDelivery.Status.PENDING:
            start_delivery(delivery.pk)
    finally:
        close_old_connections()


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
    transaction.on_commit(lambda: start_delivery(delivery.pk))
    return delivery


def replay_delivery(delivery: WebhookDelivery) -> WebhookDelivery:
    """Create a fresh attempt record rather than rewriting delivery history."""
    replay = WebhookDelivery.objects.create(
        event=delivery.event,
        endpoint=delivery.endpoint,
        event_type=delivery.event_type,
        payload=delivery.payload,
    )
    transaction.on_commit(lambda: start_delivery(replay.pk))
    return replay
