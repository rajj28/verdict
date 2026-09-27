"""Read-only voting pages; every write goes through the JSON API."""
import secrets

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from community import policy, services
from community.models import VotingAccess, VotingConfig
from core.clock import now
from core.errors import ApiError
from projects.policy import public_projects


def _event_or_404(slug: str):
    event = policy.visible_event(slug)
    if event is None:
        raise Http404("No such event.")
    return event


def voting_page(request, slug: str):
    event = _event_or_404(slug)
    config = get_object_or_404(VotingConfig, event=event)
    link_token = request.GET.get("v", "")
    device_token = request.COOKIES.get("verdict_voter", "")
    response = render(request, "community/ballot.html", {
        "event": event,
        "config": config,
        "projects": public_projects(event).order_by("title", "public_id"),
        "link_token": link_token if config.access == VotingAccess.OPEN_LINK else "",
        "email_ticket": request.GET.get("token", ""),
        "window_open": (
            event.voting_open_at is not None and event.voting_close_at is not None
            and event.voting_open_at <= now() < event.voting_close_at
        ),
        "can_manage": policy.can_manage_voting(request.user, event),
        "api_url": f"/api/v1/events/{event.slug}/votes/ballot",
        "results_url": f"/events/{event.slug}/voting/results",
    })
    if (config.access == VotingAccess.OPEN_LINK and link_token
            and services.secrets_compare(link_token, config.link_token)
            and not device_token):
        response.set_cookie(
            "verdict_voter", secrets.token_urlsafe(32), max_age=60 * 60 * 24 * 365,
            httponly=True, secure=request.is_secure(), samesite="Lax", path="/",
        )
    return response


def email_verification_page(request, slug: str):
    event = _event_or_404(slug)
    return render(request, "community/email_verify.html", {
        "event": event,
        "ticket": request.GET.get("token", ""),
        "api_url": f"/api/v1/events/{event.slug}/votes/email/verify",
        "results_url": f"/events/{event.slug}/voting/results",
    })


def voting_results_page(request, slug: str):
    event = _event_or_404(slug)
    allowed = policy.results_visible(request.user, event)
    if allowed:
        rows = services.tallies_for_event(request.user, event)
    else:
        rows = []
    return render(request, "community/results.html", {
        "event": event, "results": rows, "results_hidden": not allowed,
        "can_manage": policy.can_manage_voting(request.user, event),
    }, status=200 if allowed else 403)


def voting_management_page(request, slug: str):
    event = _event_or_404(slug)
    try:
        config = policy.visible_config(request.user, event)
    except ApiError as exc:
        raise PermissionDenied(exc.message) from exc
    ballots = policy.visible_ballots(request.user, event)
    flags = policy.visible_flags(request.user, event)
    audit_events = policy.visible_voting_audit(request.user, event)
    tallies = services.tallies_for_event(request.user, event)
    return render(request, "manage/voting.html", {
        "event": event,
        "results": tallies,
        "ballots": ballots,
        "flags": flags,
        "audit_events": audit_events,
        "export_url": f"/api/v1/events/{event.slug}/exports/votes.csv",
        "config": config,
        "config_api_url": f"/api/v1/events/{event.slug}/voting/config",
        "voting_link": (
            f"/events/{event.slug}/vote?v={config.link_token}"
            if config and config.access == VotingAccess.OPEN_LINK else ""
        ),
    })
