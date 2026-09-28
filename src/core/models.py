"""Private messages waiting for an organizer to deliver them offline."""
from django.db import models

from core.ids import new_public_id


def _new_outbox_id() -> str:
    return new_public_id("out")


class OutboxMessage(models.Model):
    public_id = models.CharField(max_length=32, unique=True, default=_new_outbox_id)
    event = models.ForeignKey(
        "events.Event", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="outbox_messages",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    recipients = models.JSONField(default=list)
    subject = models.TextField()
    body = models.TextField()
    from_email = models.EmailField(max_length=320)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["event", "created_at"], name="core_outbox_event_created")]
