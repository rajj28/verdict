"""Users, API tokens and authentication.

Server-rendered, read-only views. Every write goes through the JSON API: the
forms in these templates carry data-api-method and are handled by
static/js/api-forms.js. Nothing here decides permission on its own either; it
asks accounts.policy and renders a 403.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache

from accounts.policy import can_manage_users, demo_accounts, visible_roles, visible_tokens, visible_users
from core.clock import now
from events.policy import visible_events
from judging.models import Assignment, ReviewStatus
from projects.models import Project
from teams.models import TeamMember

User = get_user_model()


def _require_login(request):
    if not request.user.is_authenticated:
        return redirect(f"/login?next={request.path}")
    return None


@never_cache
def login_page(request):
    """Email and password. ``next`` is validated again before it is used."""
    if request.user.is_authenticated:
        return redirect("/me")
    return render(request, "accounts/login.html", {
        "next": request.GET.get("next", ""),
        "reset_done": request.GET.get("reset") == "1",
        "demo_accounts": demo_accounts() if settings.DEMO_MODE else [],
    })


@never_cache
def register_page(request):
    if request.user.is_authenticated:
        return redirect("/me")
    return render(request, "accounts/register.html", {"next": request.GET.get("next", "")})


@never_cache
def reset_page(request):
    """The offline reset target: /reset?token=… . Reading the page changes nothing."""
    return render(request, "accounts/reset.html", {
        "token": (request.GET.get("token") or "").strip(),
    })


@never_cache
def dashboard(request):
    """/me: one card per event the caller has a role in."""
    guard = _require_login(request)
    if guard is not None:
        return guard
    user = request.user
    roles = list(visible_roles(user))
    # The nav needs the same rows; handing them over keeps the page at one query
    # for roles instead of two.
    request.portal_roles = roles
    event_ids = [role.event_id for role in roles]

    memberships = {
        row.event_id: row
        for row in TeamMember.objects.filter(user=user, event_id__in=event_ids).select_related("team")
    }
    projects = {}
    if memberships:
        projects = {
            project.team_id: project
            for project in Project.objects.filter(
                team_id__in=[row.team_id for row in memberships.values()]
            ).select_related("track")
        }
    progress = {
        row["event_id"]: row
        for row in Assignment.objects.filter(judge__user=user, event_id__in=event_ids)
        .values("event_id")
        .annotate(
            assigned=Count("id"),
            submitted=Count("id", filter=Q(review__status=ReviewStatus.SUBMITTED)),
        )
    }
    for row in progress.values():
        row["remaining"] = row["assigned"] - row["submitted"]

    cards = []
    for role in roles:
        membership = memberships.get(role.event_id)
        cards.append({
            "role": role,
            "event": role.event,
            "membership": membership,
            "project": projects.get(membership.team_id) if membership else None,
            "progress": progress.get(role.event_id),
        })

    return render(request, "accounts/me.html", {
        "cards": cards,
        "has_roles": bool(cards) or user.is_admin,
        "is_admin": user.is_admin,
        "password_usable": user.has_usable_password(),
    })


@never_cache
def tokens_page(request):
    """/me/tokens: create, copy and revoke. A secret is shown exactly once."""
    guard = _require_login(request)
    if guard is not None:
        return guard
    tokens = list(visible_tokens(request.user))
    return render(request, "accounts/tokens.html", {
        "tokens": tokens,
        "live_tokens": [token for token in tokens if token.revoked_at is None],
    })


@never_cache
def admin_panel(request):
    """/admin-panel/: every account and every event. Admin only, enforced here."""
    if not request.user.is_authenticated:
        return redirect(f"/login?next={request.path}")
    if not can_manage_users(request.user):
        return render(request, "errors/403.html", {
            "detail": "The admin panel is for platform administrators.",
        }, status=403)
    query = (request.GET.get("q") or "").strip()
    return render(request, "admin_panel/index.html", {
        "users": list(visible_users(query)),
        "query": query,
        "events": list(visible_events(request.user).order_by("-created_at")),
        "user_count": User.objects.count(),
        "now": now(),
    })
