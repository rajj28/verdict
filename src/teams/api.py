"""Teams, membership and invite links.

DRF serializers and viewsets. Thin: validate, delegate to services, return the envelope.
"""
from core.errors import ApiError
from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema
from events.models import Event
from events.policy import get_event_by_slug
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from teams import services
from teams.models import Team, TeamMember
from teams.policy import team_by_public_id, team_for_read, team_members, visible_team, visible_teams

User = get_user_model()
TAGS = ["teams"]


def event_or_404(slug: str) -> Event:
    event = get_event_by_slug(slug)
    if event is None:
        raise ApiError("event_not_found", f"No event with slug {slug!r}.", status_code=404)
    return event


class MemberSerializer(serializers.ModelSerializer):
    """Display name and public id only: a team page never shows an email."""

    user = serializers.SlugRelatedField(slug_field="public_id", read_only=True)
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = TeamMember
        fields = ["user", "display_name", "is_owner", "joined_at"]

    def get_display_name(self, member: TeamMember) -> str:
        return member.user.display_name or member.user.email.split("@")[0]


class TeamSerializer(serializers.ModelSerializer):
    members = serializers.SerializerMethodField()
    event = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    member_count = serializers.SerializerMethodField()
    max_team_size = serializers.SerializerMethodField()

    class Meta:
        model = Team
        fields = ["public_id", "name", "event", "members", "member_count", "max_team_size",
                  "created_at"]

    def get_members(self, team: Team) -> list:
        return [dict(MemberSerializer(member).data) for member in team_members(team)]

    def get_member_count(self, team: Team) -> int:
        return len(self.get_members(team))

    def get_max_team_size(self, team: Team) -> int:
        return team.event.max_team_size


class TeamCreateSerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=True, max_length=200)


def team_payload(team: Team) -> dict:
    return TeamSerializer(team).data


class TeamListCreate(APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(
        operation_id="event_teams",
        summary="List the teams you may see, or create your own.",
        description="A participant sees only the team they are in; an organizer sees every "
                    "team in the event. Creating registers the caller as a participant and "
                    "makes them the owner. Names are unique per event, case-insensitively.",
        responses={200: TeamSerializer(many=True), 403: None, 404: None},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        event = event_or_404(slug)
        teams = (visible_teams(request.user, event)
                 .select_related("event").prefetch_related("memberships__user")
                 .order_by("name", "id"))
        return Response([team_payload(team) for team in teams])

    @extend_schema(
        operation_id="event_team_create",
        summary="Create your own team (you become its owner).",
        request=TeamCreateSerializer,
        responses={201: TeamSerializer, 400: None, 401: None, 403: None, 404: None,
                   409: None},
        tags=TAGS,
    )
    def post(self, request, slug: str):
        serializer = TeamCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        team = services.create_team(request.user, event_or_404(slug), serializer.validated_data["name"])
        return Response(team_payload(team), status=201)


class TeamDetail(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_team_detail",
        summary="Read one team you are a member of (organizers may read any).",
        responses={200: TeamSerializer, 404: None},
        tags=TAGS,
    )
    def get(self, request, slug: str, public_id: str):
        event = event_or_404(slug)
        team = team_for_read(event, public_id)
        if visible_team(request.user, team) is None:
            raise ApiError("team_not_found", f"No team {public_id!r} in {event.name}.", status_code=404)
        return Response(team_payload(team))


@extend_schema(
    operation_id="event_team_invite",
    summary="Mint a team invite link, revoking the one it replaces.",
    description="Members only. The response carries the full link, which is shown once "
                "in the UI; the token is a query parameter so it never lands in an "
                "access log path.",
    responses={201: None, 403: None, 404: None},
    tags=TAGS,
)
class TeamInviteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, slug: str, public_id: str):
        event = event_or_404(slug)
        team = team_by_public_id(event, public_id)
        if team is None:
            raise ApiError("team_not_found", f"No team {public_id!r} in {event.name}.", status_code=404)
        invite = services.rotate_invite(request.user, team)
        return Response({
            "team": team.public_id,
            "url": services.invite_link(invite),
            "expires_at": invite.expires_at,
        }, status=201)


@extend_schema(
    operation_id="invite_accept",
    summary="Join the team an invite link points at.",
    description="Refused with 410 invite_invalid when the link was replaced, revoked or "
                "has expired; 409 already_in_team, role_conflict or team_full when the "
                "window is open but the join does not fit.",
    responses={200: None, 401: None, 403: None, 409: None, 410: None},
    tags=TAGS,
)
class InviteAcceptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, token: str):
        member = services.accept_invite(request.user, token)
        return Response({
            "team": member.team.public_id,
            "event": member.event.slug,
            "is_owner": member.is_owner,
        }, status=201)


@extend_schema(
    operation_id="event_team_leave",
    summary="Leave your team; the owner passes ownership to the earliest member.",
    description="A team whose last member leaves is deleted, unless it holds a submitted "
                "project (409 withdraw_first) or a withdrawn or disqualified one "
                "(409 team_not_deletable), because that history is kept.",
    responses={200: None, 401: None, 403: None, 409: None},
    tags=TAGS,
)
class TeamLeaveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, slug: str, public_id: str):
        event = event_or_404(slug)
        team = team_by_public_id(event, public_id)
        if team is None:
            raise ApiError("team_not_found", f"No team {public_id!r} in {event.name}.", status_code=404)
        services.leave_team(request.user, team)
        return Response({"left": team.public_id})


@extend_schema(
    operation_id="event_team_remove_member",
    summary="Remove a member from your team (owner only).",
    responses={200: None, 401: None, 403: None, 404: None},
    tags=TAGS,
)
class TeamMemberDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, slug: str, public_id: str, user_public_id: str):
        event = event_or_404(slug)
        team = team_by_public_id(event, public_id)
        if team is None:
            raise ApiError("team_not_found", f"No team {public_id!r} in {event.name}.", status_code=404)
        user = User.objects.filter(public_id=user_public_id).only("id", "public_id").first()
        if user is None:
            raise ApiError("user_not_found", f"No account {user_public_id!r}.", status_code=404)
        services.remove_member(request.user, team, user)
        return Response({"removed": user_public_id})
