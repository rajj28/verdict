"""Start verifiable chains, explicitly identifying the unauthenticated legacy prefix."""
import hashlib
import json
from datetime import UTC

import django.db.models.deletion
from django.db import migrations, models

import audit.models
import core.clock


def backfill(apps, schema_editor):
    Entry = apps.get_model("audit", "AuditEvent")
    Head = apps.get_model("audit", "AuditChainHead")
    alias = schema_editor.connection.alias
    heads = {}
    for row in Entry.objects.using(alias).select_related("event", "actor").order_by("created_at", "id").iterator(chunk_size=500):
        key = f"event:{row.event_id}" if row.event_id is not None else "global"
        if key not in heads:
            heads[key] = Head.objects.using(alias).create(
                scope_key=key, event_slug=row.event.slug if row.event_id else "",
            )
        head = heads[key]
        row.chain_id = head.pk
        row.sequence = head.sequence + 1
        row.previous_hash = head.entry_hash
        row.actor_public_id = row.actor.public_id if row.actor_id else ""
        row.event_slug = row.event.slug if row.event_id else ""
        # Frozen v1 payload: do not import evolving runtime serializers here.
        payload = {
            "version": "verdict-audit-sha256-v1", "chain_id": head.public_id,
            "sequence": row.sequence, "previous_hash": row.previous_hash,
            "created_at": row.created_at.astimezone(UTC).isoformat(timespec="microseconds"),
            "actor_public_id": row.actor_public_id, "actor_label": row.actor_label,
            "event_slug": row.event_slug, "action": row.action,
            "target_type": row.target_type, "target_id": row.target_id,
            "summary": row.summary, "data": row.data, "ip_hash": row.ip_hash,
        }
        row.entry_hash = hashlib.sha256(json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        row.save(using=alias, update_fields=["chain", "sequence", "previous_hash", "entry_hash", "actor_public_id", "event_slug"])
        head.sequence = row.sequence
        head.entry_hash = row.entry_hash
    for head in heads.values():
        head.legacy_entries = head.sequence
        head.save(using=alias, update_fields=["sequence", "entry_hash", "legacy_entries"])


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [
        migrations.CreateModel(
            name="AuditChainHead",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.CharField(default=audit.models.new_chain_id, max_length=32, unique=True)),
                ("scope_key", models.CharField(max_length=80, unique=True)),
                ("event_slug", models.CharField(blank=True, max_length=120)),
                ("sequence", models.PositiveBigIntegerField(default=0)),
                ("entry_hash", models.CharField(default="0" * 64, max_length=64)),
                ("legacy_entries", models.PositiveBigIntegerField(default=0)),
            ], options={"db_table": "audit_chain_head"},
        ),
        migrations.AlterField(model_name="auditevent", name="event", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_events", to="events.event")),
        migrations.AlterField(model_name="auditevent", name="created_at", field=models.DateTimeField(default=core.clock.now)),
        migrations.AddField(model_name="auditevent", name="chain", field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="entries", to="audit.auditchainhead")),
        migrations.AddField(model_name="auditevent", name="sequence", field=models.PositiveBigIntegerField(default=0), preserve_default=False),
        migrations.AddField(model_name="auditevent", name="previous_hash", field=models.CharField(default="", max_length=64), preserve_default=False),
        migrations.AddField(model_name="auditevent", name="entry_hash", field=models.CharField(default="", max_length=64), preserve_default=False),
        migrations.AddField(model_name="auditevent", name="actor_public_id", field=models.CharField(blank=True, default="", max_length=32), preserve_default=False),
        migrations.AddField(model_name="auditevent", name="event_slug", field=models.CharField(blank=True, default="", max_length=120), preserve_default=False),
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AlterField(model_name="auditevent", name="chain", field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="entries", to="audit.auditchainhead")),
        migrations.AddConstraint(model_name="auditevent", constraint=models.UniqueConstraint(fields=("chain", "sequence"), name="audit_chain_sequence_uniq")),
        migrations.AddConstraint(model_name="auditevent", constraint=models.CheckConstraint(condition=models.Q(sequence__gt=0), name="audit_sequence_positive")),
    ]
