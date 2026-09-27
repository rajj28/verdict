"""Teams, membership and invite links.

Server-rendered, read-only views. Every write goes through the JSON API: the
create-team form and the invite, leave and remove controls are all data-api-*
attributes handled by static/js/api-forms.js.
"""
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode

from events.models import Event
from events.policy import (is_organizer, role_of, submission_window_open, visible_events)
from projects.models import ACTIVE_STATUSES
from teams import services
from teams.models import TeamInvite
from teams.policy import (active_invite, team_for_read, team_members, team_of, visible_team,
                          visible_teams)


def _login_redirect(request):
    return redirect(f"/login?{urlencode({'next': request.get_full_path()})}")


def _active_project(team):
    return team.projects.filter(status__in=ACTIVE_STATUSES).order_by("-id").first()


def _event(slug: str) -> Event:
    return get_object_or_404(visible_events(), slug=slug)


def team_page(request, slug: str):
    """/events/{slug}/team: your team, its invite link and its project."""
    if not request.user.is_authenticated:
        return _login_redirect(request)
    event = _event(slug)
    role = role_of(request.user, event)
    team = team_of(request.user, event)
    other_teams = []
    if is_organizer(request.user, event):
        # An organizer runs the event rather than competing in it, so they pick a
        # team to look at instead of owning one.
        requested = (request.GET.get("team") or "").strip()
        if requested:
            team = visible_team(request.user, team_for_read(event, requested))
    if team is not None:
        # Reload with members and their users in one prefetch: the page shows a
        # row per member, and a query per member would be the classic N+1.
        team = team_for_read(event, team.public_id)
    if is_organizer(request.user, event):
        other_teams = list(visible_teams(request.user, event)
                           .prefetch_related("memberships").order_by("name", "id")[:50])

    members = list(team_members(team)) if team is not None else []
    invite = active_invite(team) if team is not None else None
    return render(request, "teams/team.html", {
        "event": event,
        "role": role,
        "team": team,
        "members": members,
        "is_owner": any(member.user_id == request.user.pk and member.is_owner for member in members),
        "invite": invite,
        "invite_url": services.invite_link(invite) if invite is not None else None,
        "project": _active_project(team) if team is not None else None,
        "window_open": submission_window_open(event),
        "other_teams": other_teams,
        "can_register": role is None,
    })


def invite_page(request):
    """/invite?token=…: what the link offers, and the one button that takes it.

    The token arrives in the query string (BUILD-SEC section 16) so it never
    becomes part of a path in the access log.
    """
    token = (request.GET.get("token") or "").strip()
    invite = (TeamInvite.objects.filter(token=token).select_related("team__event").first()
              if token else None)
    team = invite.team if invite is not None else None
    if invite is None:
        problem = "That invite link is not valid. Ask the team for a new one."
    else:
        problem = services.invite_problem(invite)
    size = team.event.max_team_size if team is not None else 0
    full = team.memberships.count() if team is not None else 0
    if problem is None and team is not None and full >= size:
        problem = f"{team.name} already has its {size} members."
    signed_in = request.user.is_authenticated
    return render(request, "teams/invite.html", {
        "token": token,
        "team": team,
        "event": team.event if team is not None else None,
        "problem": problem,
        "full": full,
        "size": size,
        "signed_in": signed_in,
        "already_in": bool(signed_in and team is not None
                           and team_of(request.user, team.event) is not None),
        "login_url": f"/login?{urlencode({'next': request.get_full_path()})}",
        "register_url": f"/register?{urlencode({'next': request.get_full_path()})}",
        "team_url": reverse("team-page", args=[team.event.slug]) if team is not None else "",
    })
