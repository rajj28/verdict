"""Template context every page needs (brand, demo mode, current user role).

Role-aware navigation is computed here, once, so no template has to ask the
database a question. An anonymous visitor costs zero extra queries, which is what
keeps the public gallery at a constant three.
"""
from django.conf import settings

from accounts.policy import visible_roles
from events.models import Role


def portal(request):
    context = {
        "DEMO_MODE": settings.DEMO_MODE,
        "PORTAL_NAME": "VERDICT",
        "portal_roles": [],
        "organized_events": [],
        "is_judge_anywhere": False,
        "show_admin_panel": False,
    }
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return context
    # A view that already listed the caller's roles (the dashboard does) puts
    # them on the request, so the nav costs no second query.
    roles = getattr(request, "portal_roles", None)
    if roles is None:
        roles = list(visible_roles(user))
    request.portal_roles = roles
    context["portal_roles"] = roles
    context["organized_events"] = [role.event for role in roles if role.role == Role.ORGANIZER]
    context["is_judge_anywhere"] = any(role.role == Role.JUDGE for role in roles)
    context["show_admin_panel"] = user.is_admin
    return context
