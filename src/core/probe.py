"""Rollback-only adversarial requests against VERDICT's real URL stack."""
from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client

import audit.services
from accounts.tokens import issue_token, revoke_token
from community.models import Ballot, VotingAccess, VotingConfig, VotingStyle
from core.clock import now
from events.models import CustomQuestion, Event, EventRole, Role, Track
from judging.models import Assignment, Review, Rubric
from projects.models import Answer, Project, ProjectStatus
from teams.models import Team, TeamInvite, TeamMember

User = get_user_model()

EXTENSION_REGISTRY = ("voting", "publication")


@dataclass(frozen=True)
class Attack:
    key: str
    area: str
    description: str
    method: str
    path: str
    actor: str
    expected: tuple[int, ...]
    why: str
    body: dict[str, Any] | None = None
    request_kind: str = "json"
    must_contain: str = ""
    must_not_contain: str = ""
    expected_detail: str = ""


class _RollbackProbe(Exception):
    """End the disposable fixture transaction without committing its rows."""


def _safe_path(path: str) -> str:
    """Keep the disposable invite capability out of audit and report records."""
    return re.sub(r"(/api/v1/invites/)[^/]+(/accept)", r"\1[temporary]\2", path)


def _user(email: str, label: str, *, is_admin: bool = False, is_host: bool = False):
    return User.objects.create_user(
        email=email, password=None, display_name=label, is_admin=is_admin, is_host=is_host
    )


def _event(slug: str, name: str, owner, at):
    closed = at - timedelta(days=1)
    return Event.objects.create(
        slug=slug,
        name=name,
        submissions_close_at=closed,
        judging_open_at=closed,
        judging_close_at=at - timedelta(minutes=1),
        shrinkage_lambda=2.0,
        created_by=owner,
        voting_open_at=at - timedelta(minutes=1),
        voting_close_at=at + timedelta(minutes=1),
    )


def _role(event, user, role: str, public_id: str, tracks=()):
    row = EventRole.objects.create(event=event, user=user, role=role, public_id=public_id)
    if tracks:
        row.tracks.set(tracks)
    return row


def _project(event, team, track, public_id, title, status, *, thumbnail=""):
    return Project.objects.create(
        event=event,
        team=team,
        track=track,
        public_id=public_id,
        title=title,
        summary="Probe project",
        description="Disposable probe project",
        repo_url="https://example.invalid/probe",
        status=status,
        thumbnail=thumbnail,
        first_submitted_at=now() if status == ProjectStatus.SUBMITTED else None,
        last_submitted_at=now() if status == ProjectStatus.SUBMITTED else None,
    )


def _fixture():
    """Build independent event actors and target rows inside the rollback."""
    at = now()
    admin = _user("integrity-admin@probe.invalid", "Probe admin", is_admin=True)
    organizer_a = _user("integrity-org-a@probe.invalid", "Organizer A", is_host=True)
    organizer_b = _user("integrity-org-b@probe.invalid", "Organizer B", is_host=True)
    participant = _user("integrity-participant@probe.invalid", "Participant")
    draft_owner = _user("integrity-draft-owner@probe.invalid", "Draft owner")
    joiner = _user("integrity-joiner@probe.invalid", "Joiner")
    judge_a = _user("integrity-judge-a@probe.invalid", "Judge A")
    judge_b = _user("integrity-judge-b@probe.invalid", "Judge B")

    event_a = _event(f"probe-a-{secrets.token_hex(5)}", "Probe Event A", admin, at)
    event_b = _event(f"probe-b-{secrets.token_hex(5)}", "Probe Event B", admin, at)
    VotingConfig.objects.create(
        event=event_a, access=VotingAccess.AUTHENTICATED,
        style=VotingStyle.QUADRATIC, credits=16,
    )
    track_a = Track.objects.create(event=event_a, name="Track A", position=0)
    track_b = Track.objects.create(event=event_a, name="Track B", position=1)
    track_b_event = Track.objects.create(event=event_b, name="Track B", position=0)
    _role(event_a, organizer_a, Role.ORGANIZER, "org_probe_a")
    _role(event_b, organizer_b, Role.ORGANIZER, "org_probe_b")
    _role(event_a, participant, Role.PARTICIPANT, "par_probe_a")
    _role(event_a, draft_owner, Role.PARTICIPANT, "par_probe_draft")
    _role(event_a, joiner, Role.PARTICIPANT, "par_probe_joiner")
    judge_a_role = _role(event_a, judge_a, Role.JUDGE, "jdg_probe_a", [track_a])
    judge_b_role = _role(event_a, judge_b, Role.JUDGE, "jdg_probe_b", [track_b])
    judge_b_event_role = _role(event_b, judge_b, Role.JUDGE, "jdg_probe_event_b",
                               [track_b_event])

    team_a = Team.objects.create(event=event_a, name="Participant team", created_by=participant)
    TeamMember.objects.create(team=team_a, user=participant, event=event_a, is_owner=True)
    team_draft_owner = Team.objects.create(event=event_a, name="Private draft team",
                                           created_by=draft_owner)
    TeamMember.objects.create(team=team_draft_owner, user=draft_owner, event=event_a, is_owner=True)
    assigned = _project(event_a, team_a, track_a, "prj_probe_a", "=1+1",
                        ProjectStatus.SUBMITTED)
    team_other_track = Team.objects.create(event=event_a, name="Other track team",
                                           created_by=organizer_b)
    other_track = _project(event_a, team_other_track, track_b, "prj_probe_track_b", "Other track",
                           ProjectStatus.SUBMITTED)
    team_unassigned = Team.objects.create(event=event_a, name="Unassigned team",
                                          created_by=organizer_a)
    unassigned = _project(event_a, team_unassigned, track_a, "prj_probe_unassigned", "Unassigned",
                          ProjectStatus.SUBMITTED)
    private_draft = _project(event_a, team_draft_owner, track_a, "prj_probe_draft",
                             "Private draft", ProjectStatus.DRAFT,
                             thumbnail="probe/private-draft.png")
    event_b_team = Team.objects.create(event=event_b, name="Event B team", created_by=organizer_b)
    event_b_project = _project(event_b, event_b_team, track_b_event, "prj_probe_event_b",
                               "Event B project", ProjectStatus.SUBMITTED)
    question = CustomQuestion.objects.create(
        event=event_a, prompt="Private probe answer", is_public=False, required=False
    )
    Answer.objects.create(project=unassigned, question=question, value="secret-probe-answer")
    assignment_a = Assignment.objects.create(event=event_a, judge=judge_a_role, project=assigned)
    Assignment.objects.create(event=event_a, judge=judge_b_role, project=other_track)
    assignment_event_b = Assignment.objects.create(
        event=event_b, judge=EventRole.objects.get(event=event_b, user=judge_b), project=event_b_project
    )
    Review.objects.create(event=event_a, assignment=assignment_a, judge=judge_a_role,
                          project=assigned, status="draft")
    Review.objects.create(event=event_b, assignment=assignment_event_b,
                          judge=EventRole.objects.get(event=event_b, user=judge_b),
                          project=event_b_project, status="draft")
    Rubric.objects.create(event=event_a)
    invite = TeamInvite.objects.create(
        team=team_a, created_by=participant, expires_at=at + timedelta(days=1)
    )

    return {
        "admin": admin,
        "organizer": organizer_a,
        "organizer_other": organizer_b,
        "participant": participant,
        "joiner": joiner,
        "judge": judge_a,
        "judge_other": judge_b,
        "event": event_a,
        "other_event": event_b,
        "track": track_a,
        "assigned": assigned,
        "other_track": other_track,
        "unassigned": unassigned,
        "draft": private_draft,
        "event_b_project": event_b_project,
        "question": question,
        "invite": invite,
        "judge_a_role": judge_a_role,
        "judge_b_role": judge_b_role,
        "judge_b_event_role": judge_b_event_role,
    }


def _attacks(f):
    event = f["event"].slug
    other_event = f["other_event"].slug
    project = f["assigned"].public_id
    draft = f["draft"].public_id
    judge_b_event_id = f["judge_b_event_role"].public_id
    judge_b = f["judge_b_role"].public_id
    invite = f["invite"].token
    question = f["question"].public_id
    project_write = {
        "title": "Probe write",
        "summary": "After close",
        "description": "Must not persist",
        "track": f["track"].public_id,
        "repo_url": "https://example.invalid/probe",
    }
    cases = [
        Attack("peer-scores-query", "JUDGING", "Judge cannot select a peer with ?judge=",
               "GET", f"/api/v1/judge/scores?judge={judge_b}", "judge", (403,),
               "The score query parameter may select only the caller's own judge role."),
        Attack("peer-scores-path", "JUDGING", "Judge cannot read a peer score path",
               "GET", f"/api/v1/events/{event}/judges/{judge_b}/scores", "judge", (403,),
               "Per-judge score routes allow only the judge themself or an event organizer."),
        Attack("unassigned-review-read", "JUDGING", "Unassigned project review is not readable",
               "GET", f"/api/v1/events/{event}/judge/reviews/{f['unassigned'].public_id}",
               "judge", (403,), "Private reviews require an active assignment."),
        Attack("unassigned-review-write", "JUDGING", "Unassigned project review cannot be written",
               "PUT", f"/api/v1/events/{event}/judge/reviews/{f['unassigned'].public_id}",
               "judge", (403,), "Review writes require the caller's project assignment.",
               {"scores": {}, "comment": "probe"}),
        Attack("other-track-review-read", "JUDGING", "Other-track review is not readable",
               "GET", f"/api/v1/events/{event}/judge/reviews/{f['other_track'].public_id}",
               "judge", (403,), "A judge cannot read projects assigned outside their track."),
        Attack("other-track-review-write", "JUDGING", "Other-track review cannot be written",
               "PUT", f"/api/v1/events/{event}/judge/reviews/{f['other_track'].public_id}",
               "judge", (403,), "A judge cannot write projects assigned outside their track.",
               {"scores": {}, "comment": "probe"}),
        Attack("judging-closed-write", "JUDGING", "Closed judging rejects review submission",
               "POST", f"/api/v1/events/{event}/judge/reviews/{project}/submit",
               "judge", (403,), "The judging window is closed."),
        Attack("participant-create-after-deadline", "SUBMISSIONS",
               "Participant cannot create a project after submissions close",
               "POST", f"/api/v1/events/{event}/projects", "participant", (403,),
               "Project creation is refused after the submission deadline.", project_write),
        Attack("participant-update-after-deadline", "SUBMISSIONS",
               "Participant cannot edit a draft after submissions close",
               "PATCH", f"/api/v1/events/{event}/projects/{f['assigned'].public_id}",
               "participant", (403,), "Project edits are refused after the submission deadline.",
               {"title": "Changed after close"}),
        Attack("participant-image-after-deadline", "SUBMISSIONS",
               "Participant cannot add an image after submissions close",
               "POST", f"/api/v1/events/{event}/projects/{f['assigned'].public_id}/images",
               "participant", (403,), "Image changes use the same closed submission window.",
               request_kind="image"),
        Attack("participant-answer-after-deadline", "SUBMISSIONS",
               "Participant cannot change answers after submissions close",
               "PUT", f"/api/v1/events/{event}/projects/{f['assigned'].public_id}/answers",
               "participant", (403,), "Answer changes use the same closed submission window.",
               {question: "changed after close"}),
        Attack("participant-join-after-deadline", "SUBMISSIONS",
               "Invite cannot add a team member after submissions close",
               "POST", f"/api/v1/invites/{invite}/accept", "joiner", (403,),
               "Team join is refused after the event submission deadline.", {}),
        Attack("other-team-draft-by-id", "PROJECTS", "Participant cannot read another team's draft",
               "GET", f"/api/v1/events/{event}/projects/{draft}", "participant", (404,),
               "A draft is hidden from users outside its team and event organizers."),
        Attack("draft-media-by-url", "MEDIA", "Private draft media is not served to another team",
               "GET", "/media/probe/private-draft.png", "participant", (404,),
               "Media access is authorized through the owning project's visibility policy."),
        Attack("foreign-organizer-review-read", "EVENT ISOLATION",
               "Organizer cannot read another event's review",
               "GET", f"/api/v1/events/{other_event}/judges/{judge_b_event_id}/scores",
               "organizer", (403,), "Organizer permissions are scoped to their own event."),
        Attack("foreign-organizer-export", "EVENT ISOLATION",
               "Organizer cannot export another event's reviews",
               "GET", f"/api/v1/events/{other_event}/exports/reviews.csv",
               "organizer", (403,), "CSV export permissions are scoped to the event."),
        Attack("judge-csv-export", "EXPORTS", "Judge cannot export organizer CSV data",
               "GET", f"/api/v1/events/{event}/exports/reviews.csv", "judge", (403,),
               "CSV exports are organizer-only."),
        Attack("participant-csv-export", "EXPORTS", "Participant cannot export organizer CSV data",
               "GET", f"/api/v1/events/{event}/exports/reviews.csv", "participant", (403,),
               "CSV exports are organizer-only."),
        Attack("anonymous-api", "AUTH", "Anonymous judge API request requires authentication",
               "GET", "/api/v1/judge/scores", "anonymous", (401,),
               "Private API data requires authentication."),
        Attack("session-post-without-csrf", "AUTH", "Session POST without CSRF is rejected",
               "POST", "/api/v1/me/tokens", "session", (403,),
               "Session-authenticated state changes require a CSRF token.",
               {"name": "probe csrf"}),
        Attack("revoked-token-reuse", "AUTH", "A revoked temporary bearer token is rejected",
               "GET", "/api/v1/me", "revoked", (401,),
               "Revoked API credentials cannot authenticate."),
        Attack("csv-formula-injection", "EXPORTS", "CSV formula payload is neutralised",
               "GET", f"/api/v1/events/{event}/exports/projects.csv", "organizer", (200,),
               "Spreadsheet formula prefixes must be rendered as inert text.",
               must_contain="'=1+1", must_not_contain=",=1+1,",
               expected_detail="200 with formula cell quoted"),
        Attack("voting-results-hidden", "VOTING",
               "Non-organizer cannot read live voting results",
               "GET", f"/api/v1/events/{event}/voting/results", "participant", (403,),
               "Voting results remain hidden until the voting window closes.",
               must_contain="results_hidden"),
        Attack("second-ballot-same-identity", "VOTING",
               "The same authenticated voter cannot create a second ballot",
               "POST", f"/api/v1/events/{event}/votes/ballot", "participant", (409,),
               "One ballot is allowed per authenticated identity.",
               request_kind="duplicate_ballot", must_contain="duplicate_voter"),
        Attack("quadratic-ballot-over-budget", "VOTING",
               "A quadratic ballot over its credit budget is refused",
               "PUT", f"/api/v1/events/{event}/votes/ballot/[temporary]",
               "participant", (400,),
               "Ballot cost is recomputed and capped by the configured budget.",
               request_kind="quadratic_over_budget", must_contain="budget_exceeded"),
    ]
    return cases


def _perform(case: Attack, clients, tokens):
    if case.actor == "session":
        client = _client(enforce_csrf_checks=True)
        client.force_login(clients["participant_user"])
        headers = {}
    else:
        client = clients.get(case.actor) or _client()
        token = tokens.get(case.actor)
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    if case.request_kind == "duplicate_ballot":
        url = f"/api/v1/events/{case.path.split('/events/', 1)[1].split('/')[0]}/votes/ballot"
        data = json.dumps({})
        client.post(url, data=data, content_type="application/json", **headers)
        return client.post(url, data=data, content_type="application/json", **headers)
    if case.request_kind == "quadratic_over_budget":
        ballot = Ballot.objects.get(
            voter__event__slug=case.path.split("/events/", 1)[1].split("/", 1)[0],
            voter__user=clients["participant_user"],
        )
        url = f"/api/v1/events/{ballot.voter.event.slug}/votes/ballot/{ballot.public_id}"
        project = Project.objects.filter(event=ballot.voter.event, status=ProjectStatus.SUBMITTED).first()
        data = json.dumps({"items": [{"project": project.public_id, "votes": 5}]})
        return client.put(url, data=data, content_type="application/json", **headers)
    if case.request_kind == "image":
        return client.post(
            case.path,
            data={"image": SimpleUploadedFile("probe.png", b"")},
            **headers,
        )
    if case.method == "GET":
        return client.get(case.path, **headers)
    data = json.dumps(case.body if case.body is not None else {})
    return getattr(client, case.method.lower())(
        case.path, data=data, content_type="application/json", **headers
    )


def _client(*, enforce_csrf_checks=False):
    """Use a configured host so production-like ALLOWED_HOSTS still test routes."""
    allowed = settings.ALLOWED_HOSTS
    host = allowed[0] if allowed and allowed[0] != "*" else "localhost"
    if host.startswith("."):
        host = f"probe{host}"
    return Client(HTTP_HOST=host, enforce_csrf_checks=enforce_csrf_checks)


def _render_case(case: Attack, response):
    text = response.content.decode("utf-8", "replace")
    passed = response.status_code in case.expected
    details = [str(response.status_code)]
    if case.must_contain:
        passed = passed and case.must_contain in text
        details.append("formula neutralised" if case.must_contain in text else "unsafe cell missing")
    if case.must_not_contain:
        passed = passed and case.must_not_contain not in text
        details.append("no raw formula" if case.must_not_contain not in text else "raw formula present")
    expectation = case.expected_detail or " or ".join(map(str, case.expected))
    return {
        "key": case.key,
        "area": case.area,
        "description": case.description,
        "method": case.method,
        "path": _safe_path(case.path),
        "actor": case.actor,
        "expected": expectation,
        "actual": "; ".join(details),
        "status_code": response.status_code,
        "passed": passed,
        "reason": case.why if passed else f"Leak or unexpected response: {case.why}",
    }


def run_probe(*, actor=None, event=None) -> dict:
    """Run HTTP attacks against real routing, then roll back every fixture row."""
    report = {"cases": [], "event": event.slug if event else "", "extensions": list(EXTENSION_REGISTRY)}
    try:
        with transaction.atomic():
            fixture = _fixture()
            users = {
                "admin": fixture["admin"],
                "organizer": fixture["organizer"],
                "organizer_other": fixture["organizer_other"],
                "participant": fixture["participant"],
                "joiner": fixture["joiner"],
                "judge": fixture["judge"],
                "judge_other": fixture["judge_other"],
            }
            tokens = {}
            clients = {}
            issued = []
            try:
                for role, user in users.items():
                    token_row, plaintext = issue_token(user, f"Integrity probe {role}")
                    tokens[role] = plaintext
                    issued.append(token_row)
                    clients[role] = _client()
                revoked_row, revoked_plaintext = issue_token(
                    fixture["participant"], "Integrity probe revoked token"
                )
                tokens["revoked"] = revoked_plaintext
                issued.append(revoked_row)
                revoke_token(revoked_row)
                clients["participant_user"] = fixture["participant"]
                request_logger = logging.getLogger("django.request")
                original_disabled = request_logger.disabled
                request_logger.disabled = True
                try:
                    for case in _attacks(fixture):
                        response = _perform(case, clients, tokens)
                        report["cases"].append(_render_case(case, response))
                finally:
                    request_logger.disabled = original_disabled
            finally:
                for token_row in issued:
                    if token_row.revoked_at is None:
                        revoke_token(token_row)
            raise _RollbackProbe()
    except _RollbackProbe:
        pass
    finally:
        passed = sum(1 for case in report["cases"] if case["passed"])
        report["passed"] = passed
        report["total"] = len(report["cases"])
        report["ok"] = passed == len(report["cases"])
        report["summary"] = f"{passed}/{len(report['cases'])} attacks refused"
        audit.services.record(
            actor,
            "integrity.probe_run",
            event=event,
            summary=f"Integrity probe completed: {report['summary']}.",
            data={
                "passed": report["passed"],
                "total": report["total"],
                "ok": report["ok"],
                "cases": report["cases"],
                "extensions": report["extensions"],
            },
        )
    return report
