"""Webhook deliveries and verifiable judge participation certificates."""
from django.conf import settings
from django.db import models

from core.ids import new_public_id


def _new_wh_id() -> str:
    return new_public_id("whk")


def _new_wd_id() -> str:
    return new_public_id("whd")


def _new_record_id() -> str:
    return new_public_id("rec")


class WebhookEndpoint(models.Model):
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="webhook_endpoints")
    public_id = models.CharField(max_length=32, default=_new_wh_id)
    url = models.URLField(max_length=1000)
    secret = models.CharField(max_length=128)
    event_types = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   related_name="webhook_endpoints_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "interop_webhook_endpoint"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["event", "public_id"], name="interop_wh_unique_public_id"),
        ]

    def __str__(self) -> str:
        return f"{self.public_id} ({self.url})"


class WebhookDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="webhook_deliveries")
    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries")
    public_id = models.CharField(max_length=32, default=_new_wd_id)
    event_type = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    attempt = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    response_ms = models.PositiveIntegerField(null=True, blank=True)
    error = models.CharField(max_length=500, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "interop_webhook_delivery"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["event", "public_id"], name="interop_wd_unique_public_id"),
        ]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="interop_wd_retry_idx")]

    def __str__(self) -> str:
        return f"{self.event_type} -> {self.endpoint_id} ({self.status})"


class JudgeParticipationRecord(models.Model):
    """An immutable signed record; revocation is additive and does not erase it."""

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="judge_records")
    judge = models.ForeignKey("events.EventRole", on_delete=models.PROTECT, related_name="participation_records")
    record_id = models.CharField(max_length=32, default=_new_record_id)
    kid = models.CharField(max_length=32)
    record = models.JSONField()
    signature = models.CharField(max_length=256)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                  related_name="judge_records_issued")
    issued_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="judge_records_revoked")

    class Meta:
        db_table = "interop_judge_participation_record"
        ordering = ["-issued_at"]
        constraints = [
            models.UniqueConstraint(fields=["event", "record_id"], name="interop_record_id_per_event"),
            models.UniqueConstraint(fields=["event", "judge"], name="interop_record_one_per_judge_event"),
        ]

    def __str__(self) -> str:
        return self.record_id

    @property
    def public_id(self) -> str:
        return self.record_id
