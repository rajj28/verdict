"""Fixture import, JSON round trip and CSV exports.

DRF serializers and viewsets. Thin: validate, delegate to services, return the envelope.
"""
from __future__ import annotations

import json

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.errors import ApiError
from events.policy import get_event_by_slug, is_organizer
from interop.exports import EXPORT_KINDS, event_json, export_csv


def _get_event(slug: str):
    event = get_event_by_slug(slug)
    if event is None:
        raise ApiError("event_not_found", f"No event with slug {slug!r}.", status_code=404)
    return event


def _require_organizer(user, event):
    if not is_organizer(user, event):
        raise ApiError(
            "forbidden",
            "Only organizers of this event can export it.",
            status_code=403,
        )


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
        description="Organizers and admins only. Kinds: participants, teams, projects, judges, "
                    "assignments, reviews, progress, results, audit.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
            OpenApiParameter(
                "kind", str, OpenApiParameter.PATH,
                description="Export kind.",
                enum=list(EXPORT_KINDS),
            ),
        ],
        responses={
            (200, "text/csv"): OpenApiTypes.STR,
            403: OpenApiResponse(description="Not an organizer of this event."),
            404: OpenApiResponse(description="No such event or export kind."),
        },
    )
    def get(self, request: Request, slug: str, kind: str) -> HttpResponse:
        event = _get_event(slug)
        _require_organizer(request.user, event)
        document = export_csv(event, kind)
        response = HttpResponse(document, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{event.slug}-{kind}.csv"'
        return response


class EventJsonExportView(APIView):
    """``GET /api/v1/events/{slug}/exports/event.json``.

    Fixtures-shaped JSON that can be re-imported via POST /api/v1/imports.
    Organizer-only.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_export_json",
        summary="Export an event as fixtures-shaped JSON (organizer).",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH, description="Event slug."),
        ],
        responses={
            (200, "application/json"): OpenApiTypes.STR,
            403: OpenApiResponse(description="Not an organizer of this event."),
            404: OpenApiResponse(description="No such event."),
        },
    )
    def get(self, request: Request, slug: str) -> HttpResponse:
        event = _get_event(slug)
        _require_organizer(request.user, event)
        document = event_json(event)
        response = HttpResponse(document, content_type="application/json; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{event.slug}-event.json"'
        return response


class ImportView(APIView):
    """``POST /api/v1/imports`` — admin or host only.

    Upload a fixtures-shaped JSON body to import as a new event. If the
    requested slug is taken a numeric suffix is appended automatically.
    Returns the import report.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="import_fixture",
        summary="Import a fixtures-shaped JSON as a new event (admin/host).",
        responses={
            201: OpenApiResponse(description="Import report."),
            400: OpenApiResponse(description="Invalid fixture."),
            403: OpenApiResponse(description="Not an admin or host."),
            409: OpenApiResponse(description="Already imported or slug taken."),
        },
    )
    def post(self, request: Request) -> Response:
        user = request.user
        if not (getattr(user, "is_admin", False) or getattr(user, "is_host", False)):
            raise ApiError(
                "forbidden",
                "Only admins and hosts can import fixtures.",
                status_code=403,
            )
        # Accept either a JSON body or a file upload.
        if request.FILES.get("file"):
            try:
                raw = request.FILES["file"].read()
                data = json.loads(raw)
            except (ValueError, KeyError) as exc:
                raise ApiError("invalid_fixture", f"Could not parse uploaded file: {exc}", status_code=400)
        else:
            data = request.data
            if not isinstance(data, dict):
                raise ApiError(
                    "invalid_fixture",
                    "Request body must be a fixtures-shaped JSON object.",
                    status_code=400,
                )

        from interop.importer import import_fixture

        slug_base = (data.get("event") or {}).get("id") or "imported-event"
        # Sanitize and try slug suffixes on collision.
        import re
        from django.utils.text import slugify

        slug = slugify(slug_base)[:60] or "imported-event"
        from events.models import Event

        attempt = slug
        for suffix in range(1, 100):
            if not Event.objects.filter(slug=attempt).exists():
                break
            attempt = f"{slug}-{suffix}"
        else:
            attempt = f"{slug}-import"

        try:
            report = import_fixture(data, slug=attempt, actor=user)
        except ApiError:
            raise
        return Response(report.as_dict(), status=201)
