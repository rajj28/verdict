"""Audit API: read-only log of every state change in an event.

Organizers and admins only. Thin view; all scoping lives in audit.policy.
"""
from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from django.http import HttpResponse
import json

from audit.policy import require_admin, require_organizer, visible_audit_events
from audit import services
from core.errors import ApiError
from core.pagination import VerdictPagination
from events.policy import get_event_by_slug

from core.schema import error_responses


TAGS = ["audit"]

#: Audit responses are organizer-only evidence: never stored, never shared.
PRIVATE_CACHE = "private, no-store"


# --- serializers ---------------------------------------------------------


class AuditEntrySerializer(serializers.Serializer):
    """One hash-chained entry. The integer pk is never exposed."""

    entry_id = serializers.CharField(help_text="Stable public identity '<chain_id>:<sequence>'.")
    chain_id = serializers.CharField()
    created_at = serializers.DateTimeField()
    actor = serializers.CharField(help_text="Display label captured at write time.")
    actor_public_id = serializers.CharField(allow_null=True)
    action = serializers.CharField(help_text="Dotted action name, e.g. 'project.submitted'.")
    target_type = serializers.CharField(allow_null=True)
    target_id = serializers.CharField(allow_null=True)
    summary = serializers.CharField(allow_blank=True)
    data = serializers.DictField(help_text="Action-specific structured detail.")
    sequence = serializers.IntegerField()
    previous_hash = serializers.CharField()
    entry_hash = serializers.CharField()


class AuditCheckpointSerializer(serializers.Serializer):
    """The independently retainable head a verifier can check against later."""

    version = serializers.IntegerField(help_text="Hash format version.")
    chain_id = serializers.CharField(allow_null=True)
    scope = serializers.CharField(help_text="'global' or 'event'.")
    event_slug_at_creation = serializers.CharField(allow_blank=True)
    sequence = serializers.IntegerField()
    head_hash = serializers.CharField()
    legacy_entries = serializers.IntegerField()


class AuditVerifySerializer(serializers.Serializer):
    """Result of re-hashing a consistent prefix of the chain."""

    ok = serializers.BooleanField(help_text="False if any consistency error was found.")
    chain_id = serializers.CharField(allow_null=True)
    sequence = serializers.IntegerField(help_text="Length the stored head claims.")
    head_hash = serializers.CharField()
    checked = serializers.IntegerField(help_text="Entries actually rehashed and compared.")
    errors = serializers.ListField(child=serializers.CharField())
    checkpoint = AuditCheckpointSerializer()
    retained_head_present = serializers.BooleanField()
    limitation = serializers.CharField(help_text="What this check does not prove.")


class AuditCheckpointEntrySerializer(serializers.Serializer):
    payload = serializers.DictField(help_text="Canonical, versioned entry payload.")
    entry_hash = serializers.CharField()


class AuditCheckpointDocumentSerializer(AuditVerifySerializer):
    """The bounded download: a verification result plus the canonical entries."""

    entries = AuditCheckpointEntrySerializer(many=True)
    entries_included = serializers.BooleanField(
        help_text="False when the chain is longer than the export bound; the download "
                  "then carries the checkpoint only and note explains why."
    )
    note = serializers.CharField(required=False, allow_blank=True)


def _get_event(slug: str):
    event = get_event_by_slug(slug)
    if event is None:
        raise ApiError("event_not_found", f"No event with slug {slug!r}.", status_code=404)
    return event


def _private(response):
    response["Cache-Control"] = PRIVATE_CACHE
    return response


class AuditListView(APIView):
    """``GET /events/{slug}/audit`` — organizer only.

    Filters (query params):
      - action: prefix match
      - actor: the actor's immutable public_id snapshot
      - since: ISO 8601 datetime
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="audit_list",
        summary="Audit log for an event (organizer).",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
            OpenApiParameter("action", str, OpenApiParameter.QUERY,
                             description="Filter by action prefix.", required=False),
            OpenApiParameter("actor", str, OpenApiParameter.QUERY,
                             description="Filter by actor public_id.", required=False),
            OpenApiParameter("since", str, OpenApiParameter.QUERY,
                             description="Filter by ISO 8601 datetime (inclusive).", required=False),
        ],
        responses={200: OpenApiResponse(
                      response=AuditEntrySerializer(many=True),
                      description="One page of audit entries.",
                  ),
                  **error_responses(401, 403, 404, 429)},
        tags=TAGS,
    )
    def get(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        require_organizer(request.user, event)
        action = request.query_params.get("action")
        actor_id = request.query_params.get("actor")
        since = request.query_params.get("since")
        qs = visible_audit_events(request.user, event, action=action,
                                   actor_public_id=actor_id, since=since)
        paginator = VerdictPagination()
        page = paginator.paginate_queryset(qs, request)
        rows = [
            {
                # (chain, sequence) is the stable public identity of an entry;
                # the integer pk is never exposed.
                "entry_id": f"{ae.chain.public_id}:{ae.sequence}",
                "chain_id": ae.chain.public_id,
                "created_at": ae.created_at.isoformat(),
                "actor": ae.actor_label,
                "actor_public_id": ae.actor_public_id,
                "action": ae.action,
                "target_type": ae.target_type,
                "target_id": ae.target_id,
                "summary": ae.summary,
                "data": ae.data,
                "sequence": ae.sequence,
                "previous_hash": ae.previous_hash,
                "entry_hash": ae.entry_hash,
            }
            for ae in (page if page is not None else qs)
        ]
        if page is not None:
            return _private(paginator.get_paginated_response(rows))
        return _private(Response({"results": rows, "count": len(rows)}))


class _AuditVerifyBase(APIView):
    """Shared implementation for the three audit-verification routes.

    One view class serves the event-scoped, global-admin and archived-chain
    URLs, but the schema has to name each of them separately, so the
    annotations live on the three thin subclasses below.
    """

    permission_classes = [IsAuthenticated]

    def verify(self, request, slug=None, chain_id=None):
        event = _get_event(slug) if slug else None
        if event is None:
            require_admin(request.user)
        else:
            require_organizer(request.user, event)
        return _private(Response(services.verify_chain(event, chain_id=chain_id)))

    def checkpoint(self, request, slug=None, chain_id=None):
        event = _get_event(slug) if slug else None
        if event is None:
            require_admin(request.user)
        else:
            require_organizer(request.user, event)
        document = services.export_chain(event, chain_id=chain_id)
        response = HttpResponse(json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False), content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="audit-{document["chain_id"] or "empty"}-checkpoint.json"'
        response["Cache-Control"] = PRIVATE_CACHE
        return response


VERIFY_ROLES = {
    "event": "Requires organizer of that event.",
    "global": "Platform administrator only.",
    "archived": "Platform administrator only.",
}


class EventAuditVerifyView(_AuditVerifyBase):
    @extend_schema(
        operation_id="event_audit_verify",
        summary="Re-hash an event's audit chain and report consistency.",
        description="Every retained entry is re-canonicalized and re-hashed in order. "
                    "The response says how far the stored head could be checked, never "
                    "that the chain is authentic: a privileged actor can rewrite both "
                    "the data and the digest. The private limitation field says so.",
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: AuditVerifySerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        return self.verify(request, slug=slug)


class GlobalAuditVerifyView(_AuditVerifyBase):
    @extend_schema(
        operation_id="admin_audit_verify",
        summary="Re-hash the platform-wide audit chain (admin).",
        description=VERIFY_ROLES["global"],
        responses={200: AuditVerifySerializer, **error_responses(401, 403)},
        tags=TAGS,
    )
    def get(self, request):
        return self.verify(request)


class ArchivedAuditVerifyView(_AuditVerifyBase):
    @extend_schema(
        operation_id="admin_audit_chain_verify",
        summary="Re-hash one archived audit chain by its public id (admin).",
        description=VERIFY_ROLES["archived"],
        parameters=[OpenApiParameter("chain_id", str, OpenApiParameter.PATH,
                                     description="Archived chain public id.")],
        responses={200: AuditVerifySerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, chain_id: str):
        return self.verify(request, chain_id=chain_id)


class EventAuditCheckpointView(_AuditVerifyBase):
    @extend_schema(
        operation_id="event_audit_checkpoint",
        summary="Download an event's private audit checkpoint.",
        description="Sent as a JSON attachment, never cached. Entries are omitted once the "
                    "chain is longer than the export bound, in which case entries_included "
                    "is false and the document carries the head only.",
        parameters=[OpenApiParameter("slug", str, OpenApiParameter.PATH,
                                     description="Event slug.")],
        responses={200: OpenApiResponse(
                      response=AuditCheckpointDocumentSerializer,
                      description="Checkpoint document, as a file download.",
                  ),
                  **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, slug: str):
        return self.checkpoint(request, slug=slug)


class GlobalAuditCheckpointView(_AuditVerifyBase):
    @extend_schema(
        operation_id="admin_audit_checkpoint",
        summary="Download the platform-wide audit checkpoint (admin).",
        description=VERIFY_ROLES["global"],
        responses={200: OpenApiResponse(
                      response=AuditCheckpointDocumentSerializer,
                      description="Checkpoint document, as a file download.",
                  ),
                  **error_responses(401, 403)},
        tags=TAGS,
    )
    def get(self, request):
        return self.checkpoint(request)


class ArchivedAuditCheckpointView(_AuditVerifyBase):
    @extend_schema(
        operation_id="admin_audit_chain_checkpoint",
        summary="Download one archived audit chain by its public id (admin).",
        description=VERIFY_ROLES["archived"],
        parameters=[OpenApiParameter("chain_id", str, OpenApiParameter.PATH,
                                     description="Archived chain public id.")],
        responses={200: OpenApiResponse(
                      response=AuditCheckpointDocumentSerializer,
                      description="Checkpoint document, as a file download.",
                  ),
                  **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def get(self, request, chain_id: str):
        return self.checkpoint(request, chain_id=chain_id)
