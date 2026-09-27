"""Fixture import, JSON round trip and CSV exports.

DRF serializers and viewsets. Thin: validate, delegate to services, return the envelope.
"""
from core.errors import ApiError
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from events.policy import get_event_by_slug, is_organizer
from interop.exports import EXPORT_KINDS, export_csv
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView


class EventExportView(APIView):
    """``GET /api/v1/events/{slug}/exports/{kind}.csv``.

    A plain HttpResponse, not a DRF renderer, so the ``.csv`` path never goes
    through content negotiation and the bytes on the wire are the CSV
    (BUILD-SEC section 16). Permission first: the set of export kinds is
    organizer's information, not a public menu.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_export_csv",
        summary="Download one CSV export of an event.",
        description="Organizers and admins only. Kinds: reviews, results.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("kind", str, OpenApiParameter.PATH,
                             description="Export kind.",
                             enum=list(EXPORT_KINDS)),
        ],
        responses={
            (200, "text/csv"): OpenApiTypes.STR,
            403: OpenApiResponse(description="Not an organizer of this event."),
            404: OpenApiResponse(description="No such event or export kind."),
        },
    )
    def get(self, request, slug: str, kind: str):
        event = get_event_by_slug(slug)
        if event is None:
            raise ApiError("event_not_found", f"No event with slug {slug!r}.", status_code=404)
        if not is_organizer(request.user, event):
            raise ApiError("forbidden", "Only organizers of this event can export it.",
                           status_code=403)
        document = export_csv(event, kind)
        response = HttpResponse(document, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{event.slug}-{kind}.csv"'
        return response
