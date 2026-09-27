"""Community voting, ballots, comments and abuse signals."""
import secrets

from django.conf import settings
from django.db import models

from core.ids import new_public_id


def _new_link_token() -> str:
    return secrets.token_urlsafe(32)


def _voter_id() -> str:
    return new_public_id("vtr")


def _ballot_id() -> str:
    return new_public_id("bal")


def _comment_id() -> str:
    return new_public_id("cmt")


def _flag_id() -> str:
    return new_public_id("abf")


def _order_seed() -> str:
    return secrets.token_hex(24)


class VotingAccess(models.TextChoices):
    OPEN_LINK = "open_link", "Open link"
    EMAIL = "email", "Email verification"
    AUTHENTICATED = "authenticated", "Authenticated account"


class VotingStyle(models.TextChoices):
    SINGLE = "single", "Single vote"
    QUADRATIC = "quadratic", "Quadratic"


class VoterKind(models.TextChoices):
    USER = "user", "User"
    EMAIL = "email", "Email"
    DEVICE = "device", "Device"


class VotingConfig(models.Model):
    event = models.OneToOneField(
        "events.Event", on_delete=models.CASCADE, related_name="voting_config"
    )
    access = models.CharField(max_length=16, choices=VotingAccess.choices,
                              default=VotingAccess.OPEN_LINK)
    style = models.CharField(max_length=16, choices=VotingStyle.choices,
                             default=VotingStyle.SINGLE)
    credits = models.PositiveIntegerField(default=16)
    max_votes_per_project = models.PositiveIntegerField(default=1)
    link_token = models.CharField(max_length=64, default=_new_link_token, unique=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(credits__gte=1),
                                   name="community_voting_credits_positive"),
            models.CheckConstraint(condition=models.Q(max_votes_per_project__gte=1),
                                   name="community_max_votes_positive"),
            models.CheckConstraint(
                condition=models.Q(style=VotingStyle.QUADRATIC)
                | models.Q(max_votes_per_project=1),
                name="community_single_vote_limit_is_one",
            ),
        ]


class Voter(models.Model):
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="voters")
    public_id = models.CharField(max_length=32, default=_voter_id, unique=True)
    kind = models.CharField(max_length=8, choices=VoterKind.choices)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
        related_name="community_voters",
    )
    email_hash = models.CharField(max_length=64, blank=True)
    device_hash = models.CharField(max_length=64, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    ip_hash = models.CharField(max_length=16, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "user"], condition=models.Q(user__isnull=False),
                                    name="community_voter_event_user_uniq"),
            models.UniqueConstraint(fields=["event", "email_hash"],
                                    condition=~models.Q(email_hash=""),
                                    name="community_voter_event_email_uniq"),
            models.UniqueConstraint(fields=["event", "device_hash"],
                                    condition=~models.Q(device_hash=""),
                                    name="community_voter_event_device_uniq"),
        ]
        indexes = [models.Index(fields=["event", "ip_hash", "created_at"],
                                name="community_voter_ip_time_idx")]


class Ballot(models.Model):
    voter = models.OneToOneField(Voter, on_delete=models.CASCADE, related_name="ballot")
    public_id = models.CharField(max_length=32, default=_ballot_id, unique=True)
    order_seed = models.CharField(max_length=64, default=_order_seed)
    submitted_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class BallotItem(models.Model):
    ballot = models.ForeignKey(Ballot, on_delete=models.CASCADE, related_name="items")
    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT,
                                related_name="community_ballot_items")
    votes = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["ballot", "project"],
                                    name="community_ballot_project_uniq"),
            models.CheckConstraint(condition=models.Q(votes__gte=0),
                                   name="community_ballot_votes_nonnegative"),
        ]


class Comment(models.Model):
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE,
                                related_name="community_comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                               related_name="community_comments")
    public_id = models.CharField(max_length=32, default=_comment_id, unique=True)
    body = models.TextField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)
    hidden_at = models.DateTimeField(null=True, blank=True)
    hidden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="community_comments_hidden",
    )
    hide_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["project", "hidden_at", "created_at"],
                                name="community_comment_visible_idx")]


class AbuseFlag(models.Model):
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="abuse_flags")
    public_id = models.CharField(max_length=32, default=_flag_id, unique=True)
    kind = models.CharField(max_length=32)
    subject = models.CharField(max_length=64)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
