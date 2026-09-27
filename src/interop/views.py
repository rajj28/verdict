"""Read-only certificate, record, webhook, and embed pages."""
import json
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.clickjacking import xframe_options_exempt

from events.models import Event
from events.policy import is_organizer, visible_events
from interop import certificates, policy
from interop.signing import public_key_document, record_document
from projects.policy import filter_gallery, public_projects


def _event(slug):
    return get_object_or_404(visible_events(), slug=slug)


def manage_webhooks(request, slug):
    event = _event(slug)
    if not is_organizer(request.user, event):
        return HttpResponse("Forbidden", status=403)
    endpoints = policy.visible_endpoints(request.user, event)
    deliveries = policy.visible_deliveries(request.user, event)[:100]
    return render(request, "manage/webhooks.html", {
        "event": event, "endpoints": endpoints, "deliveries": deliveries,
    })


def manage_certificates(request, slug):
    event = _event(slug)
    if not is_organizer(request.user, event):
        return HttpResponse("Forbidden", status=403)
    entries = certificates.certificate_index(event)
    for item in entries:
        item["code"] = certificates.verification_code(event, item["kind"], item["public_id"])
        item["url"] = request.build_absolute_uri(
            f"/certificates/verify/{event.slug}/{item['kind']}/{item['public_id']}?code={item['code']}"
        )
    if request.GET.get("print") == "1":
        return render(request, "interop/certificates_print.html", {"event": event, "certificates": entries})
    records = policy.visible_records(request.user, event)
    return render(request, "manage/certificates.html", {
        "event": event, "certificates": entries, "records": records,
    })


def manage_embed(request, slug):
    event = _event(slug)
    if not is_organizer(request.user, event):
        return HttpResponse("Forbidden", status=403)
    script_url = request.build_absolute_uri(f"/embed/{event.slug}.js")
    iframe_url = request.build_absolute_uri(f"/embed/{event.slug}")
    snippet = f'<script src="{script_url}" defer></script>'
    return render(request, "manage/embed.html", {
        "event": event, "snippet": snippet, "iframe_url": iframe_url,
    })


def certificate_page(request, slug, kind, public_id):
    event = _event(slug)
    data = certificates.certificate_data(event, kind, public_id)
    if data is None:
        return HttpResponse("Certificate not found", status=404)
    from interop.services import certificate_access

    if not certificate_access(request.user, event, kind, data):
        return HttpResponse("Forbidden", status=403)
    code = certificates.verification_code(event, kind, public_id)
    return render(request, "interop/certificate.html", {
        "event": event,
        "kind": kind,
        "certificate": data,
        "verification_code": code,
        "verification_url": request.build_absolute_uri(
            f"/certificates/verify/{event.slug}/{kind}/{public_id}?code={code}"
        ),
        "code_valid": certificates.code_matches(code, request.GET.get("code", code)),
    })


def verify_certificate(request, slug, kind, public_id):
    event = _event(slug)
    valid = certificates.verify_code(event, kind, public_id, request.GET.get("code", ""))
    return JsonResponse({"valid": valid, "kind": kind})


def public_keys(request):
    return JsonResponse(public_key_document())


def judge_record(request, record_id):
    record = get_object_or_404(policy.visible_records(), record_id=record_id)
    document = record_document(record)
    if "application/json" in request.headers.get("Accept", "") or request.GET.get("format") == "json":
        return JsonResponse(document)
    return render(request, "interop/record.html", {
        "record": record, "document": json.dumps(document, indent=2),
    })


def verify_page(request):
    return render(request, "interop/verify.html")


@xframe_options_exempt
def embed_gallery(request, slug):
    event = _event(slug)
    if not event.gallery_public:
        return HttpResponse("Gallery is not public", status=404)
    projects = filter_gallery(
        public_projects(event), q=request.GET.get("q", ""), track=request.GET.get("track", "")
    ).prefetch_related("images")
    return render(request, "interop/embed.html", {
        "event": event,
        "projects": projects,
        "tracks": event.tracks.all(),
        "selected_track": request.GET.get("track", ""),
        "query": request.GET.get("q", ""),
    })


def embed_script(request, slug):
    _event(slug)
    path = Path(settings.BASE_DIR) / "static" / "js" / "embed.js"
    response = HttpResponse(path.read_text(encoding="utf-8"), content_type="application/javascript; charset=utf-8")
    response["Cache-Control"] = "public, max-age=300"
    return response
