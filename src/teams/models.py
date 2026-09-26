"""Teams, membership and invites. One team per person per event, enforced in the DB."""
import secrets

from django.conf import settings
from django.db import models

from core.clock import now
from core.ids import new_public_id


def _new_tm_id() -> str:
    return new_public_id("tm")
TOKEN_BYTES = 32  # secrets.token_urlsafe(32) -> 43 url-safe characters

def new_invite_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


class Team(models.Model):
    """A competing team.

    name is intentionally NOT unique: the fixture reuses names inside one event
    (StillTrail, AmberSwitch, OpenSignal). Identity is public_id.
    """

    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="teams")
    public_id = models.CharField(max_length=32, default=_new_tm_id)
    name = models.CharField(max_length=120)
    source_id = models.CharField(max_length=40, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="teams_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "teams_team"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["event", "public_id"], name="team_unique_public_id_per_event"),
        ]

    def __str__(self) -> str:
        return f"{self.event.slug}: {self.name}"

    def member_count(self) -> int:
        return self.memberships.count()

    def is_full(self) -> bool:
        return self.memberships.count() >= self.event.max_team_size


class TeamMember(models.Model):
    """Membership. event is denormalised so the one-team-per-person rule is a DB constraint."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="team_memberships")
    event = models.ForeignKey("events.Event", on_delete=models.CASCADE, related_name="memberships")
    is_owner = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "teams_team_member"
        ordering = ["joined_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["event", "user"], name="member_unique_team_per_event"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.team_id}"


class TeamInvite(models.Model):
    """A 43-char url-safe link. Rotating a link revokes the old invite."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="invites")
    token = models.CharField(max_length=64, unique=True, default=new_invite_token)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name="team_invites_created")
    expires_at = models.DateTimeField()
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    use_count = models.PositiveIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "teams_team_invite"
        ordering = ["-created_at"]

    @property
    def is_usable(self) -> bool:
        if self.revoked_at is not None:
            return False
        if self.max_uses is not None and self.use_count >= self.max_uses:
            return False
        return self.expires_at > now()
