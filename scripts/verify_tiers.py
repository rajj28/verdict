#!/usr/bin/env python3
"""Exercise T1/T2 depth using .dogfood.toml tokens and a disposable event.

The script uses only urllib, json, tomllib (with the run.py fallback parser),
and the standard library. The event it creates is intentionally retained as
review evidence; the seeded fixture event is never modified.
"""
import argparse
import json
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
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


def call(base, path, header=None, method="GET", body=None, opener=None, extra_headers=None):
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
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except (OSError, ValueError) as error:
        return 0, f"{type(error).__name__}: {error}"


class Evidence:
    def __init__(self, base, auth):
        self.base = base
        self.auth = auth
        self.rows = []

    def check(self, tier, label, status, wanted, detail="", contains=""):
        ok = status in wanted and (not contains or contains in detail)
        self.rows.append((tier, label, ok, status, wanted, detail))
        print(f"{tier}  {label} ..... {'PASS' if ok else 'FAIL'}")
        if not ok:
            print(f"  wanted {wanted}, got {status or 'no response'} {detail[:300]}")
        return ok


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
    status, gallery = call(base, gallery_path)
    evidence.check("T1", "public gallery", status, (200,), gallery)
    status, search = call(base, "/api/v1/projects?q=Glass")
    evidence.check("T1", "gallery search returns matching projects", status, (200,), search,
                   contains="Glass Signal")

    peer = routes.get("peer_scores", "")
    match = re.search(r"/events/([^/]+)/judges/([^/]+)/scores", peer)
    event_slug = match.group(1) if match else "sample-hack-2026"
    peer_id = match.group(2) if match else "jdg_24"

    # Exercise the whole disposable T1 lifecycle through public API writes.
    unique_suffix = secrets.token_hex(3)
    unique = f"Tier Evidence {int(time.time())}-{unique_suffix}"
    event_body = {
        "name": unique,
        "submissions_open_at": None,
        "submissions_close_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 86400)),
        "judging_open_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 86400)),
    }
    status, text = call(base, "/api/v1/events", auth.get("organizer"), "POST", event_body)
    created = None
    if status == 201:
        try:
            created = json.loads(text)
        except json.JSONDecodeError:
            created = None
    evidence.check("T1", "create disposable verification event", status, (201,), text)
    if created:
        slug = created["slug"]
        status, text = call(base, f"/api/v1/events/{slug}/tracks", auth.get("organizer"),
                            "POST", {"name": "Evidence Track", "position": 0})
        try:
            track_id = json.loads(text).get("public_id", "")
        except json.JSONDecodeError:
            track_id = ""
        evidence.check("T1", "create event track", status, (201,), text)
        status, text = call(base, f"/api/v1/events/{slug}/teams", auth.get("participant"),
                            "POST", {"name": "Evidence Team"})
        try:
            team = json.loads(text).get("public_id", "")
        except json.JSONDecodeError:
            team = ""
        evidence.check("T1", "participant creates a team", status, (201,), text)
        status, text = call(
            base, f"/api/v1/events/{slug}/projects", auth.get("participant"), "POST",
            {"title": "Evidence draft", "summary": "T1 proof", "description": "Disposable",
             "repo_url": "https://example.invalid/evidence", "track": track_id},
        )
        try:
            project_id = json.loads(text).get("public_id", "")
        except json.JSONDecodeError:
            project_id = ""
        evidence.check("T1", "create project draft", status, (201,), text)
        if project_id:
            status, text = call(
                base, f"/api/v1/events/{slug}/projects/{project_id}/submit",
                auth.get("participant"), "POST", {},
            )
            evidence.check("T1", "submit project draft", status, (200,), text)
            status, text = call(
                base, f"/api/v1/events/{slug}/projects/{project_id}",
                auth.get("participant"), "PATCH", {"title": "Evidence edited"},
            )
            evidence.check("T1", "edit submitted project", status, (200,), text)
            # The second registered person is required for a complete invite acceptance.
            cookiejar = CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookiejar))
            status, page = call(base, "/register", opener=opener)
            csrf_cookie = next((cookie.value for cookie in cookiejar
                                if cookie.name == "csrftoken"), "")
            if status == 200 and csrf_cookie:
                password = f"Evidence-{int(time.time())}-Pass9!"
                email = f"evidence-{int(time.time())}-{unique_suffix}@example.invalid"
                status, text = call(
                    base, "/api/v1/auth/register", method="POST",
                    body={"email": email, "password": password, "display_name": "Evidence Joiner"},
                    opener=opener, extra_headers={"X-CSRFToken": csrf_cookie},
                )
                registered = status == 201
            else:
                registered = False
                text = page
            if registered and team:
                status, invite_text = call(
                    base, f"/api/v1/events/{slug}/teams/{team}/invite",
                    auth.get("participant"), "POST", {},
                )
                try:
                    invite_url = json.loads(invite_text).get("url", "")
                    invite_token = invite_url.partition("token=")[2]
                except json.JSONDecodeError:
                    invite_token = ""
                evidence.check("T1", "create invite link", status, (201,), invite_text)
                if invite_token:
                    # Registration rotates the CSRF secret; use the latest cookie.
                    csrf_cookie = next((cookie.value for cookie in cookiejar
                                        if cookie.name == "csrftoken"), csrf_cookie)
                    status, text = call(
                        base, f"/api/v1/invites/{invite_token}/accept", method="POST", body={},
                        opener=opener, extra_headers={"X-CSRFToken": csrf_cookie},
                    )
                    evidence.check("T1", "invite-link join", status, (201,), text)
            else:
                evidence.check("T1", "invite-link join", 0, (201,), "second account unavailable")
            status, text = call(
                base, f"/api/v1/events/{slug}/close-submissions",
                auth.get("organizer"), "POST", {},
            )
            evidence.check("T1", "close submissions", status, (200,), text)
            status, text = call(
                base, f"/api/v1/events/{slug}/projects/{project_id}",
                auth.get("participant"), "PATCH", {"title": "Must be refused"},
            )
            evidence.check("T1", "edit after close is refused", status, (403,), text)

    # T2 checks target the seeded fixture event, whose scoring policy is locked.
    status, text = call(base, f"/api/v1/events/{event_slug}/rubric", auth.get("organizer"))
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
    status, text = call(base, f"/api/v1/events/{event_slug}/rubric", auth.get("organizer"),
                        "PUT", {"criteria": criteria})
    evidence.check("T2", "locked rubric rejects edits", status, (409,), text)
    status, text = call(base, f"/api/v1/events/{event_slug}/progress", auth.get("organizer"))
    evidence.check("T2", "organizer progress", status, (200,), text)
    status, text = call(base, f"/api/v1/events/{event_slug}/judge/reviews/prj_01",
                        auth.get("judge_a"))
    evidence.check("T2", "other-track review isolation", status, (403,), text)
    for kind in ("participants", "teams", "projects", "judges", "assignments",
                 "reviews", "progress", "results", "audit"):
        status, text = call(
            base, f"/api/v1/events/{event_slug}/exports/{kind}.csv", auth.get("organizer")
        )
        evidence.check("T2", f"{kind} CSV export", status, (200,), text)
    status, text = call(base, f"/api/v1/events/{event_slug}/results/preview",
                        auth.get("organizer"))
    evidence.check("T2", "normalization preview is available", status, (200,), text,
                   contains='"normalized"')

    # Preserve the report-only contract: all checks are printed, process exits 0.
    print(f"summary {sum(row[2] for row in evidence.rows)}/{len(evidence.rows)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
