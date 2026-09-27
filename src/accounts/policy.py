"""Users, API tokens and authentication.

Read scoping and permission predicates. Every queryset in a view starts here.
"""
from django.db.models import Q, QuerySet

from accounts.models import ApiToken, User
from events.models import EventRole, Role

# The five one-click demo sign-ins from BUILD-SPEC section 8, with what each can
# actually do. The emails are the seeded ones, so the buttons cannot drift away
# from what bootstrap creates.
DEMO_ROLE_DESCRIPTIONS = {
    "admin": "Every event, plus the user panel and password resets.",
    "organizer": "Runs both events: setup, judges, assignments, results, exports.",
    "judge_a": "Reviews a queue of fixture projects, sees only their own scores.",
    "judge_b": "A second judge, to show that judges are isolated from each other.",
    "participant": "A team that has already submitted, plus a fresh event to join.",
}


def can_manage_users(user) -> bool:
    """Only platform admins see or change user flags."""
    return bool(getattr(user, "is_authenticated", False) and user.is_admin)


def visible_users(query: str = "") -> QuerySet[User]:
    """The admin panel's user list, searchable by email or display name.

    Ordering is by email so the table is stable; the search is one filter, not a
    Python loop, so paging stays cheap.
    """
    users = User.objects.all().order_by("email")
    query = (query or "").strip()
    if query:
        users = users.filter(Q(email__icontains=query) | Q(display_name__icontains=query))
    return users


def visible_tokens(user) -> QuerySet[ApiToken]:
    """The caller's own tokens. A token row is only ever visible to its owner."""
    if not getattr(user, "is_authenticated", False):
        return ApiToken.objects.none()
    return ApiToken.objects.filter(user=user).order_by("-created_at")


def visible_roles(user) -> QuerySet[EventRole]:
    """Every event role the caller holds, with the event and judge tracks ready."""
    if not getattr(user, "is_authenticated", False):
        return EventRole.objects.none()
    return (EventRole.objects.filter(user=user)
            .select_related("event").prefetch_related("tracks").order_by("event__slug"))


def judges_anywhere(user) -> bool:
    return bool(getattr(user, "is_authenticated", False) and
                EventRole.objects.filter(user=user, role=Role.JUDGE).exists())


def demo_accounts() -> list[dict]:
    """The demo sign-in buttons: role, seeded email and what the role can do.

    Reads the authoritative role -> email list from core.bootstrap lazily: the
    seed module imports half the portal and has no business being imported by a
    template context processor at start-up.
    """
    from core.bootstrap import DEMO_TOKENS

    return [
        {
            "role": role,
            "email": email,
            "description": DEMO_ROLE_DESCRIPTIONS.get(role, ""),
        }
        for role, email, _plaintext in DEMO_TOKENS
    ]


def demo_account_for(role: str) -> User | None:
    """The seeded account behind a demo role, or None if it is not there yet."""
    from core.bootstrap import DEMO_TOKENS

    emails = {name: email for name, email, _plaintext in DEMO_TOKENS}
    email = emails.get((role or "").strip())
    if not email:
        return None
    return User.objects.filter(email=email, is_active=True).first()
