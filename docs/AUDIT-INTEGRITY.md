# Audit integrity

The audit log provides tamper evidence against a retained chain head. It is not
tamper-proof. An operator who can rewrite the database can rewrite entries and
the head together. Detecting that attack requires a checkpoint retained outside
the database before the attack. A newly downloaded checkpoint is not independent
evidence about the past.

## What is authenticated

Each event has its own chain; actions without an event use a separate global
chain. A head has a public `ach_` identity, the last sequence number, and the last
entry hash. The internal event database key selects a head, so renaming an event
or reusing a deleted event's slug does not join two histories.

Entries have consecutive sequences starting at 1. The first previous hash is
64 zeroes. Every entry hashes its version, chain public ID, sequence, previous
hash, UTC timestamp (six fractional digits), actor public-ID snapshot and label,
event-slug snapshot, action, target reference, summary, data and pseudonymous IP
hash. Mutable actor/event foreign keys are excluded. Deleting an actor or event
nulls those relationships while retaining authenticated identity snapshots and
history. Archived event chains are accessible by public chain ID to admins only.

Serialization is UTF-8 JSON with sorted keys, no ASCII escaping, separators
`(',', ':')`, and non-finite numbers prohibited. SHA-256 hashes those bytes.
The format identifier is `verdict-audit-sha256-v1`.

## Writes, migration and verification

`audit.services.record` creates or finds the unique head in a transaction and
locks that head with `SELECT FOR UPDATE`. Concurrent first creation uses Django's
unique `get_or_create` savepoint handling. It then inserts the entry and advances
the head. It does not acquire an Event lock, avoiding a second business-object
lock order. A caller's rollback removes its entry and head update; creating a
head in a rolled-back transaction leaves no orphan head.

Model and queryset guards reject ordinary entry edits/deletes and bulk insertion.
Mutable FK nullification remains allowed. These are application controls, not
protection from privileged SQL. There are no database triggers that interfere
with Django flush or deletion collection.

Migration `audit.0002_audit_chain` backfills entries in timestamp/ID order inside
the migration transaction, using a frozen v1 serializer. Each head records how
many entries are legacy. Hashing legacy rows cannot establish that they were
unchanged before this migration. Run schema migrations with application writes
stopped and retain a database backup; rolling back this schema discards its
integrity metadata.

`verify_chain(event)` locks the head and streams the ordered entries, checking
each sequence, previous hash, recomputed entry hash, total count and final head.
This detects a changed entry, broken link, missing middle entry and deletion of
the last entry while the head remains intact. It uses a fixed number of queries,
not one query per row. It does not establish that every real-world action was
logged, authenticate events before the legacy boundary, or prove actor intent.

## Private API and saved checkpoints

Event organizers and admins can use:

- `GET /api/v1/events/{slug}/audit/verify`
- `GET /api/v1/events/{slug}/audit/checkpoint`
- `/manage/{slug}/audit`, which shows verification and a download link.

Only admins can access `GET /api/v1/admin/audit/verify` and `/checkpoint` for the
global chain, or `/api/v1/admin/audit/{chain_id}/verify` and `/checkpoint` for an
archived chain. This does not expose a global log to event organizers.

Downloads have `Cache-Control: no-store` and contain a checkpoint plus canonical
entry payloads and hashes. Entry export is limited to 10,000 rows. Larger chains
still export the checkpoint with `entries_included: false`; verification streams
the rows and the regular audit API remains paginated. Save checkpoints securely:
the canonical payload includes organizer-private audit data and identity snapshots.

For a download with `entries_included: true`, an independent Python verifier
requires only the standard library:

```python
import hashlib
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    document = json.load(handle)
assert document["entries_included"], "This checkpoint omits the entry payloads"
checkpoint = document["checkpoint"]
previous = "0" * 64
for sequence, entry in enumerate(document["entries"], 1):
    payload = entry["payload"]
    assert payload["version"] == "verdict-audit-sha256-v1"
    assert payload["chain_id"] == checkpoint["chain_id"]
    assert payload["sequence"] == sequence
    assert payload["previous_hash"] == previous
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == entry["entry_hash"]
    previous = entry["entry_hash"]
assert len(document["entries"]) == checkpoint["sequence"]
assert previous == checkpoint["head_hash"]
print("Downloaded prefix verifies against its checkpoint")
```

To check an independently saved older checkpoint against a later full export,
match the chain ID and the entry hash at the older sequence to its saved head
hash. A later export shorter than the saved sequence, with another chain ID, or
with a different hash at that sequence contradicts the saved checkpoint. The
application does not upload or compare external checkpoint files yet.

## Evidence

`tests.test_audit_chain` covers independent digest reproduction, policy, append
guards, raw-SQL edits and deletions, identity retention, rollback, bounded export,
fixed verification query count, legacy backfill, and simultaneous first appends.

The simultaneous-first-append test exercises `SELECT FOR UPDATE` head locking and
only produces locking evidence when it runs against PostgreSQL, which is the only
supported backend. PostgreSQL is therefore required to run this test module: it
is not skippable and there is no supported fallback database. A run that skips the
concurrency test, or that does not report the backend, establishes nothing about
lock ordering. Record the backend and the test count with any evidence from it.
