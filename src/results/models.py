"""Published results. The engine that fills rows lives in results/engine.py (P2-EN)."""
from django.conf import settings
from django.db import models

from core.ids import new_public_id


def _new_pub_id() -> str:
    return new_public_id("pub")


class ResultPublication(models.Model):
    """An immutable decision record: inputs, digest, rows and the engine parameters.

    inputs + input_digest make a publication verifiable later without trusting
    that the live data has not moved (BUILD-SEC section 16).
    feedback_released_at: when not None, team members can read their own feedback.
    """

    event = models.ForeignKey(
        "events.Event",
        on_delete=models.CASCADE,
        related_name="result_publications",
    )
    public_id = models.CharField(max_length=32, default=_new_pub_id)
    version = models.PositiveIntegerField(default=1)
    project_snapshot_backfilled = models.BooleanField(default=False)
    method = models.CharField(max_length=16)
    params = models.JSONField(default=dict, blank=True)
    inputs = models.JSONField(default=dict, blank=True)
    input_digest = models.CharField(max_length=64, blank=True, default="")
    rows = models.JSONField(default=list)
    judge_rows = models.JSONField(default=list, blank=True)
    awards = models.JSONField(
        default=list,
        blank=True,
        help_text="Prize awards as decided at publication time (results.prizes).",
    )
    unawarded = models.JSONField(
        default=list,
        blank=True,
        help_text="Prizes that could not be awarded, with the reason (ties need an organizer).",
    )
    note = models.CharField(max_length=300, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="publications",
    )
    published_at = models.DateTimeField(auto_now_add=True)
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="superseded_by",
    )
    feedback_released_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When set, team members can see their project's de-attributed feedback.",
    )
    feedback_released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedback_releases",
    )

    class Meta:
        db_table = "results_result_publication"
        ordering = ["-published_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "public_id"],
                name="publication_unique_public_id_per_event",
            ),
            models.UniqueConstraint(
                fields=["event", "version"],
                name="publication_unique_version_per_event",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.public_id} ({self.method})"
