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
from rest_framework import serializers

from core.schema import error_responses

TAGS = ["interop"]


# --- serializers ---------------------------------------------------------


class ImportRequestSerializer(serializers.Serializer):
    """A fixtures-shaped document, as an object.

    Field-by-field keys depend on the source document, so the body is documented
    as the free-form object it is; the import report is what says what happened.
    """

    event = serializers.DictField(
        required=False, help_text="Event document; its id seeds the slug."
    )
    users = serializers.ListField(child=serializers.DictField(), required=False)
    teams = serializers.ListField(child=serializers.DictField(), required=False)
    projects = serializers.ListField(child=serializers.DictField(), required=False)
    reviews = serializers.ListField(child=serializers.DictField(), required=False)
    assignments = serializers.ListField(child=serializers.DictField(), required=False)
    criteria = serializers.ListField(child=serializers.DictField(), required=False)


class ImportReportSerializer(serializers.Serializer):
    """What one import did. Compared by meaning, not only by counts."""

    source_id = serializers.CharField(help_text="Public id of the source document.")
    slug = serializers.CharField(help_text="Slug the event was created under; suffixed on collision.")
    counts = serializers.DictField(
        help_text="Row counts per table, so a caller can check meaning rather than totals."
    )
    superseded = serializers.ListField(
        child=serializers.DictField(), help_text="Objects replaced by this import."
    )
    constant_scorers = serializers.ListField(child=serializers.DictField())
    under_reviewed = serializers.ListField(child=serializers.DictField())
    notes = serializers.ListField(
        child=serializers.CharField(), help_text="Anything the importer refused or adjusted."
    )
    fixture_sha256 = serializers.CharField(
        help_text="Digest of the exact bytes imported, so the source can be identified later."
    )
    fixture_bytes = serializers.IntegerField()


class WebhookEndpointSerializer(serializers.Serializer):
    """A subscriber. The signing secret is never returned by the list or the delete."""

    public_id = serializers.CharField()
    url = serializers.CharField(help_text="Absolute https URL the deliveries are posted to.")
    event_types = serializers.ListField(
        child=serializers.CharField(),
        help_text="Subscribed action names, or ['*'] for everything emitted.",
    )
    is_active = serializers.BooleanField()


class WebhookEndpointListSerializer(serializers.Serializer):
    endpoints = WebhookEndpointSerializer(many=True)


class WebhookEndpointCreatedSerializer(serializers.Serializer):
    """The only time the signing secret is returned; store it now or not at all."""

    public_id = serializers.CharField()
    url = serializers.CharField()
    event_types = serializers.ListField(child=serializers.CharField())
    secret = serializers.CharField(
        help_text="HMAC signing secret. Shown once; deliveries carry "
                  "'X-Verdict-Signature' derived from it."
    )


class WebhookEndpointCreateSerializer(serializers.Serializer):
    url = serializers.CharField(
        help_text="Absolute https URL. Plain http, loopback, link-local and private ranges "
                  "are refused as unsafe_webhook_url."
    )
    event_types = serializers.ListField(
        child=serializers.CharField(), required=False,
        help_text="Action names to subscribe to. Defaults to every emitted event.",
    )


class WebhookEndpointDisabledSerializer(serializers.Serializer):
    public_id = serializers.CharField()
    is_active = serializers.BooleanField(
        help_text="Always false: an endpoint is disabled, never deleted, so its delivery "
                  "history stays auditable."
    )


class WebhookDeliveryAckSerializer(serializers.Serializer):
    delivery_id = serializers.CharField(help_text="Public id of the queued delivery.")
    status = serializers.CharField(
        help_text="'pending' now. Delivery happens on a worker with retries, leasing and "
                  "duplicate suppression; 202 means accepted, not delivered."
    )


class CertificateIndexRowSerializer(serializers.Serializer):
    kind = serializers.CharField(help_text="'participation', 'judge' or 'award'.")
    public_id = serializers.CharField()
    label = serializers.CharField(allow_blank=True)
    subject = serializers.CharField(allow_blank=True)
    people = serializers.ListField(child=serializers.CharField())
    detail = serializers.CharField(required=False, allow_blank=True)
    project = serializers.CharField(required=False, allow_blank=True)


class JudgeRecordsIssuedSerializer(serializers.Serializer):
    records = serializers.ListField(
        child=serializers.CharField(), help_text="Judge record ids that now exist."
    )
    count = serializers.IntegerField()


class JudgeRecordRevokedSerializer(serializers.Serializer):
    record_id = serializers.CharField()
    revoked = serializers.BooleanField(
        help_text="Always true. A revocation is recorded and verifiable; it does not "
                  "erase the record or the reviews behind it."
    )


class SignedRecordSerializer(serializers.Serializer):
    """One half of a verifiable record: the claims, and the signature over them."""

    record = serializers.DictField(
        help_text="kid, record_id, event and judge sub-objects, and issued_at."
    )
    signature = serializers.CharField(help_text="Base64 Ed25519 signature over the record.")


class RecordVerificationSerializer(serializers.Serializer):
    valid = serializers.BooleanField()
    reason = serializers.CharField(
        help_text="Why it failed, or 'Signature is valid.' This checks the signature "
                  "against the published key, not who issued the record."
    )


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
        tags=TAGS,
        responses={
            (200, "text/csv"): OpenApiTypes.STR,
            **error_responses(401, 403, 404),
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
        tags=TAGS,
        responses={
            (200, "application/json"): OpenApiTypes.STR,
            **error_responses(401, 403, 404),
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
        description="Admins and hosts only. Accepts a JSON body or a multipart file upload. "
                    "A taken slug gets a numeric suffix rather than overwriting anything. The "
                    "report records the sha256 of the exact bytes, so the same import can be "
                    "recognised later; an already-imported source is refused rather than "
                    "applied twice.",
        request={"application/json": ImportRequestSerializer},
        tags=TAGS,
        responses={201: ImportReportSerializer, **error_responses(400, 401, 403, 409, 413)},
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

    @extend_schema(
        operation_id="event_webhooks",
        summary="List the event's webhook subscribers.",
        description="Organizer only. Subscribers see only action names and URLs; a signing "
                    "secret is never returned here.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={200: WebhookEndpointListSerializer, **error_responses(401, 403, 404)},
        tags=TAGS,
    )
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

    @extend_schema(
        operation_id="event_webhook_subscribe",
        summary="Subscribe a URL to an event's webhooks.",
        description="Organizer only. The target is validated as a safe absolute https URL. "
                    "The signing secret is returned once here. Deliveries carry scoped "
                    "payloads only: no private score or judge note ever leaves the portal, "
                    "whatever the subscription says.",
        request=WebhookEndpointCreateSerializer,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={201: WebhookEndpointCreatedSerializer,
                   **error_responses(400, 401, 403, 404)},
        tags=TAGS,
    )
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

    @extend_schema(
        operation_id="event_webhook_unsubscribe",
        summary="Disable a webhook subscriber.",
        description="Organizer only. The endpoint is disabled rather than deleted, and its "
                    "still-pending deliveries are failed, so the subscription history is not "
                    "silently erased.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),

            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Webhook endpoint public id."),
        ],
        responses={200: WebhookEndpointDisabledSerializer,
                   **error_responses(401, 403, 404)},
        tags=TAGS,
    )
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

    @extend_schema(
        operation_id="event_webhook_test",
        summary="Queue a signed test delivery for one subscriber.",
        description="Organizer only. 202 means the delivery is queued, not that the "
                    "subscriber answered; read the delivery's status for that. A disabled "
                    "endpoint is 409 endpoint_disabled.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),

            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Webhook endpoint public id."),
        ],
        responses={202: WebhookDeliveryAckSerializer,
                   **error_responses(400, 401, 403, 404, 409)},
        tags=TAGS,
    )
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

    @extend_schema(
        operation_id="event_webhook_replay",
        summary="Re-queue one delivery that already went out.",
        description="Organizer only. A replay is a new delivery row against the same payload, "
                    "so a subscriber that deduplicates on the original id will ignore it; "
                    "nothing is rewritten in place.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),

            OpenApiParameter("public_id", str, OpenApiParameter.PATH,
                             description="Webhook delivery public id."),
        ],
        responses={202: WebhookDeliveryAckSerializer,
                   **error_responses(401, 403, 404)},
        tags=TAGS,
    )
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

    @extend_schema(
        operation_id="event_certificates_export",
        summary="Download the certificate index with per-row verification links.",
        description="Organizer only. Each row carries a verification code derived from the "
                    "HMAC secret, which proves the portal issued the link. It is not the same "
                    "thing as the Ed25519 signature on a judge record: a lost SECRET_KEY "
                    "breaks these links too, so they are not a durable trust anchor.",
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={200: OpenApiResponse(
                      response=CertificateIndexRowSerializer(many=True),
                      description="CSV file with kind, public_id, verification_code and url.",
                  ),
                  **error_responses(401, 403, 404)},
        tags=TAGS,
    )
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

    @extend_schema(
        operation_id="event_judge_records_issue",
        summary="Issue signed participation records for every judge who reviewed.",
        description="Organizer only, and only after judging has closed (403 judging_open "
                    "before that). Judges with no submitted review get no record. Re-issuing "
                    "refreshes the existing records rather than minting duplicates.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
        ],
        responses={200: JudgeRecordsIssuedSerializer,
                   **error_responses(401, 403, 404)},
        tags=TAGS,
    )
    def post(self, request: Request, slug: str) -> Response:
        event = _get_event(slug)
        rows = services.issue_judge_records(request.user, event)
        return Response({"records": [row.record_id for row in rows], "count": len(rows)})


class JudgeRecordRevokeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="event_judge_record_revoke",
        summary="Revoke one signed judge record.",
        description="Organizer only. The revocation is published in the key document, so a "
                    "holder of the record can check that it was revoked; the record itself and "
                    "the reviews behind it are kept.",
        request=None,
        parameters=[
            OpenApiParameter("slug", str, OpenApiParameter.PATH,
                             description="Event slug."),
            OpenApiParameter("record_id", str, OpenApiParameter.PATH,
                             description="Judge record id, as issued."),
        ],
        responses={200: JudgeRecordRevokedSerializer,
                   **error_responses(401, 403, 404)},
        tags=TAGS,
    )
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

    @extend_schema(
        operation_id="record_verify",
        summary="Verify a signed judge record against the published key.",
        description="Public and unauthenticated, so a third party can check a record offline. "
                    "The key is fetched locally: this makes no network call. It proves the "
                    "signature matches the published key and that the record is not revoked. "
                    "It does not prove who issued the record, and hashes alone would not "
                    "detect a privileged actor who rewrote both data and digest.",
        request=SignedRecordSerializer,
        tags=TAGS,
        responses={200: RecordVerificationSerializer, **error_responses(400, 415)},
    )
    def post(self, request: Request) -> Response:
        valid, reason = services.verify_submission(request.data)
        return Response({"valid": valid, "reason": reason})
