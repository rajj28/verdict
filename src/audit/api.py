"""Audit API: read-only log of every state change in an event.

Organizers and admins only. Thin view; all scoping lives in audit.policy.
"""
from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
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


#: Audit responses are organizer-only evidence: never stored, never shared.
PRIVATE_CACHE = "private, no-store"


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
        responses={
            200: OpenApiResponse(description="Paginated audit events."),
            403: OpenApiResponse(description="Not an organizer."),
        },
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


class AuditVerifyView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OpenApiResponse(description="Chain hashes, sequence and retained head verification.")})
    def get(self, request, slug=None, chain_id=None):
        event = _get_event(slug) if slug else None
        if event is None:
            require_admin(request.user)
        else:
            require_organizer(request.user, event)
        return _private(Response(services.verify_chain(event, chain_id=chain_id)))


class AuditCheckpointView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: OpenApiResponse(description="Private JSON checkpoint and canonical entries (bounded export).")})
    def get(self, request, slug=None, chain_id=None):
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
