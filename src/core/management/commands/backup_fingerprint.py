"""``manage.py backup_fingerprint``: a deterministic, secret-free proof of portal state.

The output is the JSON that ``scripts/backup.py`` stores in ``manifest.json`` and
compares after a restore. It answers one question: is the restored database the
same database? It contains

* ``rows``: one count per concrete model, keyed by ``app_label.model_name`` so the
  keys stay stable when models move between apps.
* ``audit``: the latest audit entry (sequence and entry hash) plus every chain
  head, so a rewritten or truncated chain cannot pass unnoticed.
* ``publications``: every result publication's public id, version and stored input
  digest, so the restore can re-verify each decision record.

There is deliberately no timestamp and no ordering by insertion: the same portal
state must produce byte-identical output, or the restore comparison is useless.
Each count is an independent read, so take a backup while the portal is idle.

Read-only by construction: it selects counts and public identifiers, never
secrets. No password hashes, token hashes, email addresses or key material are
read, so the fingerprint is safe to keep next to the backup it describes.
"""

import json

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db.models import Count

from audit.models import AuditChainHead, AuditEvent
from audit.services import GENESIS_HASH
from results.models import ResultPublication

FINGERPRINT_VERSION = 1


def row_counts() -> dict[str, int]:
    """One count per concrete model, including Django's own tables.

    The dump carries every table in the database, so a proof that skipped
    ``sessions`` or ``auth_permission`` would be a weaker proof than the restore
    it checks. Through-table models are excluded: they are not tables of their own.
    """
    counts: dict[str, int] = {}
    for model in apps.get_models(include_auto_created=False):
        if model._meta.proxy:
            continue
        counts[model._meta.label_lower] = model.objects.count()
    return dict(sorted(counts.items()))


def audit_state() -> dict:
    """The latest audit entry plus every chain head.

    "Latest" is the newest ``created_at``, with sequence and primary key as
    tie-breakers so two fingerprints of the same state always agree.
    """
    latest = AuditEvent.objects.order_by("-created_at", "-sequence", "-pk").first()
    return {
        "entries": AuditEvent.objects.aggregate(total=Count("pk"))["total"],
        "head_chain_id": latest.chain.public_id if latest is not None else None,
        "head_hash": latest.entry_hash if latest is not None else GENESIS_HASH,
        "head_sequence": latest.sequence if latest is not None else 0,
        "chains": [
            {
                "chain_id": head.public_id,
                "event_slug": head.event_slug,
                "head_hash": head.entry_hash,
                "sequence": head.sequence,
            }
            for head in AuditChainHead.objects.order_by("public_id")
        ],
    }


def publications() -> list[dict]:
    """Public id, version and stored input digest of every published result set.

    The digest is what makes a decision record checkable later without trusting
    that the live inputs still match, so it belongs in the backup's proof.
    """
    return [
        {
            "event": row["event__slug"],
            "input_digest": row["input_digest"],
            "public_id": row["public_id"],
            "version": row["version"],
        }
        for row in ResultPublication.objects.order_by("event__slug", "version").values(
            "public_id", "version", "input_digest", "event__slug"
        )
    ]


def fingerprint() -> dict:
    """Build the whole fingerprint. Pure read; the caller decides how to print it."""
    rows = row_counts()
    return {
        "audit": audit_state(),
        "fingerprint_version": FINGERPRINT_VERSION,
        "publications": publications(),
        "rows": rows,
        "rows_total": sum(rows.values()),
    }


class Command(BaseCommand):
    help = "Print a deterministic JSON fingerprint of the portal (rows, audit head, publications)."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(fingerprint(), sort_keys=True, indent=2, ensure_ascii=False))
