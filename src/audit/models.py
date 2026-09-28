"""Append-only audit log. Every state change in every service lands here."""
from django.conf import settings
from django.db import models

from core import clock
from core.ids import new_public_id


def new_chain_id():
    return new_public_id("ach")


class AuditChainHead(models.Model):
    """Serialized append position; independent of mutable event/actor FKs."""

    public_id = models.CharField(max_length=32, unique=True, default=new_chain_id)
    scope_key = models.CharField(max_length=80, unique=True)
    event_slug = models.CharField(max_length=120, blank=True)
    sequence = models.PositiveBigIntegerField(default=0)
    entry_hash = models.CharField(max_length=64, default="0" * 64)
    legacy_entries = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "audit_chain_head"


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        # Django's deletion collector may null mutable relationships. The
        # immutable identity snapshots, not these FKs, are authenticated.
        if kwargs and set(kwargs) <= {"actor", "actor_id", "event", "event_id"} and all(
            value is None for value in kwargs.values()
        ):
            return super().update(**kwargs)
        raise ValueError("Audit entries are append-only.")

    def delete(self):
        raise ValueError("Audit entries are append-only.")

    def bulk_create(self, *args, **kwargs):
        raise ValueError("Use audit.services.record to append entries.")


class AuditEvent(models.Model):
    """One recorded state change.

    actor_label and ip_hash are snapshots taken at write time so the record stays
    readable even if the actor is later renamed or anonymised.
    """

    event = models.ForeignKey("events.Event", on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="audit_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                              blank=True, related_name="audit_events")
    actor_label = models.CharField(max_length=120, blank=True)
    action = models.CharField(max_length=80, db_index=True)
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    summary = models.CharField(max_length=300)
    data = models.JSONField(default=dict, blank=True)
    ip_hash = models.CharField(max_length=32, null=True, blank=True)
    created_at = models.DateTimeField(default=clock.now)
    chain = models.ForeignKey(AuditChainHead, on_delete=models.PROTECT, related_name="entries")
    sequence = models.PositiveBigIntegerField()
    previous_hash = models.CharField(max_length=64)
    entry_hash = models.CharField(max_length=64)
    actor_public_id = models.CharField(max_length=32, blank=True)
    event_slug = models.CharField(max_length=120, blank=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        db_table = "audit_audit_event"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["event", "created_at"], name="audit_event_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["chain", "sequence"], name="audit_chain_sequence_uniq"),
            models.CheckConstraint(condition=models.Q(sequence__gt=0), name="audit_sequence_positive"),
        ]

    def save(self, *args, _append=False, **kwargs):
        if not _append or not self._state.adding:
            raise ValueError("Use audit.services.record; existing entries are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Audit entries are append-only.")

    def __str__(self) -> str:
        return f"{self.action} {self.summary}"
