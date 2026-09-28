"""JSON endpoints for ballots, voting results, moderation and project comments."""
import csv
import io
import secrets

from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

import audit.services
from community import policy, services
from community.models import Ballot, VotingAccess, VotingConfig
from core.errors import ApiError
from events.models import Event
from projects.policy import public_projects

from core.schema import error_responses

TAGS = ["Community voting"]


class BallotItemSerializer(serializers.Serializer):
    project = serializers.CharField(max_length=32)
    votes = serializers.IntegerField(min_value=0, max_value=2_147_483_647)


class BallotWriteSerializer(serializers.Serializer):
    items = BallotItemSerializer(many=True)
    link_token = serializers.CharField(required=False, allow_blank=True)
    email_ticket = serializers.CharField(required=False, allow_blank=True)


class BallotCreateSerializer(serializers.Serializer):
    link_token = serializers.CharField(required=False, allow_blank=True)
    email_ticket = serializers.CharField(required=False, allow_blank=True)


class EmailVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()


class EmailTicketSerializer(serializers.Serializer):
    token = serializers.CharField()


class EmailDeliverySerializer(serializers.Serializer):
    sent = serializers.BooleanField()
    delivery = serializers.ChoiceField(choices=("queued", "sent"))
    detail = serializers.CharField()


class CommentWriteSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=1000)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300)


class NullableDateTimeField(serializers.DateTimeField):
    def to_internal_value(self, data):
        if data == "":
            return None
        return super().to_internal_value(data)


class VotingConfigWriteSerializer(serializers.Serializer):
    """Partial update of the voting configuration; every field is optional."""

    access = serializers.ChoiceField(choices=VotingAccess.choices, required=False)
    style = serializers.ChoiceField(choices=("single", "quadratic"), required=False)
    credits = serializers.IntegerField(min_value=1, required=False)
    max_votes_per_project = serializers.IntegerField(min_value=1, required=False)
    rotate_link = serializers.BooleanField(required=False)
    voting_open_at = NullableDateTimeField(required=False, allow_null=True)
    voting_close_at = NullableDateTimeField(required=False, allow_null=True)


class BallotProjectSerializer(serializers.Serializer):
    """One row of the ballot: the project and the votes placed on it so far."""

    public_id = serializers.CharField()
    title = serializers.CharField()
    votes = serializers.IntegerField(help_text="0 while the ballot is unsubmitted.")


class BallotSerializer(serializers.Serializer):
    """A ballot and the rules that govern it, so the page needs no second call."""

    ballot = serializers.CharField(help_text="Ballot public id; the PUT target.")
    projects = BallotProjectSerializer(many=True)
    style = serializers.CharField(help_text="'single' or 'quadratic'.")
    credits = serializers.IntegerField(
        help_text="Quadratic credit budget for the whole ballot. The cost of one vote is "
                  "votes squared, so total spend is the sum of squares; unspent credits are "
                  "not refunded."
    )
    max_votes_per_project = serializers.IntegerField(
        help_text="0 means one vote per project in quadratic mode."
    )


class TallySerializer(serializers.Serializer):
    """One aggregate row. The underscored names are the tally keys the API returns."""

    project__public_id = serializers.CharField()
    project__title = serializers.CharField()
    votes = serializers.IntegerField()
    credits = serializers.IntegerField(
        help_text="Sum of votes squared, the quadratic cost actually spent."
    )


class VotingResultsSerializer(serializers.Serializer):
    event = serializers.CharField()
    results = TallySerializer(many=True)


class VotingConfigReadSerializer(serializers.Serializer):
    """The current voting configuration and its window, as read back."""

    configured = serializers.BooleanField()
    access = serializers.CharField(required=False,
                                   help_text="Present once configured.")
    style = serializers.CharField(required=False)
    credits = serializers.IntegerField(required=False)
    max_votes_per_project = serializers.IntegerField(required=False)
    link_token = serializers.CharField(
        required=False,
        help_text="Organizer capability that opens open-link voting. Treat it as a secret; "
                  "it is never returned to a non-organizer.",
    )
    voting_open_at = NullableDateTimeField(required=False, allow_null=True)
    voting_close_at = NullableDateTimeField(required=False, allow_null=True)


class CommentSerializer(serializers.Serializer):
    """A visible comment. Hidden comments are absent from the list, not marked."""

    public_id = serializers.CharField()
    author = serializers.CharField(help_text="Display name, never an email address.")
    body = serializers.CharField()
    created_at = serializers.DateTimeField()


class CommentListSerializer(serializers.Serializer):
    comments = CommentSerializer(many=True)


class CommentCreatedSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    body = serializers.CharField()


class CommentModeratedSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    hidden = serializers.BooleanField()


class ManagedBallotItemSerializer(serializers.Serializer):
    project = serializers.CharField()
    votes = serializers.IntegerField()


class ManagedBallotSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    submitted_at = serializers.DateTimeField(allow_null=True)
    voided_at = serializers.DateTimeField(allow_null=True)
    void_reason = serializers.CharField(allow_null=True, allow_blank=True)
    items = ManagedBallotItemSerializer(many=True)


class AbuseFlagSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    kind = serializers.CharField()
    subject = serializers.CharField(allow_null=True, allow_blank=True)
    detail = serializers.CharField(allow_null=True, allow_blank=True)
    created_at = serializers.DateTimeField()
    resolved_at = serializers.DateTimeField(allow_null=True)


class VotingAuditRowSerializer(serializers.Serializer):
    action = serializers.CharField()
    target = serializers.CharField(allow_null=True, allow_blank=True)
    actor = serializers.CharField(allow_blank=True)
    summary = serializers.CharField(allow_blank=True)
    data = serializers.DictField()
    created_at = serializers.DateTimeField()


class VotingManagementSerializer(serializers.Serializer):
    """Organizer view of the ballot table, the abuse flags and the voting audit."""

    results = TallySerializer(many=True)
    ballots = ManagedBallotSerializer(many=True)
    flags = AbuseFlagSerializer(many=True)
    audit = VotingAuditRowSerializer(many=True)


class BallotModeratedSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    voided = serializers.BooleanField()


class FlagResolvedSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    resolved = serializers.BooleanField()


def _event(slug: str) -> Event:
    event = policy.visible_event(slug)
    if event is None:
        raise Http404("No such event.")
    return event


def _device_token(request) -> str:
    return request.COOKIES.get("verdict_voter", "")


def _ballot_payload(ballot: Ballot) -> dict:
    votes = {item.project.public_id: item.votes for item in ballot.items.select_related("project")}
    return {
        "ballot": ballot.public_id,
        "projects": [
            {"public_id": project.public_id, "title": project.title,
             "votes": votes.get(project.public_id, 0)}
            for project in services.ballot_order(ballot)
        ],
        "style": ballot.voter.event.voting_config.style,
        "credits": ballot.voter.event.voting_config.credits,
        "max_votes_per_project": ballot.voter.event.voting_config.max_votes_per_project,
    }


class BallotView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="community_ballot",
        summary="Read the caller's ballot for an event, or start one.",
        description="The same operation serves /votes/ballot and /votes/ballot/{public_id}; "
                    "an id that is not the caller's ballot is 404 ballot_not_found. An "
                    "anonymous visitor is identified by a device cookie or a capability, "
                    "which is not proof of one human: cookies, email gates and accounts "
                    "establish continuity, not uniqueness.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("v", str, OpenApiParameter.QUERY, required=False,
                             description="Voting link capability, for open-link voting."),
            OpenApiParameter("email_ticket", str, OpenApiParameter.QUERY, required=False,
                             description="One-time ticket from a delivered email link."),
        ],
        responses={200: BallotSerializer, **error_responses(403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug, public_id=None):
        event = _event(slug)
        ballot = services.current_ballot(
            event, request.user, request, link_token=request.query_params.get("v", ""),
            device_token=_device_token(request),
            email_ticket=request.query_params.get("email_ticket", ""),
        )
        if public_id is not None and ballot.public_id != public_id:
            raise ApiError("ballot_not_found", "No ballot was found for this voter.",
                           status_code=404)
        return Response(_ballot_payload(ballot))

    @extend_schema(
        operation_id="community_ballot_create",
        summary="Open a ballot before placing any votes.",
        description="Needed for 'single' style voting, where the choice is one project. A "
                    "second ballot for the same identity is 409, not a second chance to "
                    "vote. In open-link mode a device cookie is set when there is none yet.",
        request=BallotCreateSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={201: BallotSerializer,
                   **error_responses(400, 403, 404, 409, 429)},
        tags=TAGS,
    )
    def post(self, request, slug):
        event = _event(slug)
        config = VotingConfig.objects.filter(event=event).first()
        data = dict(request.data) if isinstance(request.data, dict) else {}
        if config and config.access == VotingAccess.OPEN_LINK:
            data.setdefault("link_token", request.query_params.get("v", ""))
        serializer = BallotCreateSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        device_token = _device_token(request)
        if config and config.access == VotingAccess.OPEN_LINK and not device_token:
            device_token = secrets.token_urlsafe(32)
        ballot = services.create_ballot(
            event, request.user, request, link_token=serializer.validated_data.get("link_token", ""),
            device_token=device_token,
            email_ticket=serializer.validated_data.get("email_ticket", ""),
        )
        response = Response(_ballot_payload(ballot), status=201)
        if config and config.access == VotingAccess.OPEN_LINK and not _device_token(request):
            response.set_cookie(
                "verdict_voter", device_token, max_age=60 * 60 * 24 * 365,
                httponly=True, secure=request.is_secure(), samesite="Lax", path="/",
            )
        return response

    @extend_schema(
        operation_id="community_ballot_submit",
        summary="Submit the votes on a ballot.",
        description="The whole ballot is replaced atomically, so a partial network write "
                    "cannot leave half a ballot. The credit budget is checked server-side: "
                    "asking for more votes than the quadratic budget allows is 400 credits. "
                    "After the close the answer is 409 voting_closed.",
        request=BallotWriteSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Ballot public id being submitted."),
        ],
        responses={200: BallotSerializer,
                   **error_responses(400, 403, 404, 409, 429)},
        tags=TAGS,
    )
    def put(self, request, slug, public_id):
        event = _event(slug)
        serializer = BallotWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ballot = services.submit_ballot(
            event, public_id, request.user, request, serializer.validated_data["items"],
            link_token=serializer.validated_data.get(
                "link_token", request.query_params.get("v", "")
            ),
            device_token=_device_token(request),
            email_ticket=serializer.validated_data.get("email_ticket", ""),
        )
        return Response(_ballot_payload(ballot))


class EventBallotDetailView(BallotView):
    """The same view on /votes/ballot/{public_id}.

    Split out purely so each documented operation has its own operation id: the
    schema names the two routes separately, and one method serving both paths
    would collide. Behaviour is BallotView's, unchanged.
    """

    @extend_schema(
        operation_id="community_ballot_detail",
        summary="Read one ballot by its public id.",
        description="Answers the caller's own ballot; any other id is 404 ballot_not_found, "
                    "so a ballot id cannot be probed.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Ballot public id."),
            OpenApiParameter("v", str, OpenApiParameter.QUERY, required=False,
                             description="Voting link capability, for open-link voting."),
            OpenApiParameter("email_ticket", str, OpenApiParameter.QUERY, required=False,
                             description="One-time ticket from a delivered email link."),
        ],
        responses={200: BallotSerializer, **error_responses(403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug, public_id=None):
        return super().get(request, slug, public_id=public_id)

    @extend_schema(
        operation_id="community_ballot_detail_submit",
        summary="Submit the votes on a ballot addressed by its public id.",
        description="Identical to submitting on /votes/ballot with the id in the path.",
        request=BallotWriteSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Ballot public id being submitted."),
        ],
        responses={200: BallotSerializer,
                   **error_responses(400, 403, 404, 409, 429)},
        tags=TAGS,
    )
    def put(self, request, slug, public_id):
        return super().put(request, slug, public_id)

    @extend_schema(
        operation_id="community_ballot_detail_create",
        summary="Open a ballot from the detail route (same operation as the collection).",
        description="Identical to POST /votes/ballot. Documented separately so each path "
                    "carries its own operation id.",
        request=BallotCreateSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Ignored on this route; ballots get their id on create."),
        ],
        responses={201: BallotSerializer,
                   **error_responses(400, 403, 404, 409, 429)},
        tags=TAGS,
    )
    def post(self, request, slug, public_id=None):
        return super().post(request, slug)


class EmailVotingView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="request_voting_email", tags=["Community voting"],
        request=EmailVerificationSerializer,
        responses={202: EmailDeliverySerializer,
                   **error_responses(400, 403, 409, 429, 503)},
        description="Queues a private organizer-delivered link offline, or sends via the configured email "
                    "backend. The response never contains the link. Offline delivery does not prove mailbox ownership.",
    )
    def post(self, request, slug):
        event = _event(slug)
        serializer = EmailVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        delivery = services.request_email_verification(
            event, serializer.validated_data["email"], request
        )
        return Response({
            "sent": delivery == "sent", "delivery": delivery,
            "detail": ("Your link is queued for the event organizer to deliver. Contact the organizer; "
                       "no email was sent. This offline process does not verify mailbox ownership."
                       if delivery == "queued" else "Your voting link was accepted by the email service. Check your email."),
        }, status=202)


class EmailVotingVerifyView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="verify_voting_email",
        summary="Exchange a delivered email ticket for a ballot.",
        description="One-time ticket. Consuming it shows the link reached whoever holds the "
                    "mailbox; it does not show a single human, and an organizer-queued "
                    "offline link is not even that.",
        request=EmailTicketSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={201: BallotSerializer,
                   **error_responses(400, 403, 404, 409, 410)},
        tags=TAGS,
    )
    def post(self, request, slug):
        event = _event(slug)
        serializer = EmailTicketSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ballot = services.create_ballot(
            event, request.user, request, email_ticket=serializer.validated_data["token"]
        )
        return Response(_ballot_payload(ballot), status=201)


class VotingResultsView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="community_voting_results",
        summary="Aggregate vote totals for an event.",
        description="Aggregate only: no ballot, no voter, no device token. Answered 404 "
                    "until the organizer's policy makes results visible, which is a "
                    "separate decision from the voting window closing.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={200: VotingResultsSerializer, **error_responses(404)},
        tags=TAGS,
    )
    def get(self, request, slug):
        event = _event(slug)
        rows = services.tallies_for_event(request.user, event)
        return Response({"event": event.slug, "results": rows})


class VotingConfigView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="community_voting_config",
        summary="Read the event's voting configuration.",
        description="Organizer only, because the response carries the open-link capability. "
                    "A participant gets 403 rather than a redacted copy, so the capability "
                    "cannot be learned by asking.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={200: VotingConfigReadSerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug):
        event = _event(slug)
        config = policy.visible_config(request.user, event)
        if config is None:
            return Response({
                "configured": False, "voting_open_at": event.voting_open_at,
                "voting_close_at": event.voting_close_at,
            })
        return Response({
            "configured": True, "access": config.access, "style": config.style,
            "credits": config.credits, "max_votes_per_project": config.max_votes_per_project,
            "link_token": config.link_token, "voting_open_at": event.voting_open_at,
            "voting_close_at": event.voting_close_at,
        })

    @extend_schema(
        operation_id="community_voting_config_update",
        summary="Change the access mode, ballot style, credits or window.",
        description="Organizer only. rotate_link mints a new open-link capability and "
                    "retires the old one. The write is re-checked under lock, so a change "
                    "that races the close is 409 rather than a window that is both open and "
                    "closed.",
        request=VotingConfigWriteSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={200: VotingConfigReadSerializer,
                   **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
    def patch(self, request, slug):
        event = _event(slug)
        serializer = VotingConfigWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        config = services.configure_voting(
            event, request.user, serializer.validated_data, request
        )
        return Response({
            "configured": True, "access": config.access, "style": config.style,
            "credits": config.credits, "max_votes_per_project": config.max_votes_per_project,
            "link_token": config.link_token, "voting_open_at": config.event.voting_open_at,
            "voting_close_at": config.event.voting_close_at,
        })


class ProjectCommentsView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="community_project_comments",
        summary="Read the public comment thread on one project.",
        description="Hidden comments are absent from the list rather than marked, and author "
                    "display names carry no email address.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
        ],
        responses={200: CommentListSerializer, **error_responses(404)},
        tags=TAGS,
    )
    def get(self, request, slug, public_id):
        event = _event(slug)
        project = get_object_or_404(
            public_projects(event), event=event, public_id=public_id
        )
        comments = policy.visible_comments(request.user, project)
        return Response({"comments": [
            {"public_id": row.public_id, "author": row.author.display_name, "body": row.body,
             "created_at": row.created_at}
            for row in comments
        ]})

    @extend_schema(
        operation_id="community_project_comment_create",
        summary="Post a comment on a project.",
        description="Sign-in required. The body is length-checked server-side, and a "
                    "moderator can hide it afterwards with a recorded reason.",
        request=CommentWriteSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
        ],
        responses={201: CommentCreatedSerializer, **error_responses(400, 401, 404, 429)},
        tags=TAGS,
    )
    def post(self, request, slug, public_id):
        if not request.user.is_authenticated:
            raise ApiError("not_authenticated", "Sign in to comment.", status_code=401)
        event = _event(slug)
        project = get_object_or_404(
            public_projects(event), event=event, public_id=public_id
        )
        serializer = CommentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = services.create_comment(
            project, request.user, serializer.validated_data["body"], request
        )
        return Response({"public_id": comment.public_id, "body": comment.body}, status=201)


class ModerateCommentView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="moderate_comment",
        summary="Hide or restore one comment, with a recorded reason.",
        description="Organizer only. A reason is mandatory and the change is audited; there "
                    "is no silent retroactive edit, and 'restore' puts a hidden comment back "
                    "rather than recreating it.",
        request=ReasonSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Public id of the ballot, comment or flag."),
            OpenApiParameter("action", str, OpenApiParameter.PATH,
                             description="'hide' or 'restore' for a comment; "
                                         "'void' or 'restore' for a ballot."),
        ],
        responses={200: CommentModeratedSerializer,
                   **error_responses(400, 401, 403, 404)},
        tags=TAGS,
    )
    def post(self, request, slug, public_id, action):
        if action not in {"hide", "restore"}:
            raise ApiError("invalid_action", "Choose hide or restore.", status_code=400)
        event = _event(slug)
        project = get_object_or_404(public_projects(event), event=event, community_comments__public_id=public_id)
        comment = get_object_or_404(
            policy.visible_comments(request.user, project), public_id=public_id
        )
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = services.moderate_comment(
            comment, request.user, serializer.validated_data["reason"], request,
            restore=action == "restore",
        )
        return Response({"public_id": row.public_id, "hidden": row.hidden_at is not None})


class VotingManagementView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="community_voting_manage",
        summary="Organizer view of ballots, abuse flags and the voting audit.",
        description="Organizer only. Individual ballots are listed so one can be voided with "
                    "a reason, never silently dropped.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={200: VotingManagementSerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug):
        event = _event(slug)
        policy.require_manager(request.user, event)
        ballots = policy.visible_ballots(request.user, event)
        flags = policy.visible_flags(request.user, event)
        audit_rows = policy.visible_voting_audit(request.user, event)
        return Response({
            "results": services.tallies_for_event(request.user, event),
            "ballots": [
                {"public_id": ballot.public_id, "submitted_at": ballot.submitted_at,
                 "voided_at": ballot.voided_at, "void_reason": ballot.void_reason,
                 "items": [{"project": item.project.public_id, "votes": item.votes}
                           for item in ballot.items.all()]}
                for ballot in ballots
            ],
            "flags": [
                {"public_id": flag.public_id, "kind": flag.kind, "subject": flag.subject,
                 "detail": flag.detail, "created_at": flag.created_at,
                 "resolved_at": flag.resolved_at}
                for flag in flags
            ],
            "audit": [
                {"action": row.action, "target": row.target_id, "actor": row.actor_label,
                 "summary": row.summary, "data": row.data, "created_at": row.created_at}
                for row in audit_rows
            ],
        })


class ModerateBallotView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="moderate_ballot",
        summary="Void or restore one ballot, with a recorded reason.",
        description="Organizer only. A voided ballot keeps its row and its reason and stops "
                    "counting; restoring puts it back.",
        request=ReasonSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Public id of the ballot, comment or flag."),
            OpenApiParameter("action", str, OpenApiParameter.PATH,
                             description="'hide' or 'restore' for a comment; "
                                         "'void' or 'restore' for a ballot."),
        ],
        responses={200: BallotModeratedSerializer,
                   **error_responses(400, 401, 403, 404)},
        tags=TAGS,
    )
    def post(self, request, slug, public_id, action):
        if action not in {"void", "restore"}:
            raise ApiError("invalid_action", "Choose void or restore.", status_code=400)
        event = _event(slug)
        ballot = get_object_or_404(
            policy.visible_ballots(request.user, event), public_id=public_id
        )
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = services.moderate_ballot(
            ballot, request.user, serializer.validated_data["reason"], request,
            restore=action == "restore",
        )
        return Response({"public_id": row.public_id, "voided": row.voided_at is not None})


class ResolveFlagView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="resolve_abuse_flag",
        summary="Mark one voting abuse flag as resolved.",
        description="Organizer only. Resolution is recorded under lock and audited; the flag "
                    "itself is kept, because deleting it would hide that abuse was reported.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Project public id."),
        ],
        responses={200: FlagResolvedSerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def post(self, request, slug, public_id):
        event = _event(slug)
        with transaction.atomic():
            flag = get_object_or_404(
                policy.visible_flags(request.user, event).select_for_update(),
                event=event, public_id=public_id,
            )
            if flag.resolved_at is None:
                flag.resolved_at = services.now()
                flag.save(update_fields=["resolved_at"])
                audit.services.record(
                    request.user, "community.abuse_flag.resolved", event=event, target=flag,
                    summary="A voting abuse flag was resolved.",
                    data={"kind": flag.kind}, request=request,
                )
        return Response({"public_id": flag.public_id, "resolved": True})


class VotesExportView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="community_votes_export",
        summary="Download the event's vote totals as CSV.",
        description="Organizer only. Aggregate rows only, in the same order as the results "
                    "API. A cell starting with =, +, - or @ is prefixed with a quote so a "
                    "spreadsheet cannot execute a project title as a formula.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={200: OpenApiResponse(
                      description="CSV file with project_public_id, project_title, votes "
                                  "and quadratic_credits columns.",
                  ),
                  **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug):
        event = _event(slug)
        policy.require_manager(request.user, event)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["project_public_id", "project_title", "votes", "quadratic_credits"])
        for row in services.tallies_for_event(request.user, event):
            public_id = row["project__public_id"]
            title = row["project__title"]
            writer.writerow([_safe_csv_cell(public_id), _safe_csv_cell(title), row["votes"] or 0,
                             row["credits"] or 0])
        response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{event.slug}-votes.csv"'
        return response


def _safe_csv_cell(value: str) -> str:
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
