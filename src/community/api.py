"""JSON endpoints for ballots, voting results, moderation and project comments."""
import csv
import io
import secrets

from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
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


class VotingConfigSerializer(serializers.Serializer):
    access = serializers.ChoiceField(choices=VotingAccess.choices, required=False)
    style = serializers.ChoiceField(choices=("single", "quadratic"), required=False)
    credits = serializers.IntegerField(min_value=1, required=False)
    max_votes_per_project = serializers.IntegerField(min_value=1, required=False)
    rotate_link = serializers.BooleanField(required=False)
    voting_open_at = NullableDateTimeField(required=False, allow_null=True)
    voting_close_at = NullableDateTimeField(required=False, allow_null=True)


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


class EmailVotingView(APIView):
    permission_classes = (AllowAny,)

    @extend_schema(
        operation_id="request_voting_email", tags=["Community voting"],
        request=EmailVerificationSerializer,
        responses={202: EmailDeliverySerializer,
                   400: OpenApiResponse(description="Invalid email or access mode."),
                   403: OpenApiResponse(description="Voting is closed."),
                   409: OpenApiResponse(description="A ballot already exists."),
                   429: OpenApiResponse(description="Request limit reached."),
                   503: OpenApiResponse(description="Configured email service unavailable.")},
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

    def get(self, request, slug):
        event = _event(slug)
        rows = services.tallies_for_event(request.user, event)
        return Response({"event": event.slug, "results": rows})


class VotingConfigView(APIView):
    permission_classes = (IsAuthenticated,)

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

    def patch(self, request, slug):
        event = _event(slug)
        serializer = VotingConfigSerializer(data=request.data, partial=True)
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
