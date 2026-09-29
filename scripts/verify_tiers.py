#!/usr/bin/env python3
"""Exercise T1/T2/T3/T4 depth using .dogfood.toml tokens and disposable events.

The script uses only the standard library (urllib, json, tomllib with the
run.py fallback parser, http.server, threading, hmac, hashlib). Every event it
creates is disposable and clearly named; the seeded fixture event is only read,
never modified.

Output contract: under each ``TIER  label ..... PASS|FAIL`` line, one
indented line per request in the form
``METHOD path  as <actor>  -> status``. Query strings are never printed, so
capabilities (voting link tokens, email tickets) and bearer tokens never leak
into the report. The process always exits 0 (report-only contract).
"""
import argparse
import hashlib
import hmac
import json
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None


def parse_toml(text):
    """Read the simple TOML subset used by .dogfood.toml."""
    data, section = {}, None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        header = re.fullmatch(r"\[([A-Za-z0-9_.]+)\]", line)
        if header:
            section = data.setdefault(header.group(1), {})
            continue
        key, sep, value = line.partition("=")
        if not sep or section is None:
            continue
        value = value.strip()
        section[key.strip()] = (
            re.findall(r'"([^"]*)"', value)
            if value.startswith("[")
            else value.strip('"').strip("'")
        )
    return data


def load_config(path):
    if tomllib:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    return parse_toml(path.read_text(encoding="utf-8"))


def call(base, path, header=None, method="GET", body=None, opener=None,
         extra_headers=None):
    """One HTTP request. Returns (status, text, headers dict)."""
    req = urllib.request.Request(base + path, method=method)
    if header:
        name, _, value = header.partition(":")
        req.add_header(name.strip(), value.strip())
    for name, value in (extra_headers or {}).items():
        req.add_header(name, value)
    if body is not None:
        req.data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    opener = opener or urllib.request.build_opener()
    try:
        with opener.open(req, timeout=10) as response:
            return (response.status, response.read().decode("utf-8", "replace"),
                    dict(response.headers.items()))
    except urllib.error.HTTPError as error:
        try:
            text = error.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            text = ""
        return error.code, text, dict(error.headers.items() or {})
    except (OSError, ValueError) as error:
        return 0, f"{type(error).__name__}: {error}", {}


def js(text):
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def iso_in(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + seconds))


# With three projects an independent shuffle matches another ballot 1 time in 6,
# so one collision is luck, not determinism. Comparing up to seven independent
# voters keeps a portal that gives everyone the same order failing every time,
# while chance alone fails about 1 run in 280,000 instead of 1 in 36.
SHUFFLE_VOTERS = 7


def shuffle_differs(reference, orders, limit=SHUFFLE_VOTERS):
    """Return (differs, compared) for lazily produced ballot orders.

    Stops at the first order that differs from ``reference``, so later voters
    are only created when earlier ones happened to match. An empty order means
    that voter could not open a ballot, which fails the check.
    """
    compared = 0
    for order in orders:
        compared += 1
        if not order:
            return False, compared
        if order != reference:
            return True, compared
        if compared >= limit:
            break
    return False, compared


class Evidence:
    def __init__(self, base, auth):
        self.base = base
        self.auth = auth
        self.rows = []
        self.pending = []
        self.skipped = []

    def req(self, actor, method, path, status):
        logged = path.split("?", 1)[0]
        self.pending.append(f"  {method} {logged}  as {actor}  -> {status}")

    def check(self, tier, label, status, wanted, detail="", contains=""):
        ok = status in wanted and (not contains or contains in detail)
        self.rows.append((tier, label, ok, status, wanted, detail))
        print(f"{tier}  {label} ..... {'PASS' if ok else 'FAIL'}", flush=True)
        for line in self.pending:
            print(line, flush=True)
        self.pending = []
        if not ok:
            print(f"  wanted {wanted}, got {status or 'no response'} {detail[:300]}",
                  flush=True)
        return ok

    def skip(self, tier, label, reason):
        """A check this server's configuration does not allow; never counted as passed."""
        self.skipped.append((tier, label, reason))
        print(f"{tier}  {label} ..... SKIPPED", flush=True)
        for line in self.pending:
            print(line, flush=True)
        self.pending = []
        print(f"  reason: {reason}", flush=True)

    def request(self, actor, method, path, header=None, body=None, opener=None,
                extra_headers=None):
        status, text, headers = call(
            self.base, path, header=header, method=method, body=body,
            opener=opener, extra_headers=extra_headers,
        )
        self.req(actor, method, path, status)
        return status, text, headers


def new_opener():
    jar = CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def csrf_token(jar):
    for cookie in jar:
        if cookie.name == "csrftoken":
            return cookie.value
    return ""


def register_session(evidence, base, tag, unique):
    """Register a fresh account; returns (opener, csrf, email, ok).

    The opener carries the login session, so later HTML reads prove what a
    signed-in non-owner sees. The password never leaves this function.
    """
    opener, jar = new_opener()
    status, _, _ = call(base, "/register", opener=opener)
    evidence.req(tag, "GET", "/register", status)
    token = csrf_token(jar)
    email = f"tier-evidence-{tag}-{unique}@example.invalid"
    password = f"Evidence-{unique}-Pass9!"
    status, _body, _ = call(
        base, "/api/v1/auth/register", method="POST",
        body={"email": email, "password": password, "display_name": f"Evidence {tag}"},
        opener=opener, extra_headers={"X-CSRFToken": token or "missing"},
    )
    evidence.req(tag, "POST", "/api/v1/auth/register", status)
    return opener, csrf_token(jar), email, status == 201


def session_post(evidence, base, actor, opener, csrf, path, body):
    return evidence.request(actor, "POST", path, body=body, opener=opener,
                            extra_headers={"X-CSRFToken": csrf})


def make_voting_event(evidence, base, auth, tag, unique, access, style="quadratic",
                      credits=16, max_votes=10):
    """Create a disposable event with an open voting window and a track.

    Returns (slug, track_id) or (None, None). Requests stay in the evidence
    buffer so they print under the next check.
    """
    name = f"Tier Evidence {tag} {unique}"
    status, text, _ = evidence.request(
        "organizer", "POST", "/api/v1/events", header=auth.get("organizer"),
        body={"name": name,
              "submissions_open_at": None,
              "submissions_close_at": iso_in(7 * 86400),
              "judging_open_at": iso_in(7 * 86400)},
    )
    created = js(text)
    slug = created.get("slug", "")
    if status != 201 or not slug:
        evidence.check("T3", f"{tag} setup creates event", status, (201,), text)
        return None, None
    status, text, _ = evidence.request(
        "organizer", "POST", f"/api/v1/events/{slug}/tracks",
        header=auth.get("organizer"),
        body={"name": "Evidence Track", "position": 0},
    )
    track_id = js(text).get("public_id", "")
    if status != 201 or not track_id:
        evidence.check("T3", f"{tag} setup creates track", status, (201,), text)
        return None, None
    status, text, _ = evidence.request(
        "organizer", "PATCH", f"/api/v1/events/{slug}/voting/config",
        header=auth.get("organizer"),
        body={"access": access, "style": style, "credits": credits,
              "max_votes_per_project": max_votes,
              "voting_open_at": iso_in(-3600),
              "voting_close_at": iso_in(6 * 3600)},
    )
    if status != 200:
        evidence.check("T3", f"{tag} setup configures voting", status, (200,), text)
        return None, None
    return slug, track_id


def submit_project_as(evidence, base, actor, header, slug, track_id, title,
                      opener=None, csrf="", team_name=""):
    """Create a team, then create and submit one project. Returns public id."""
    team_body = {"name": team_name or f"{title} team"}
    if opener is not None:
        status, _, _ = evidence.request(
            actor, "POST", f"/api/v1/events/{slug}/teams", body=team_body,
            opener=opener, extra_headers={"X-CSRFToken": csrf})
    else:
        status, _, _ = evidence.request(
            actor, "POST", f"/api/v1/events/{slug}/teams",
            header=header, body=team_body)
    if status not in (201, 409):
        return ""
    body = {"title": title, "summary": "Tier evidence entry",
            "description": "Disposable project created by verify_tiers.py.",
            "repo_url": "https://example.invalid/evidence", "track": track_id}
    if opener is not None:
        status, text, _ = evidence.request(
            actor, "POST", f"/api/v1/events/{slug}/projects", body=body,
            opener=opener, extra_headers={"X-CSRFToken": csrf})
    else:
        status, text, _ = evidence.request(
            actor, "POST", f"/api/v1/events/{slug}/projects",
            header=header, body=body)
    project_id = js(text).get("public_id", "")
    if status != 201 or not project_id:
        return ""
    if opener is not None:
        status, _, _ = evidence.request(
            actor, "POST",
            f"/api/v1/events/{slug}/projects/{project_id}/submit", body={},
            opener=opener, extra_headers={"X-CSRFToken": csrf})
    else:
        status, _, _ = evidence.request(
            actor, "POST",
            f"/api/v1/events/{slug}/projects/{project_id}/submit",
            header=header, body={})
    return project_id if status == 200 else ""


class WebhookSink:
    """A tiny stdlib receiver proving a signed delivery actually arrives."""

    def __init__(self):
        self.state = {"secret": "", "hits": []}
        self.server = None
        self.thread = None

    def start(self):
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(min(length, 1_000_000))
                secret = state["secret"].encode("utf-8")
                expected = ("sha256=" + hmac.new(secret, body,
                                                 hashlib.sha256).hexdigest()
                            if secret else "missing")
                supplied = self.headers.get("X-Verdict-Signature", "")
                valid = bool(secret) and hmac.compare_digest(expected, supplied)
                state["hits"].append({
                    "path": self.path,
                    "event": self.headers.get("X-Verdict-Event", ""),
                    "delivery": self.headers.get("X-Verdict-Delivery", ""),
                    "signature": supplied,
                    "valid_signature": valid,
                    "payload": body.decode("utf-8", "replace"),
                })
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}/hooks"

    def wait_for_hit(self, timeout=25.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.state["hits"]:
                return self.state["hits"][0]
            time.sleep(0.5)
        return None

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            self.server = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", default=".dogfood.toml")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    base = cfg["portal"]["base_url"].rstrip("/")
    auth = cfg.get("auth", {})
    routes = cfg.get("routes", {})
    evidence = Evidence(base, auth)

    gallery_path = routes.get("gallery", "/projects")
    status, gallery, _ = evidence.request("visitor", "GET", gallery_path)
    evidence.check("T1", "public gallery", status, (200,), gallery)
    status, search, _ = evidence.request(
        "visitor", "GET", "/api/v1/projects?q=Glass")
    evidence.check("T1", "gallery search returns matching projects", status,
                   (200,), search, contains="Glass Signal")

    peer = routes.get("peer_scores", "")
    match = re.search(r"/events/([^/]+)/judges/([^/]+)/scores", peer)
    event_slug = match.group(1) if match else "sample-hack-2026"

    # Exercise the whole disposable T1 lifecycle through public API writes.
    unique_suffix = secrets.token_hex(3)
    unique = f"{int(time.time())}-{unique_suffix}"
    event_body = {
        "name": f"Tier Evidence {unique}",
        "submissions_open_at": None,
        "submissions_close_at": iso_in(86400),
        "judging_open_at": iso_in(86400),
    }
    status, text, _ = evidence.request(
        "organizer", "POST", "/api/v1/events",
        header=auth.get("organizer"), body=event_body)
    created = None
    if status == 201:
        try:
            created = json.loads(text)
        except json.JSONDecodeError:
            created = None
    evidence.check("T1", "create disposable verification event", status, (201,),
                   text)
    if created:
        slug = created["slug"]
        status, text, _ = evidence.request(
            "organizer", "POST", f"/api/v1/events/{slug}/tracks",
            header=auth.get("organizer"),
            body={"name": "Evidence Track", "position": 0})
        try:
            track_id = json.loads(text).get("public_id", "")
        except json.JSONDecodeError:
            track_id = ""
        evidence.check("T1", "create event track", status, (201,), text)
        status, text, _ = evidence.request(
            "participant", "POST", f"/api/v1/events/{slug}/teams",
            header=auth.get("participant"), body={"name": "Evidence Team"})
        try:
            team = json.loads(text).get("public_id", "")
        except json.JSONDecodeError:
            team = ""
        evidence.check("T1", "participant creates a team", status, (201,), text)
        status, text, _ = evidence.request(
            "participant", "POST", f"/api/v1/events/{slug}/projects",
            header=auth.get("participant"),
            body={"title": "Evidence draft", "summary": "T1 proof",
                  "description": "Disposable",
                  "repo_url": "https://example.invalid/evidence",
                  "track": track_id},
        )
        try:
            project_id = json.loads(text).get("public_id", "")
        except json.JSONDecodeError:
            project_id = ""
        evidence.check("T1", "create project draft", status, (201,), text)
        if project_id:
            status, text, _ = evidence.request(
                "participant", "POST",
                f"/api/v1/events/{slug}/projects/{project_id}/submit",
                header=auth.get("participant"), body={},
            )
            evidence.check("T1", "submit project draft", status, (200,), text)
            status, text, _ = evidence.request(
                "participant", "PATCH",
                f"/api/v1/events/{slug}/projects/{project_id}",
                header=auth.get("participant"), body={"title": "Evidence edited"},
            )
            evidence.check("T1", "edit submitted project", status, (200,), text)
            # The second registered person is required for a complete invite acceptance.
            opener, jar = new_opener()
            status, page, _ = evidence.request("visitor", "GET", "/register",
                                               opener=opener)
            csrf_cookie = csrf_token(jar)
            if status == 200 and csrf_cookie:
                password = f"Evidence-{unique}-Pass9!"
                email = f"evidence-{unique}@example.invalid"
                status, text, _ = evidence.request(
                    "joiner", "POST", "/api/v1/auth/register",
                    body={"email": email, "password": password,
                          "display_name": "Evidence Joiner"},
                    opener=opener,
                    extra_headers={"X-CSRFToken": csrf_cookie},
                )
                registered = status == 201
            else:
                registered = False
                text = page
            if registered and team:
                status, invite_text, _ = evidence.request(
                    "participant", "POST",
                    f"/api/v1/events/{slug}/teams/{team}/invite",
                    header=auth.get("participant"), body={},
                )
                try:
                    invite_url = json.loads(invite_text).get("url", "")
                    invite_token = invite_url.partition("token=")[2]
                except json.JSONDecodeError:
                    invite_token = ""
                evidence.check("T1", "create invite link", status, (201,),
                               invite_text)
                if invite_token:
                    # Registration rotates the CSRF secret; use the latest cookie.
                    csrf_cookie = csrf_token(jar) or csrf_cookie
                    status, text, _ = evidence.request(
                        "joiner", "POST",
                        f"/api/v1/invites/{invite_token}/accept", body={},
                        opener=opener,
                        extra_headers={"X-CSRFToken": csrf_cookie},
                    )
                    evidence.check("T1", "invite-link join", status, (201,), text)
            else:
                evidence.check("T1", "invite-link join", 0, (201,),
                               "second account unavailable")
            status, text, _ = evidence.request(
                "organizer", "POST", f"/api/v1/events/{slug}/close-submissions",
                header=auth.get("organizer"), body={},
            )
            evidence.check("T1", "close submissions", status, (200,), text)
            status, text, _ = evidence.request(
                "participant", "PATCH",
                f"/api/v1/events/{slug}/projects/{project_id}",
                header=auth.get("participant"),
                body={"title": "Must be refused"},
            )
            evidence.check("T1", "edit after close is refused", status, (403,),
                           text)

    # T2 checks target the seeded fixture event, whose scoring policy is locked.
    status, text, _ = evidence.request(
        "organizer", "GET", f"/api/v1/events/{event_slug}/rubric",
        header=auth.get("organizer"))
    try:
        rubric = json.loads(text)
        criteria = rubric.get("criteria", [])
    except json.JSONDecodeError:
        criteria = []
    criteria = criteria or [{
        "key": "probe",
        "name": "Probe",
        "weight": "1.000",
        "min_score": 1,
        "max_score": 5,
        "position": 0,
    }]
    status, text, _ = evidence.request(
        "organizer", "PUT", f"/api/v1/events/{event_slug}/rubric",
        header=auth.get("organizer"), body={"criteria": criteria})
    evidence.check("T2", "locked rubric rejects edits", status, (409,), text)
    status, text, _ = evidence.request(
        "organizer", "GET", f"/api/v1/events/{event_slug}/progress",
        header=auth.get("organizer"))
    evidence.check("T2", "organizer progress", status, (200,), text)
    status, text, _ = evidence.request(
        "judge_a", "GET",
        f"/api/v1/events/{event_slug}/judge/reviews/prj_01",
        header=auth.get("judge_a"))
    evidence.check("T2", "other-track review isolation", status, (403,), text)
    for kind in ("participants", "teams", "projects", "judges", "assignments",
                 "reviews", "progress", "results", "audit"):
        status, text, _ = evidence.request(
            "organizer", "GET",
            f"/api/v1/events/{event_slug}/exports/{kind}.csv",
            header=auth.get("organizer"))
        evidence.check("T2", f"{kind} CSV export", status, (200,), text)
    status, text, _ = evidence.request(
        "organizer", "GET", f"/api/v1/events/{event_slug}/results/preview",
        header=auth.get("organizer"))
    evidence.check("T2", "normalization preview is available", status, (200,),
                   text, contains='"normalized"')

    # --- T3: community voting on disposable events -------------------------
    t3_unique = f"{int(time.time())}-{secrets.token_hex(3)}"
    vslug, vtrack = make_voting_event(
        evidence, base, auth, "voting", t3_unique, "authenticated")
    if vslug:
        # Two extra voters, each with their own team and submitted project, so
        # the ballot holds three projects and per-voter shuffles can differ.
        helpers = []
        for tag in ("voter2", "voter3"):
            opener, csrf, _, ok = register_session(evidence, base, tag, t3_unique)
            if not ok:
                helpers.append(None)
                continue
            project = submit_project_as(
                evidence, base, f"{tag}(session)", None, vslug, vtrack,
                f"Evidence entry {tag}", opener=opener, csrf=csrf)
            helpers.append((opener, csrf, project) if project else None)
        if any(item is None for item in helpers):
            evidence.check("T3", "voting setup submits three projects", 0,
                           (201,), "helper registration or submission failed")
            vslug = None
        else:
            proj_a = submit_project_as(
                evidence, base, "participant", auth.get("participant"), vslug,
                vtrack, "Evidence entry participant")
            proj_ids = [proj_a, helpers[0][2], helpers[1][2]]
            if not all(proj_ids):
                evidence.check("T3", "voting setup submits three projects", 0,
                               (201,), "participant submission failed")
                vslug = None
    if vslug:
        status, text, _ = evidence.request(
            "participant", "POST", f"/api/v1/events/{vslug}/votes/ballot",
            header=auth.get("participant"), body={})
        ballot_a = js(text).get("ballot", "")
        evidence.check("T3", "authenticated voting opens a ballot", status,
                       (201,), text)
        status, text, _ = evidence.request(
            "participant", "POST", f"/api/v1/events/{vslug}/votes/ballot",
            header=auth.get("participant"), body={})
        evidence.check("T3", "duplicate ballot is refused", status, (409,), text)
        if ballot_a:
            over = {"items": [{"project": proj_ids[0], "votes": 5}]}
            status, text, _ = evidence.request(
                "participant", "PUT",
                f"/api/v1/events/{vslug}/votes/ballot/{ballot_a}",
                header=auth.get("participant"), body=over)
            evidence.check("T3", "quadratic budget is enforced", status,
                           (400,), text, contains="budget_exceeded")
            valid = {"items": [{"project": proj_ids[0], "votes": 2},
                               {"project": proj_ids[1], "votes": 2}]}
            status, text, _ = evidence.request(
                "participant", "PUT",
                f"/api/v1/events/{vslug}/votes/ballot/{ballot_a}",
                header=auth.get("participant"), body=valid)
            evidence.check("T3", "ballot within budget is accepted", status,
                           (200,), text)
            # Ballot order: the same ballot reads back identically, while a
            # second voter shuffles from their own seed.
            status, first, _ = evidence.request(
                "participant", "GET",
                f"/api/v1/events/{vslug}/votes/ballot/{ballot_a}",
                header=auth.get("participant"))
            order_one = [row.get("public_id") for row in
                         js(first).get("projects", [])]
            status, second, _ = evidence.request(
                "participant", "GET",
                f"/api/v1/events/{vslug}/votes/ballot/{ballot_a}",
                header=auth.get("participant"))
            order_two = [row.get("public_id") for row in
                         js(second).get("projects", [])]
            stable = (status == 200 and order_one and order_one == order_two
                      and sorted(order_one) == sorted(proj_ids))
            def voter_order(actor, opener, csrf):
                status, text, _ = session_post(
                    evidence, base, actor, opener, csrf,
                    f"/api/v1/events/{vslug}/votes/ballot", {})
                ballot = js(text).get("ballot", "")
                if status != 201 or not ballot:
                    return []
                status, text, _ = evidence.request(
                    actor, "GET", f"/api/v1/events/{vslug}/votes/ballot/{ballot}",
                    opener=opener)
                return [row.get("public_id") for row in js(text).get("projects", [])]

            def other_orders():
                # voter2 and voter3 already exist; later voters are registered
                # only if every earlier shuffle happened to match.
                for tag, helper in (("voter2", helpers[0]), ("voter3", helpers[1])):
                    yield voter_order(f"{tag}(session)", helper[0], helper[1])
                for number in range(4, SHUFFLE_VOTERS + 2):
                    tag = f"voter{number}"
                    opener, csrf, _, ok = register_session(evidence, base, tag, t3_unique)
                    yield voter_order(f"{tag}(session)", opener, csrf) if ok else []

            random_ok, compared = shuffle_differs(order_one, other_orders())
            evidence.check(
                "T3", "ballot order is stable per ballot, random per voter",
                200 if (stable and random_ok) else 0, (200,),
                f"stable={stable} random={random_ok} voters_compared={compared}")
            status, text, _ = evidence.request(
                "participant", "GET", f"/api/v1/events/{vslug}/voting/results",
                header=auth.get("participant"))
            evidence.check("T3", "results hidden from voters during window",
                           status, (403,), text, contains="results_hidden")
            status, text, _ = evidence.request(
                "visitor", "GET", f"/api/v1/events/{vslug}/voting/results")
            evidence.check("T3", "results hidden from visitors during window",
                           status, (403,), text)
            status, text, _ = evidence.request(
                "voter2(session)", "GET",
                f"/events/{vslug}/voting/results", opener=helpers[0][0])
            evidence.check("T3", "results page hidden during window", status,
                           (403,), text)
            status, text, _ = evidence.request(
                "participant", "GET", f"/api/v1/events/{vslug}/exports/votes.csv",
                header=auth.get("participant"))
            evidence.check("T3", "votes export hidden from voters", status,
                           (403,), text)
            status, text, _ = evidence.request(
                "organizer", "GET", f"/api/v1/events/{vslug}/voting/results",
                header=auth.get("organizer"))
            evidence.check("T3", "organizer still sees live tallies", status,
                           (200,), text)
            status, text, _ = evidence.request(
                "organizer", "GET", f"/api/v1/events/{vslug}/exports/votes.csv",
                header=auth.get("organizer"))
            evidence.check("T3", "organizer votes export", status, (200,), text)
            # Comments: create, hide, restore; the sixth comment hits 429.
            status, text, _ = evidence.request(
                "participant", "POST",
                f"/api/v1/events/{vslug}/projects/{proj_ids[0]}/comments",
                header=auth.get("participant"),
                body={"body": "Evidence comment one"})
            if status == 429 and '"throttled"' in text:
                # A run in the last ten minutes used up the seeded participant's five
                # comments; that limit is the anti-spam control these checks prove.
                evidence.skip("T3", "comment create, moderation and rate limit",
                              "the seeded participant already posted five comments in the "
                              "last ten minutes (an earlier run); rerun after ten minutes")
            else:
                comment_id = js(text).get("public_id", "")
                evidence.check("T3", "comment create", status, (201,), text)
                if comment_id:
                    status, text, _ = evidence.request(
                        "organizer", "POST",
                        f"/api/v1/events/{vslug}/comments/{comment_id}/hide",
                        header=auth.get("organizer"),
                        body={"reason": "Evidence moderation check"})
                    evidence.check("T3", "comment hide with reason", status,
                                   (200,), text)
                    status, text, _ = evidence.request(
                        "participant", "GET",
                        f"/api/v1/events/{vslug}/projects/{proj_ids[0]}/comments",
                        header=auth.get("participant"))
                    evidence.check("T3", "hidden comment leaves the thread",
                                   status, (200,), text,
                                   contains='"comments":[]')
                    status, text, _ = evidence.request(
                        "organizer", "POST",
                        f"/api/v1/events/{vslug}/comments/{comment_id}/restore",
                        header=auth.get("organizer"),
                        body={"reason": "Evidence review passed"})
                    evidence.check("T3", "comment restore", status, (200,), text)
                for number in range(2, 6):
                    status, text, _ = evidence.request(
                        "participant", "POST",
                        f"/api/v1/events/{vslug}/projects/{proj_ids[0]}/comments",
                        header=auth.get("participant"),
                        body={"body": f"Evidence comment {number}"})
                evidence.check("T3", "fifth comment still allowed", status,
                               (201,), text)
                status, text, _ = evidence.request(
                    "participant", "POST",
                    f"/api/v1/events/{vslug}/projects/{proj_ids[0]}/comments",
                    header=auth.get("participant"),
                    body={"body": "Evidence comment six"})
                evidence.check("T3", "comment rate limit answers 429", status,
                               (429,), text)
            status, text, _ = evidence.request(
                "organizer", "GET", f"/api/v1/events/{vslug}/voting/manage",
                header=auth.get("organizer"))
            evidence.check("T3", "abuse audit trail is readable", status,
                           (200,), text, contains="audit")
            status, text, _ = evidence.request(
                "organizer", "GET", f"/api/v1/events/{vslug}/audit",
                header=auth.get("organizer"))
            evidence.check("T3", "event audit log is readable", status,
                           (200,), text, contains="community")

    # Open-link voting on its own event (rules lock per event after voting).
    oslug, otrack = make_voting_event(
        evidence, base, auth, "open-link", t3_unique, "open_link", style="single",
        max_votes=1)
    if oslug:
        status, text, _ = evidence.request(
            "organizer", "GET", f"/api/v1/events/{oslug}/voting/config",
            header=auth.get("organizer"))
        link_token = js(text).get("link_token", "")
        if status != 200 or not link_token:
            evidence.check("T3", "open-link capability is issued", status,
                           (200,), "voting config hides the link token")
        else:
            project = submit_project_as(
                evidence, base, "participant", auth.get("participant"), oslug,
                otrack, "Evidence open-link entry")
            if not project:
                evidence.check("T3", "open-link setup submits a project", 0,
                               (201,), "project submission failed")
            else:
                opener, _ = new_opener()
                status, text, _ = evidence.request(
                    "visitor", "POST",
                    f"/api/v1/events/{oslug}/votes/ballot?v={link_token}",
                    body={}, opener=opener)
                ballot = js(text).get("ballot", "")
                evidence.check("T3", "open-link voting opens a ballot", status,
                               (201,), text)
                if ballot:
                    status, text, _ = evidence.request(
                        "visitor", "POST",
                        f"/api/v1/events/{oslug}/votes/ballot?v={link_token}",
                        body={}, opener=opener)
                    evidence.check("T3", "open-link duplicate is refused",
                                   status, (409,), text)

    # Email-gated voting via the offline outbox (no SMTP in this portal).
    eslug, etrack = make_voting_event(
        evidence, base, auth, "email-gated", t3_unique, "email")
    if eslug:
        project = submit_project_as(
            evidence, base, "participant", auth.get("participant"), eslug,
            etrack, "Evidence email entry")
        if not project:
            evidence.check("T3", "email setup submits a project", 0, (201,),
                           "project submission failed")
        else:
            voter_email = f"tier-evidence-voter-{t3_unique}@example.invalid"
            status, text, _ = evidence.request(
                "visitor", "POST", f"/api/v1/events/{eslug}/votes/email",
                body={"email": voter_email})
            evidence.check("T3", "email link is queued offline", status,
                           (202,), text, contains="queued")
            status, text, _ = evidence.request(
                "organizer", "GET", f"/api/v1/events/{eslug}/outbox",
                header=auth.get("organizer"))
            ticket = ""
            if status == 200:
                bodies = [row.get("body", "") for row in
                          js(text).get("results", [])
                          if row.get("event") == eslug]
                found = ""
                for body_text in bodies:
                    hit = re.search(r"token=([^\s\"'&]+)", body_text)
                    if hit:
                        found = hit.group(1)
                ticket = found
            evidence.check("T3", "offline outbox holds the link",
                           200 if (status == 200 and ticket) else status,
                           (200,), "link-found" if ticket else text)
            if ticket:
                status, text, _ = evidence.request(
                    "visitor", "POST",
                    f"/api/v1/events/{eslug}/votes/email/verify",
                    body={"token": ticket})
                evidence.check("T3", "email ticket opens a ballot", status,
                               (201,), text)

    # --- T4: interop, trust and hand-verifiable surface --------------------
    if vslug:
        status, text, _ = evidence.request("visitor", "GET", "/api/schema/")
        wanted_ops = ("community_ballot", "community_project_comment_create",
                      "event_webhook_subscribe", "record_verify",
                      "event_export_json", "import_fixture",
                      "community_voting_config", "event_outbox_list")
        missing = [op for op in wanted_ops if op not in text]
        evidence.check("T4", "schema documents every UI endpoint", status,
                       (200,),
                       f"missing={missing}" if missing else text[:200])
        status, text, _ = evidence.request("visitor", "GET", "/api/docs/")
        evidence.check("T4", "API docs page is served", status, (200,), text)

        sink = WebhookSink()
        receiver_url = sink.start()
        status, text, _ = evidence.request(
            "organizer", "POST", f"/api/v1/events/{vslug}/webhooks",
            header=auth.get("organizer"),
            body={"url": receiver_url, "event_types": ["*"]})
        hook = js(text)
        endpoint_id, secret = hook.get("public_id", ""), hook.get("secret", "")
        if status == 400 and "loopback, private" in text:
            # Default configuration: the SSRF guard refuses this checker's local
            # receiver, which is the correct production behaviour.
            evidence.check("T4", "private webhook destinations are refused by default",
                           status, (400,), text[:200])
            reason = ("the server refuses private destinations (WEBHOOKS_ALLOW_PRIVATE=0); "
                      "restart it with WEBHOOKS_ALLOW_PRIVATE=1 to watch signed delivery "
                      "to this checker's local receiver")
            evidence.skip("T4", "webhook delivery carries a valid HMAC signature", reason)
            evidence.skip("T4", "webhook secret is never listed", reason)
            sink.stop()
        elif status != 201 or not endpoint_id or not secret:
            evidence.check("T4", "webhook endpoint is created", status,
                           (201,), "endpoint creation failed")
            sink.stop()
        else:
            sink.state["secret"] = secret
            status, text, _ = evidence.request(
                "organizer", "POST",
                f"/api/v1/events/{vslug}/webhooks/{endpoint_id}/test",
                header=auth.get("organizer"), body={})
            queued = status == 202
            hit = sink.wait_for_hit() if queued else None
            sink.stop()
            evidence.check(
                "T4", "webhook delivery carries a valid HMAC signature",
                200 if (hit and hit["valid_signature"]) else 0, (200,),
                f"queued={queued} hit={bool(hit)} "
                f"valid={hit['valid_signature'] if hit else False}")
            status, text, _ = evidence.request(
                "organizer", "GET", f"/api/v1/events/{vslug}/webhooks",
                header=auth.get("organizer"))
            evidence.check("T4", "webhook secret is never listed", status,
                           (200,),
                           "secret-hidden" if "secret" not in text else text[:200])

        status, text, _ = evidence.request(
            "visitor", "GET", f"/embed/{vslug}")
        status2, _, headers = call(base, f"/embed/{vslug}")
        evidence.req("visitor", "GET", f"/embed/{vslug}", status2)
        csp = headers.get("Content-Security-Policy", "")
        set_cookie = headers.get("Set-Cookie", "")
        embed_ok = (status == 200 and "frame-ancestors *" in csp
                    and not set_cookie and "Evidence entry" in text)
        evidence.check("T4", "embed is public with relaxed framing", status,
                       (200,),
                       f"csp={csp[:80]} cookie={bool(set_cookie)}"
                       if not embed_ok else text[:200])
        status, _, headers = call(base, gallery_path)
        evidence.req("visitor", "GET", gallery_path, status)
        evidence.check("T4", "framing stays denied outside the embed", status,
                       (200,),
                       headers.get("Content-Security-Policy", "")[:200]
                       if "frame-ancestors 'none'" not in headers.get(
                           "Content-Security-Policy", "") else "deny-ok")

    # Certificate access: the project owner reads it, a stranger cannot.
    if vslug and helpers and all(helpers):
        (opener2, _, proj2) = helpers[0]
        (opener3, _, _) = helpers[1]
        cert_path = f"/events/{vslug}/certificates/participation/{proj2}"
        status, text, _ = evidence.request(
            "owner(session)", "GET", cert_path, opener=opener2)
        owner_ok = status == 200
        evidence.check("T4", "certificate owner can read", status, (200,),
                       text[:200])
        status, text, _ = evidence.request(
            "stranger(session)", "GET", cert_path, opener=opener3)
        evidence.check("T4", "certificate stranger is refused", status,
                       (403,), text[:200] if owner_ok else "owner check failed")

    # Signed judge records on a judging event with a real submitted review.
    judge_email = ""
    status, text, _ = evidence.request("judge_a", "GET", "/api/v1/me",
                                       header=auth.get("judge_a"))
    judge_email = js(text).get("user", {}).get("email", "")
    if not judge_email:
        evidence.check("T4", "judge identity is discoverable", status,
                       (200,), text[:200])
        judge_email = None
    if judge_email:
        jslug = None
        name = f"Tier Evidence judging {t3_unique}"
        status, text, _ = evidence.request(
            "organizer", "POST", "/api/v1/events",
            header=auth.get("organizer"),
            body={"name": name, "submissions_open_at": None,
                  "submissions_close_at": iso_in(7 * 86400),
                  "judging_open_at": iso_in(7 * 86400)})
        jslug = js(text).get("slug", "") if status == 201 else ""
        if not jslug:
            evidence.check("T4", "judging setup creates event", status,
                           (201,), text[:300])
        else:
            status, text, _ = evidence.request(
                "organizer", "POST", f"/api/v1/events/{jslug}/tracks",
                header=auth.get("organizer"),
                body={"name": "Evidence Track", "position": 0})
            jtrack = js(text).get("public_id", "")
            project = submit_project_as(
                evidence, base, "participant", auth.get("participant"), jslug,
                jtrack, "Evidence judged entry") if status == 201 else ""
            if not project:
                evidence.check("T4", "judging setup submits a project", 0,
                               (201,), "track or submission failed")
                jslug = None
        if jslug:
            status, text, _ = evidence.request(
                "organizer", "PUT", f"/api/v1/events/{jslug}/rubric",
                header=auth.get("organizer"),
                body={"criteria": [{
                    "key": "quality", "name": "Quality",
                    "weight": "1.000", "min_score": 1, "max_score": 5,
                    "position": 0}]})
            rubric_ok = status == 200
            status, text, _ = evidence.request(
                "organizer", "POST", f"/api/v1/events/{jslug}/judges",
                header=auth.get("organizer"),
                body={"email": judge_email, "tracks": [jtrack]})
            judge_id = js(text).get("public_id", "") if status == 201 else ""
            status, text, _ = evidence.request(
                "organizer", "POST",
                f"/api/v1/events/{jslug}/close-submissions",
                header=auth.get("organizer"), body={})
            closed = status == 200
            if not (rubric_ok and judge_id and closed):
                evidence.check("T4", "judging setup assigns a judge", 0,
                               (200,), "rubric, judge or close failed")
                jslug = None
        if jslug:
            status, text, _ = evidence.request(
                "organizer", "POST", f"/api/v1/events/{jslug}/assignments",
                header=auth.get("organizer"),
                body={"judges": [judge_id], "projects": [project]})
            assigned = (status == 201
                        and len(js(text).get("created", [])) == 1)
            if not assigned:
                evidence.check("T4", "judging setup creates assignment",
                               status, (201,), text[:300])
                jslug = None
        if jslug:
            review_body = {"scores": {"quality": 4},
                           "comment": "Evidence review"}
            status, text, _ = evidence.request(
                "judge_a", "PUT",
                f"/api/v1/events/{jslug}/judge/reviews/{project}",
                header=auth.get("judge_a"), body=review_body)
            drafted = status == 200
            status, text, _ = evidence.request(
                "judge_a", "POST",
                f"/api/v1/events/{jslug}/judge/reviews/{project}/submit",
                header=auth.get("judge_a"), body=review_body)
            if not (drafted and status == 200):
                evidence.check("T4", "judging setup submits a review", status,
                               (200,), text[:300])
                jslug = None
        if jslug:
            status, text, _ = evidence.request(
                "organizer", "POST", f"/api/v1/events/{jslug}/close-judging",
                header=auth.get("organizer"), body={})
            if status != 200:
                evidence.check("T4", "judging setup closes judging", status,
                               (200,), text[:300])
                jslug = None
        if jslug:
            status, text, _ = evidence.request(
                "organizer", "POST", f"/api/v1/events/{jslug}/judge-records",
                header=auth.get("organizer"), body={})
            records = js(text).get("records", [])
            record_id = records[0] if status == 200 and records else ""
            if not record_id:
                evidence.check("T4", "signed judge record is issued", status,
                               (200,), text[:300])
            else:
                status, text, _ = evidence.request(
                    "visitor", "GET", f"/records/{record_id}?format=json")
                document = json.loads(text) if status == 200 else {}
                if status != 200 or "signature" not in document:
                    evidence.check("T4", "signed judge record is public",
                                   status, (200,), text[:300])
                else:
                    status, text, _ = evidence.request(
                        "visitor", "POST", "/api/v1/records/verify",
                        body=document)
                    evidence.check("T4", "signed judge record verifies",
                                   status, (200,), text,
                                   contains='"valid":true')
                    tampered = json.loads(json.dumps(document))
                    try:
                        tampered["record"]["judge"]["display_name"] = \
                            "Mallory Tamper"
                    except (KeyError, TypeError):
                        tampered["record"]["tampered"] = True
                    status, text, _ = evidence.request(
                        "visitor", "POST", "/api/v1/records/verify",
                        body=tampered)
                    evidence.check("T4", "tampered judge record fails",
                                   status, (200,), text,
                                   contains='"valid":false')
            status, text, _ = evidence.request(
                "organizer", "GET",
                f"/api/v1/events/{jslug}/exports/event.json",
                header=auth.get("organizer"))
            try:
                exported = json.loads(text)
            except json.JSONDecodeError:
                exported = {}
            counts = {key: len(exported.get(key, []))
                      for key in ("tracks", "judges", "teams", "projects",
                                  "scores")}
            if status != 200 or not exported.get("projects"):
                evidence.check("T4", "event.json export is produced", status,
                               (200,), text[:300])
            else:
                status, report, _ = evidence.request(
                    "organizer", "POST", "/api/v1/imports",
                    header=auth.get("organizer"), body=exported)
                reported = js(report).get("counts", {})
                round_ok = (
                    status == 201
                    and reported.get("projects") == counts["projects"]
                    and reported.get("judges") == counts["judges"]
                    and reported.get("teams") == counts["teams"]
                    and reported.get("reviews") == counts["scores"])
                evidence.check(
                    "T4", "event.json round trip keeps equal counts", status,
                    (201,),
                    f"exported={counts} imported={reported}"
                    if not round_ok else f"counts={counts}")

    # Preserve the report-only contract: all checks are printed, process exits 0.
    for tier in ("T1", "T2", "T3", "T4"):
        rows = [row for row in evidence.rows if row[0] == tier]
        done = sum(1 for row in rows if row[2])
        skipped = sum(1 for row in evidence.skipped if row[0] == tier)
        note = f", {skipped} skipped (see SKIPPED lines)" if skipped else ""
        print(f"{tier} summary {done}/{len(rows)} checks passed{note}")
    note = f", {len(evidence.skipped)} skipped" if evidence.skipped else ""
    print(f"summary {sum(row[2] for row in evidence.rows)}/"
          f"{len(evidence.rows)} checks passed{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())