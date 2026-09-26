"""Append-only audit log. Every state change in every service lands here."""
from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """One recorded state change.

    actor_label and ip_hash are snapshots taken at write time so the record stays
    readable even if the actor is later renamed or anonymised.
    """

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, null=True, blank=True,
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "audit_audit_event"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["event", "created_at"], name="audit_event_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.summary}"
