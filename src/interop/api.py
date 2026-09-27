"""Fixture import, JSON round trip and CSV exports.

DRF serializers and viewsets. Thin: validate, delegate to services, return the envelope.
"""
from __future__ import annotations

import json

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.errors import ApiError
from events.policy import get_event_by_slug, is_organizer
from interop import policy, services
from interop.exports import EXPORT_KINDS, event_json, export_csv
from interop.certificates import certificate_index, verification_code
from core.csvutil import write_csv


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


class WebhookCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        _require_organizer(request.user, event)
        rows = policy.visible_endpoints(request.user, event)
        return Response({"endpoints": [{
            "public_id": endpoint.public_id,
            "url": endpoint.url,
            "event_types": endpoint.event_types,
            "is_active": endpoint.is_active,
            "created_at": endpoint.created_at,
        } for endpoint in rows]})

    def post(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        body = request.data
        if not isinstance(body, dict):
            raise ApiError("invalid", "Request body must be an object.", status_code=400)
        endpoint, secret = services.create_endpoint(
            request.user, event, url=body.get("url", ""), event_types=body.get("event_types", ["*"]),
        )
        return Response({
            "public_id": endpoint.public_id,
            "url": endpoint.url,
            "event_types": endpoint.event_types,
            "secret": secret,
        }, status=201)


class WebhookEndpointDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request: Request, slug: str, public_id: str) -> Response:
        event = _get_event(slug)
        endpoint = policy.visible_endpoints(request.user, event).filter(public_id=public_id).first()
        if endpoint is None:
            if not is_organizer(request.user, event):
                _require_organizer(request.user, event)
            raise ApiError("not_found", "Webhook endpoint was not found.", status_code=404)
        endpoint = services.disable_endpoint(request.user, endpoint)
        return Response({"public_id": endpoint.public_id, "is_active": endpoint.is_active})


class WebhookEndpointTestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, slug: str, public_id: str) -> Response:
        event = _get_event(slug)
        endpoint = policy.visible_endpoints(request.user, event).filter(public_id=public_id).first()
        if endpoint is None:
            if not is_organizer(request.user, event):
                _require_organizer(request.user, event)
            raise ApiError("not_found", "Webhook endpoint was not found.", status_code=404)
        delivery = services.test_endpoint(request.user, endpoint)
        return Response({"delivery_id": delivery.public_id, "status": delivery.status}, status=202)


class WebhookDeliveryReplayView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, slug: str, public_id: str) -> Response:
        event = _get_event(slug)
        delivery = policy.visible_deliveries(request.user, event).filter(public_id=public_id).first()
        if delivery is None:
            if not is_organizer(request.user, event):
                _require_organizer(request.user, event)
            raise ApiError("not_found", "Webhook delivery was not found.", status_code=404)
        replay = services.replay(request.user, delivery)
        return Response({"delivery_id": replay.public_id, "status": replay.status}, status=202)


class CertificateExportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, slug: str) -> HttpResponse:
        event = _get_event(slug)
        _require_organizer(request.user, event)
        rows = []
        for item in certificate_index(event):
            code = verification_code(event, item["kind"], item["public_id"])
            url = request.build_absolute_uri(
                f"/certificates/verify/{event.slug}/{item['kind']}/{item['public_id']}?code={code}"
            )
            rows.append([item["kind"], item["public_id"], code, url])
        response = HttpResponse(write_csv(["kind", "public_id", "verification_code", "url"], rows),
                                content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{event.slug}-certificates.csv"'
        return response


class JudgeRecordBulkIssueView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        rows = services.issue_judge_records(request.user, event)
        return Response({"records": [row.record_id for row in rows], "count": len(rows)})


class JudgeRecordRevokeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request, slug: str, record_id: str) -> Response:
        event = _get_event(slug)
        record = policy.visible_records(request.user, event).filter(record_id=record_id).first()
        if record is None:
            if not is_organizer(request.user, event):
                _require_organizer(request.user, event)
            raise ApiError("not_found", "Judge record was not found.", status_code=404)
        record = services.revoke_judge_record(request.user, record)
        return Response({"record_id": record.record_id, "revoked": record.revoked_at is not None})


class RecordVerifyView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request: Request) -> Response:
        valid, reason = services.verify_submission(request.data)
        return Response({"valid": valid, "reason": reason})
