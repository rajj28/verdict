"""Users, API tokens and authentication.

DRF serializers and viewsets. Thin: validate, delegate to services, return the
envelope. Every rule that decides *whether* something is allowed lives in
accounts/policy.py or accounts/services.py, never here.
"""
from django.contrib.auth import get_user_model
from django.middleware.csrf import CsrfViewMiddleware
from django.utils.http import url_has_allowed_host_and_scheme
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import ApiToken
from accounts.policy import can_manage_users, visible_roles, visible_tokens, visible_users
from accounts.services import (
    INVALID_CREDENTIALS,
    authenticate,
    change_password,
    create_api_token,
    create_reset_link,
    demo_login,
    end_session,
    register_user,
    reset_password,
    revoke_api_token,
    set_user_flags,
    start_session,
)
from accounts.throttles import LoginThrottle
from core.errors import ApiError
from core.pagination import VerdictPagination
from core.schema import error_responses

User = get_user_model()

TAGS = ["accounts"]


def require_csrf(request) -> None:
    """Enforce CSRF on the endpoints an anonymous caller can reach.

    DRF's SessionAuthentication only checks CSRF once a session exists, so an
    anonymous POST to /auth/login would otherwise be a free session-fixation and
    forced-login primitive. The meta tag in base.html guarantees the cookie is
    there, and api-forms.js sends the header.
    """
    guard = CsrfViewMiddleware(lambda request: None)
    guard.process_request(request)
    reason = guard.process_view(request, None, (), {})
    if reason is not None:
        raise ApiError("csrf_failed", "CSRF verification failed. Reload the page and try again.", 403)


def safe_next(request, candidate: str | None) -> str:
    """Only ever redirect back to this host (BUILD-SEC section 16)."""
    target = (candidate or "").strip()
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return "/me"


class IsPlatformAdmin(BasePermission):
    """403 for a signed-in non-admin; DRF still answers anonymous callers 401."""

    message = "Only platform admins can do this."

    def has_permission(self, request, view) -> bool:
        return can_manage_users(request.user)


# --- serializers ---------------------------------------------------------


class LoginSerializer(serializers.Serializer):
    email = serializers.CharField(allow_blank=True, max_length=254)
    password = serializers.CharField(allow_blank=True, trim_whitespace=False, max_length=256)
    next = serializers.CharField(required=False, allow_blank=True, max_length=500)


class RegisterSerializer(serializers.Serializer):
    email = serializers.CharField(allow_blank=True, max_length=254)
    password = serializers.CharField(allow_blank=True, trim_whitespace=False, max_length=256)
    display_name = serializers.CharField(required=False, allow_blank=True, max_length=80)
    next = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class DemoLoginSerializer(serializers.Serializer):
    role = serializers.CharField(allow_blank=True, max_length=40)


class TokenCreateSerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=True, max_length=80)


class PasswordSerializer(serializers.Serializer):
    password = serializers.CharField(allow_blank=True, trim_whitespace=False, max_length=256)


class ResetPasswordSerializer(PasswordSerializer):
    token = serializers.CharField(allow_blank=True, max_length=200)


class ChangePasswordSerializer(PasswordSerializer):
    current_password = serializers.CharField(allow_blank=True, trim_whitespace=False, max_length=256)


class UserPatchSerializer(serializers.Serializer):
    is_host = serializers.BooleanField(required=False)
    is_admin = serializers.BooleanField(required=False)
    is_active = serializers.BooleanField(required=False)


class EventRefSerializer(serializers.Serializer):
    """The two event fields a role reference carries."""

    slug = serializers.CharField()
    name = serializers.CharField()


class TrackRefSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    name = serializers.CharField()


class UserBriefSerializer(serializers.Serializer):
    """A user as anyone but themselves may see them. Never carries an email."""

    public_id = serializers.CharField()
    display_name = serializers.CharField(allow_null=True)
    is_admin = serializers.BooleanField()
    is_host = serializers.BooleanField()
    is_active = serializers.BooleanField()
    date_joined = serializers.DateTimeField(allow_null=True)


class UserSelfSerializer(UserBriefSerializer):
    """A user as they may see themselves: adds their own email address."""

    email = serializers.EmailField(help_text="Only ever returned for the caller's own account.")


class AdminUserSerializer(UserBriefSerializer):
    """Admin view of an account; adds the email the admin manages."""

    email = serializers.EmailField()


class EventRoleSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    role = serializers.CharField(help_text="participant, judge, organizer, host or admin.")
    event = EventRefSerializer()
    tracks = TrackRefSerializer(many=True)


class MeSerializer(serializers.Serializer):
    """GET /api/v1/me: the caller and the role they hold in every event."""

    user = UserSelfSerializer()
    roles = EventRoleSerializer(many=True)


class LoginResponseSerializer(serializers.Serializer):
    user = UserSelfSerializer()
    redirect_to = serializers.CharField(help_text="Safe same-host path to land on.")


class OkRedirectSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    redirect_to = serializers.CharField()


class ChangePasswordResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()


class TokenSerializer(serializers.Serializer):
    """A token row. The secret is never part of it, only the lookup prefix."""

    prefix = serializers.CharField(help_text="12-character public prefix; what DELETE takes.")
    name = serializers.CharField()
    is_demo = serializers.BooleanField(help_text="True for the shared seeded demo token.")
    created_at = serializers.DateTimeField()
    last_used_at = serializers.DateTimeField(allow_null=True)
    revoked_at = serializers.DateTimeField(allow_null=True)


class TokenRowSerializer(serializers.Serializer):
    token = TokenSerializer()


class TokenListSerializer(serializers.Serializer):
    tokens = TokenSerializer(many=True)


class TokenCreatedSerializer(serializers.Serializer):
    """The one and only time the plaintext token exists in a response."""

    token = TokenSerializer()
    plaintext = serializers.CharField(help_text="Send as 'Authorization: Bearer <plaintext>'.")


class ResetLinkSerializer(serializers.Serializer):
    user = UserBriefSerializer()
    reset_url = serializers.CharField(help_text="Relative single-use link to hand to the user.")
    expires_at = serializers.DateTimeField()


class ResetPasswordResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    user = UserBriefSerializer()
    redirect_to = serializers.CharField()


def user_brief(user: User) -> dict:
    """Never the password hash, never the email unless the caller owns the record."""
    return {
        "public_id": user.public_id,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
        "is_host": user.is_host,
        "is_active": user.is_active,
        "date_joined": user.date_joined.isoformat() if user.date_joined else None,
    }


def user_self(user: User) -> dict:
    payload = user_brief(user)
    payload["email"] = user.email
    return payload


def token_row(token: ApiToken) -> dict:
    """A token without its secret: only the prefix, which is the lookup key."""
    return {
        "prefix": token.prefix,
        "name": token.name,
        "is_demo": token.is_demo,
        "created_at": token.created_at.isoformat(),
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
        "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
    }


# --- views ---------------------------------------------------------------


class LoginView(APIView):
    """POST /api/v1/auth/login. Session cookie for the browser, nothing else."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]
    throttle_scope = "login"

    @extend_schema(
        operation_id="login",
        summary="Sign in with email and password and start a session.",
        description="A wrong email and a wrong password give the same message, so the "
                    "endpoint cannot be used to find out which addresses exist. "
                    "Rate limited to 10 attempts per 15 minutes per IP and email.",
        request=LoginSerializer,
        responses={200: LoginResponseSerializer,
                   **error_responses(400, 401, 403, 429)},
        tags=TAGS,
    )
    def post(self, request):
        require_csrf(request)
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(serializer.validated_data["email"], serializer.validated_data["password"])
        if user is None:
            raise ApiError("invalid_credentials", INVALID_CREDENTIALS, 401)
        start_session(request, user)
        return Response({
            "user": user_self(user),
            "redirect_to": safe_next(request, serializer.validated_data.get("next")),
        })


class LogoutView(APIView):
    """POST /api/v1/auth/logout."""

    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id="logout", summary="End the current session.",
                   request=None, responses={200: OkRedirectSerializer,
                                             **error_responses(401)}, tags=TAGS)
    def post(self, request):
        end_session(request)
        return Response({"ok": True, "redirect_to": "/"})


class RegisterView(APIView):
    """POST /api/v1/auth/register."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="register",
        summary="Create an account and sign in.",
        description="Email is unique and the password must be at least 8 characters and "
                    "pass Django's password checks. Field errors come back under "
                    "error.fields so the form can paint them inline.",
        request=RegisterSerializer,
        responses={201: LoginResponseSerializer, **error_responses(400, 403)},
        tags=TAGS,
    )
    def post(self, request):
        require_csrf(request)
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = register_user(email=data["email"], password=data["password"],
                             display_name=data.get("display_name", ""))
        start_session(request, user)
        # Like login: return to the page that sent the visitor here (an invite link),
        # but only ever on this host.
        return Response({"user": user_self(user), "redirect_to": safe_next(request, data.get("next"))},
                        status=201)


class DemoLoginView(APIView):
    """POST /api/v1/auth/demo-login. Exists only while DEMO_MODE is on."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="demo_login",
        summary="Sign in as one of the seeded demo accounts.",
        description="Answers 404 when DEMO_MODE is off: the shortcut is not part of a "
                    "production portal at all, not merely disabled.",
        request=DemoLoginSerializer,
        responses={200: LoginResponseSerializer, **error_responses(400, 403, 404)},
        tags=TAGS,
    )
    def post(self, request):
        require_csrf(request)
        serializer = DemoLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = demo_login(serializer.validated_data["role"])
        start_session(request, user)
        return Response({"user": user_self(user), "redirect_to": "/me"})


class MeView(APIView):
    """GET /api/v1/me. The caller's profile and their role in every event."""

    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id="me", summary="Your profile and your roles.",
                   responses={200: MeSerializer, **error_responses(401)}, tags=TAGS)
    def get(self, request):
        roles = visible_roles(request.user)
        return Response({
            "user": user_self(request.user),
            "roles": [
                {
                    "public_id": role.public_id,
                    "role": role.role,
                    "event": {"slug": role.event.slug, "name": role.event.name},
                    "tracks": [{"public_id": track.public_id, "name": track.name}
                               for track in role.tracks.all()],
                }
                for role in roles
            ],
        })


class ChangePasswordView(APIView):
    """POST /api/v1/me/password."""

    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id="change_password",
                   summary="Change your own password (current password required).",
                   request=ChangePasswordSerializer,
                   responses={200: ChangePasswordResponseSerializer,
                              **error_responses(400, 401)}, tags=TAGS)
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_password(request.user, serializer.validated_data["current_password"],
                        serializer.validated_data["password"])
        return Response({"ok": True})


class TokenListCreateView(APIView):
    """GET|POST /api/v1/me/tokens."""

    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id="my_tokens", summary="List your API tokens.",
                   description="The list never contains a secret: a token is addressed by "
                               "its 12-character prefix, which is also what DELETE takes.",
                   responses={200: TokenListSerializer, **error_responses(401)}, tags=TAGS)
    def get(self, request):
        return Response({"tokens": [token_row(token) for token in visible_tokens(request.user)]})

    @extend_schema(operation_id="create_token", summary="Create an API token.",
                   description="The plaintext is in the response exactly once; only its "
                               "sha256 is stored, so it cannot be shown again.",
                   request=TokenCreateSerializer,
                   responses={201: TokenCreatedSerializer, **error_responses(400, 401)},
                   tags=TAGS)
    def post(self, request):
        serializer = TokenCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token, plaintext = create_api_token(request.user, serializer.validated_data["name"])
        return Response({"token": token_row(token), "plaintext": plaintext}, status=201)


class TokenDetailView(APIView):
    """DELETE /api/v1/me/tokens/{prefix}."""

    permission_classes = [IsAuthenticated]

    @extend_schema(operation_id="revoke_token", summary="Revoke one of your API tokens.",
                   responses={200: TokenRowSerializer, **error_responses(401, 404)},
                   tags=TAGS)
    def delete(self, request, prefix: str):
        token = revoke_api_token(request.user, prefix)
        return Response({"token": token_row(token)}, status=200)


class ResetLinkView(APIView):
    """POST /api/v1/admin/users/{public_id}/reset-link. Admin only."""

    permission_classes = [IsPlatformAdmin]

    @extend_schema(operation_id="user_reset_link",
                   summary="Generate a one-time password reset link.",
                   description="Offline portal, no email: the link is returned to the admin "
                               "to hand over. Valid 24 hours, single use, stored hashed, and "
                               "issuing a new link retires any outstanding one.",
                   request=None,
                   responses={201: ResetLinkSerializer, **error_responses(403, 404)},
                   tags=TAGS)
    def post(self, request, public_id: str):
        target = User.objects.filter(public_id=public_id).first()
        if target is None:
            raise ApiError("user_not_found", "No such user.", 404)
        token, plaintext = create_reset_link(request.user, target)
        return Response(
            {
                "user": user_brief(target),
                "reset_url": f"/reset?token={plaintext}",
                "expires_at": token.expires_at.isoformat(),
            },
            status=201,
        )


class AdminUserListView(GenericAPIView):
    """GET /api/v1/admin/users."""

    permission_classes = [IsPlatformAdmin]
    pagination_class = VerdictPagination

    @extend_schema(operation_id="admin_users", summary="Every account (admin only).",
                   responses={200: AdminUserSerializer(many=True),
                              **error_responses(401, 403)}, tags=TAGS)
    def get(self, request):
        users = visible_users(request.query_params.get("q", ""))
        page = self.paginate_queryset(users)
        rows = [dict(user_brief(user), email=user.email) for user in page]
        return self.get_paginated_response(rows)


class AdminUserDetailView(APIView):
    """PATCH /api/v1/admin/users/{public_id}. Admin only."""

    permission_classes = [IsPlatformAdmin]

    @extend_schema(operation_id="admin_user_patch",
                   summary="Toggle is_host, is_admin or is_active on an account.",
                   description="An admin cannot remove their own administrator flag: "
                               "409 cannot_demote_self. Every change is audited.",
                   request=UserPatchSerializer,
                   responses={200: AdminUserSerializer,
                              **error_responses(400, 401, 403, 404, 409)},
                   tags=TAGS)
    def patch(self, request, public_id: str):
        target = User.objects.filter(public_id=public_id).first()
        if target is None:
            raise ApiError("user_not_found", "No such user.", 404)
        serializer = UserPatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fields = serializer.validated_data
        if not fields:
            raise ApiError("nothing_to_change", "Send at least one of is_host, is_admin, is_active.", 400)
        user = set_user_flags(request.user, target, **fields)
        return Response(dict(user_brief(user), email=user.email))


class ResetPasswordView(APIView):
    """POST /api/v1/auth/reset-password. Consumes a reset link."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(operation_id="reset_password",
                   summary="Set a new password with a reset link token.",
                   description="Single use and 24 hours. Used, expired and unknown tokens "
                               "are all refused, and the password itself still has to pass "
                               "the same checks as registration.",
                   request=ResetPasswordSerializer,
                   responses={200: ResetPasswordResponseSerializer,
                              **error_responses(400, 403)}, tags=TAGS)
    def post(self, request):
        require_csrf(request)
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = reset_password(serializer.validated_data["token"], serializer.validated_data["password"])
        return Response({"ok": True, "user": user_brief(user), "redirect_to": "/login?reset=1"})
