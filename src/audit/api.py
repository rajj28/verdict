"""Audit API: read-only log of every state change in an event.

Organizers and admins only. Thin view; all scoping lives in audit.policy.
"""
from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.policy import require_organizer, visible_audit_events
from core.errors import ApiError
from core.pagination import VerdictPagination
from events.policy import get_event_by_slug


def _get_event(slug: str):
    event = get_event_by_slug(slug)
    if event is None:
        raise ApiError("event_not_found", f"No event with slug {slug!r}.", status_code=404)
    return event


class AuditListView(APIView):
    """``GET /events/{slug}/audit`` — organizer only.

    Filters (query params):
      - action: prefix match
      - actor: actor user public_id
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
                "id": ae.pk,
                "created_at": ae.created_at.isoformat(),
                "actor": ae.actor_label,
                "action": ae.action,
                "target_type": ae.target_type,
                "target_id": ae.target_id,
                "summary": ae.summary,
                "data": ae.data,
            }
            for ae in (page if page is not None else qs)
        ]
        if page is not None:
            return paginator.get_paginated_response(rows)
        return Response({"results": rows, "count": len(rows)})
