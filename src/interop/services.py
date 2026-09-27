"""Interop writes and their permission, privacy, and lifecycle rules."""
from __future__ import annotations

import secrets

from django.db import transaction
from django.db.models import Count, Q

from accounts.models import User
import audit.services
from core.clock import now
from core.errors import ApiError
from events.models import Event, Role
from events.policy import is_organizer
from interop.models import JudgeParticipationRecord, WebhookDelivery, WebhookEndpoint
from interop.signing import sign_record
from interop.webhooks import enqueue_test, replay_delivery, validate_target
from judging.models import ReviewStatus


def _require_organizer(actor: User, event: Event) -> None:
    if not is_organizer(actor, event):
        raise ApiError("forbidden", "Only an organizer of this event can manage interop features.",
                       status_code=403)


def create_endpoint(actor: User, event: Event, *, url: str,
                    event_types: list[str]) -> tuple[WebhookEndpoint, str]:
    _require_organizer(actor, event)
    if not isinstance(event_types, list) or not event_types or not all(
        isinstance(item, str) and item and len(item) <= 80 for item in event_types
    ):
        raise ApiError("invalid_event_types", "event_types must be a non-empty list of event names or ['*'].",
                       status_code=400, fields={"event_types": "Enter event names or ['*']."})
    try:
        validate_target(url)
    except ValueError as exc:
        raise ApiError("unsafe_webhook_url", str(exc), status_code=400,
                       fields={"url": str(exc)}) from exc
    secret = secrets.token_urlsafe(32)
    with transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        _require_organizer(actor, locked_event)
        endpoint = WebhookEndpoint.objects.create(
            event=locked_event, url=url, secret=secret, event_types=event_types, created_by=actor,
        )
        audit.services.record(actor, "webhook.endpoint_created", event=locked_event, target=endpoint,
                              summary=f"Webhook endpoint {endpoint.public_id} created.")
    return endpoint, secret


def test_endpoint(actor: User, endpoint: WebhookEndpoint) -> WebhookDelivery:
    with transaction.atomic():
        endpoint = WebhookEndpoint.objects.select_for_update().select_related("event").get(pk=endpoint.pk)
        _require_organizer(actor, endpoint.event)
        if not endpoint.is_active:
            raise ApiError("endpoint_disabled", "A disabled endpoint cannot be tested.", status_code=409)
        try:
            validate_target(endpoint.url)
        except ValueError as exc:
            raise ApiError("unsafe_webhook_url", str(exc), status_code=400,
                           fields={"url": str(exc)}) from exc
        delivery = enqueue_test(endpoint)
        audit.services.record(actor, "webhook.test_requested", event=endpoint.event, target=endpoint,
                              summary=f"Tested webhook endpoint {endpoint.public_id}.")
    return delivery


def disable_endpoint(actor: User, endpoint: WebhookEndpoint) -> WebhookEndpoint:
    with transaction.atomic():
        endpoint = WebhookEndpoint.objects.select_for_update().select_related("event").get(pk=endpoint.pk)
        _require_organizer(actor, endpoint.event)
        if endpoint.is_active:
            endpoint.is_active = False
            endpoint.save(update_fields=["is_active"])
            audit.services.record(actor, "webhook.endpoint_disabled", event=endpoint.event, target=endpoint,
                                  summary=f"Disabled webhook endpoint {endpoint.public_id}.")
    return endpoint


def replay(actor: User, delivery: WebhookDelivery) -> WebhookDelivery:
    _require_organizer(actor, delivery.event)
    with transaction.atomic():
        locked = WebhookDelivery.objects.select_for_update().select_related("event", "endpoint").get(pk=delivery.pk)
        _require_organizer(actor, locked.event)
        if not locked.endpoint.is_active:
            raise ApiError("endpoint_disabled", "A disabled endpoint cannot receive a replay.", status_code=409)
        try:
            validate_target(locked.endpoint.url)
        except ValueError as exc:
            raise ApiError("unsafe_webhook_url", str(exc), status_code=400,
                           fields={"url": str(exc)}) from exc
        result = replay_delivery(locked)
        audit.services.record(actor, "webhook.delivery_replayed", event=locked.event, target=result,
                              summary=f"Replayed webhook delivery {locked.public_id}.")
    return result


def certificate_access(user: User | None, event: Event, kind: str, data: dict) -> bool:
    """Certificates belong to named team members/judges or event organizers."""
    if is_organizer(user, event):
        return True
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return user.pk in data["owner_ids"]


def issue_judge_records(actor: User, event: Event) -> list[JudgeParticipationRecord]:
    _require_organizer(actor, event)
    with transaction.atomic():
        locked_event = Event.objects.select_for_update().get(pk=event.pk)
        _require_organizer(actor, locked_event)
        at = now()
        if locked_event.judging_close_at is None or at < locked_event.judging_close_at:
            raise ApiError("judging_open", "Judge records can be issued only after judging closes.",
                           status_code=403)
        issued = []
        roles = list(
            locked_event.roles.filter(role=Role.JUDGE).select_related("user").prefetch_related("tracks")
            .annotate(review_count=Count(
                "reviews",
                filter=Q(reviews__event=locked_event, reviews__status=ReviewStatus.SUBMITTED),
            ))
        )
        existing = {
            row.judge_id: row for row in JudgeParticipationRecord.objects.filter(event=locked_event)
        }
        for role in roles:
            count = role.review_count
            if count < 1:
                continue
            prior = existing.get(role.pk)
            if prior:
                issued.append(prior)
                continue
            record_row = JudgeParticipationRecord(
                event=locked_event, judge=role, issued_by=actor,
            )
            tracks = sorted(track.name for track in role.tracks.all())
            document = {
                "kid": "",
                "record_id": record_row.record_id,
                "event": {"slug": locked_event.slug, "name": locked_event.name},
                "judge": {
                    "display_name": role.user.display_name or "Judge",
                    "public_id": role.public_id,
                },
                "reviews_submitted": count,
                "tracks": tracks,
                "issued_at": at.isoformat(),
            }
            kid, signature = sign_record(document)
            record_row.kid = kid
            record_row.record = document
            record_row.signature = signature
            record_row.save()
            audit.services.record(actor, "judge.record_issued", event=locked_event, target=record_row,
                                  summary=f"Issued signed judge participation record {record_row.record_id}.")
            issued.append(record_row)
    return issued


def revoke_judge_record(actor: User, record: JudgeParticipationRecord) -> JudgeParticipationRecord:
    with transaction.atomic():
        locked = JudgeParticipationRecord.objects.select_for_update().select_related("event").get(pk=record.pk)
        _require_organizer(actor, locked.event)
        if locked.revoked_at is None:
            locked.revoked_at = now()
            locked.revoked_by = actor
            locked.save(update_fields=["revoked_at", "revoked_by"])
            audit.services.record(actor, "judge.record_revoked", event=locked.event, target=locked,
                                  summary=f"Revoked judge participation record {locked.record_id}.")
    return locked


def verify_submission(document: object) -> tuple[bool, str]:
    from interop.signing import public_key_document, verify_record_signature

    if not isinstance(document, dict) or not isinstance(document.get("record"), dict):
        return False, "Expected an object containing record and signature."
    record = document["record"]
    signature = document.get("signature")
    if not isinstance(signature, str):
        return False, "Signature is missing."
    if not all(isinstance(record.get(key), dict if key in {"event", "judge"} else str)
               for key in ("kid", "record_id", "event", "judge", "issued_at")):
        return False, "Record is missing required fields."
    keys = public_key_document()
    if any(item["record_id"] == record.get("record_id") for item in keys["revocations"]):
        return False, "This record has been revoked."
    key = next((item for item in keys["keys"] if item["kid"] == record.get("kid")), None)
    if key is None:
        return False, "Signing key is unknown."
    try:
        valid = verify_record_signature(record, signature, key["public_key"])
    except (ValueError, TypeError):
        return False, "Record or signature encoding is invalid."
    return (True, "Signature is valid.") if valid else (False, "Signature does not match the record.")
