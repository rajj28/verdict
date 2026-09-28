"""The only way to write an AuditEvent. Called inside the same transaction as the change."""
import hashlib
import json
from datetime import UTC

from django.conf import settings
from django.db import models, transaction

from audit.models import AuditChainHead, AuditEvent
from core import clock
from core.errors import ApiError

GENESIS_HASH = "0" * 64
HASH_VERSION = "verdict-audit-sha256-v1"
EXPORT_LIMIT = 10_000

#: Carried by every verification response. Hash chaining is tamper evidence, not
#: tamper proofing: only a checkpoint retained outside the database can show that
#: a chain which still verifies was not rewritten as a whole.
CHECKPOINT_LIMITATION = (
    "A database owner can rewrite or delete entries and the chain head together, "
    "so an internally consistent chain - including an empty one with nothing to "
    "check - is not proof that no history ever existed. Retain a checkpoint "
    "outside the database to detect later rewriting of this chain."
)


def _scope_key(event):
    return f"event:{event.pk}" if event is not None else "global"


def canonical_entry(row, chain_id):
    """Versioned immutable payload; mutable relation IDs are deliberately absent."""
    return {
        "version": HASH_VERSION, "chain_id": chain_id,
        "sequence": row.sequence, "previous_hash": row.previous_hash,
        "created_at": row.created_at.astimezone(UTC).isoformat(timespec="microseconds"),
        "actor_public_id": row.actor_public_id, "actor_label": row.actor_label,
        "event_slug": row.event_slug, "action": row.action,
        "target_type": row.target_type, "target_id": row.target_id,
        "summary": row.summary, "data": row.data, "ip_hash": row.ip_hash,
    }


def entry_digest(payload):
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_ip(ip: str | None) -> str | None:
    """Store a pseudonymous ip, never the address itself."""
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8") + settings.SECRET_KEY.encode("utf-8")).hexdigest()[:16]


def _client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _actor_label(actor, event=None) -> str:
    """Snapshot like "Priya (participant)" so the log survives later renames."""
    if actor is None:
        return "system"
    name = actor.display_name or actor.email.split("@")[0]
    if event is not None and getattr(actor, "is_authenticated", False):
        role_row = actor.event_roles.filter(event=event).only("role").first()
        if role_row is not None:
            return f"{name} ({role_row.get_role_display().lower()})"
    return name


def _target_reference(target) -> tuple[str, str]:
    """Return (target_type, target_id) from any model instance with a public id."""
    if target is None:
        return "", ""
    if isinstance(target, models.Model):
        public_id = getattr(target, "public_id", None) or getattr(target, "slug", None)
        return target._meta.label_lower, str(public_id or "")
    return type(target).__name__, str(target)


@transaction.atomic
def record(actor, action: str, *, event=None, target=None, summary: str, data: dict | None = None,
           request=None) -> AuditEvent:
    """Append under the head lock; caller rollback also rolls back this append."""
    target_type, target_id = _target_reference(target)
    # Resolve actor labels before the head lock. Never acquire an Event row
    # lock here: callers already hold varied business-object locks.
    actor_label = _actor_label(actor, event)
    head, _created = AuditChainHead.objects.get_or_create(
        scope_key=_scope_key(event), defaults={"event_slug": event.slug if event else ""},
    )
    head = AuditChainHead.objects.select_for_update().get(pk=head.pk)
    row = AuditEvent(
        event=event,
        actor=actor if getattr(actor, "pk", None) else None,
        actor_label=actor_label,
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=summary[:300],
        data=data or {},
        ip_hash=hash_ip(_client_ip(request)),
        created_at=clock.now(), chain=head, sequence=head.sequence + 1,
        previous_hash=head.entry_hash,
        actor_public_id=getattr(actor, "public_id", "") if actor is not None else "",
        event_slug=event.slug if event is not None else "",
    )
    row.entry_hash = entry_digest(canonical_entry(row, head.public_id))
    row.save(_append=True, force_insert=True)
    head.sequence = row.sequence
    head.entry_hash = row.entry_hash
    head.save(update_fields=["sequence", "entry_hash"])
    return row


def _checkpoint(head):
    return {
        "version": HASH_VERSION,
        "chain_id": head.public_id if head else None,
        "scope": "global" if head is None or head.scope_key == "global" else "event",
        "event_slug_at_creation": head.event_slug if head else "",
        "sequence": head.sequence if head else 0,
        "head_hash": head.entry_hash if head else GENESIS_HASH,
        "legacy_entries": head.legacy_entries if head else 0,
    }


def _verify_locked(head, *, export=False, event=None):
    checkpoint = _checkpoint(head)
    if head is None and event is not None:
        checkpoint["scope"] = "event"
        checkpoint["event_slug_at_creation"] = event.slug
    previous = GENESIS_HASH
    checked = 0
    errors = []
    entries = []
    if head:
        rows = AuditEvent.objects.filter(chain=head).order_by("sequence").iterator(chunk_size=500)
        for row in rows:
            checked += 1
            payload = canonical_entry(row, head.public_id)
            if row.sequence != checked:
                errors.append(f"Sequence gap at entry {checked}.")
            if row.previous_hash != previous:
                errors.append(f"Previous hash mismatch at sequence {row.sequence}.")
            if entry_digest(payload) != row.entry_hash:
                errors.append(f"Entry hash mismatch at sequence {row.sequence}.")
            previous = row.entry_hash
            if export:
                entries.append({"payload": payload, "entry_hash": row.entry_hash})
    if checked != checkpoint["sequence"] or previous != checkpoint["head_hash"]:
        errors.append("Stored head does not match the retained entries (including possible tail deletion).")
    result = {"ok": not errors, "chain_id": checkpoint["chain_id"],
              "sequence": checkpoint["sequence"], "head_hash": checkpoint["head_hash"],
              "checked": checked, "errors": errors, "checkpoint": checkpoint,
              "retained_head_present": head is not None,
              "limitation": CHECKPOINT_LIMITATION}
    if export:
        result["entries"] = entries
    return result


@transaction.atomic
def verify_chain(event=None, *, chain_id=None):
    """Verify a consistent prefix; callers enforce organizer/admin read policy."""
    selector = {"public_id": chain_id} if chain_id else {"scope_key": _scope_key(event)}
    head = AuditChainHead.objects.select_for_update().filter(**selector).first()
    if chain_id and head is None:
        raise ApiError("audit_chain_not_found", "No such audit chain.", status_code=404)
    return _verify_locked(head, event=event)


@transaction.atomic
def export_chain(event=None, *, chain_id=None):
    """Bounded private export with a head that can be independently retained."""
    selector = {"public_id": chain_id} if chain_id else {"scope_key": _scope_key(event)}
    head = AuditChainHead.objects.select_for_update().filter(**selector).first()
    if chain_id and head is None:
        raise ApiError("audit_chain_not_found", "No such audit chain.", status_code=404)
    include_entries = head is None or head.sequence <= EXPORT_LIMIT
    result = _verify_locked(head, export=include_entries, event=event)
    result["entries_included"] = include_entries
    if not include_entries:
        result["note"] = f"More than {EXPORT_LIMIT} entries: this download contains the checkpoint only. The audit API paginates the retained entries."
    # _verify_locked already carries CHECKPOINT_LIMITATION; the export repeats
    # the same constant rather than a second wording of the same limitation.
    return result
