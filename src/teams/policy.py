"""Teams, membership and invite links.

Read scoping and permission predicates. Every queryset in a view starts here.

A team list is deliberately narrow (BUILD-SEC section 3): an organizer sees the
whole event, a participant sees only the team they are in, and a judge sees no
team list at all. Membership is a DB constraint, so "the team you are in" is one
row rather than a search.
"""
from django.db.models import QuerySet

from events.policy import is_organizer
from teams.models import Team, TeamInvite, TeamMember


def team_of(user, event) -> Team | None:
    """The one team this person belongs to in this event, or None.

    UniqueConstraint(event, user) on TeamMember is what makes this a lookup
    rather than a filter that could return two rows.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    membership = TeamMember.objects.filter(event=event, user=user).only("team_id").first()
    return membership.team if membership is not None else None


def is_team_member(user, team: Team) -> bool:
    if user is None or not getattr(user, "is_authenticated", False) or team is None:
        return False
    return TeamMember.objects.filter(team=team, user=user).exists()


def is_team_owner(user, team: Team) -> bool:
    if user is None or not getattr(user, "is_authenticated", False) or team is None:
        return False
    return TeamMember.objects.filter(team=team, user=user, is_owner=True).exists()


def visible_teams(user, event) -> QuerySet[Team]:
    """Organizers see every team in the event, a participant exactly their own."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Team.objects.none()
    if is_organizer(user, event):
        return Team.objects.filter(event=event)
    team = team_of(user, event)
    return Team.objects.filter(pk=team.pk) if team is not None else Team.objects.none()


def visible_team(user, team: Team | None) -> Team | None:
    """The team if the caller may read it, else None (callers answer 404)."""
    if team is None:
        return None
    if is_organizer(user, team.event) or is_team_member(user, team):
        return team
    return None


def team_by_public_id(event, public_id: str) -> Team | None:
    """Resolve a tm_ id inside one event; ids never cross events."""
    if not public_id:
        return None
    return Team.objects.filter(event=event, public_id=public_id).first()


def team_for_read(event, public_id: str) -> Team | None:
    """One team with its members and users already loaded (no N+1 on a page)."""
    if not public_id:
        return None
    return (Team.objects.filter(event=event, public_id=public_id)
            .select_related("event").prefetch_related("memberships__user").first())


def team_members(team: Team) -> QuerySet[TeamMember]:
    """Members in join order (the model's default ordering).

    Returning the related manager's own queryset means a caller that prefetched
    "memberships__user" gets the rows and the users from that one prefetch
    instead of a query per team.
    """
    return team.memberships.all()


def active_invite(team: Team) -> TeamInvite | None:
    """The invite link currently shown on the team page, if there is one."""
    return (team.invites.filter(revoked_at__isnull=True)
            .order_by("-created_at").first())
