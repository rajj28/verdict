#!/usr/bin/env python3
"""Run the live integrity attack suite against a portal using .dogfood.toml.

The authenticated probe endpoint dispatches every named attack through the
server's Django test Client, so each request still traverses real routing,
authentication and middleware without changing persistent portal data.
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None


def parse_toml(text):
    """Read the simple string/array subset used by the repository config."""
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


def request(url, header, method, body=None):
    req = urllib.request.Request(url, method=method)
    if header:
        name, _, value = header.partition(":")
        req.add_header(name.strip(), value.strip())
    if body is not None:
        req.data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except (OSError, ValueError) as error:
        return 0, f"{type(error).__name__}: {error}"


def result(label, url, method, header, body, wanted):
    status, text = request(url, header, method, body)
    ok = status == wanted
    print(f"HTTP {label} ..... {'REFUSED' if ok else 'LEAKED'} {status} (expected {wanted})")
    if not ok:
        print(f"  {method} {url}")
        print(f"  response: {text[:300]}")
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", default=".dogfood.toml")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    base = cfg["portal"]["base_url"].rstrip("/")
    organizer = cfg.get("auth", {}).get("organizer", "")
    routes = cfg.get("routes", {})
    event_match = re.search(r"/events/([^/]+)/", routes.get("peer_scores", ""))
    event_slug = event_match.group(1) if event_match else ""
    peer_match = re.search(r"/judges/([^/]+)/scores", routes.get("peer_scores", ""))
    peer_id = peer_match.group(1) if peer_match else "jdg_24"
    judge_scores = routes.get("judge_scores", "/api/v1/judge/scores")
    judge_path = routes.get(
        "peer_scores", f"/api/v1/events/{event_slug}/judges/{peer_id}/scores"
    )
    project_id = "prj_01"
    direct_results = []

    # These assertions are sent directly by urllib to the live application's routes.
    direct_results.append(result(
        "peer score by ?judge=", base + judge_scores + f"?judge={peer_id}", "GET",
        cfg.get("auth", {}).get("judge_b"), None, 403,
    ))
    direct_results.append(result(
        "peer score by path", base + judge_path, "GET",
        cfg.get("auth", {}).get("judge_b"), None, 403,
    ))
    if event_slug:
        review_path = f"/api/v1/events/{event_slug}/judge/reviews/{project_id}"
        direct_results.append(result(
            "other-track review read", base + review_path, "GET",
            cfg.get("auth", {}).get("judge_a"), None, 403,
        ))
        direct_results.append(result(
            "other-track review write", base + review_path, "PUT",
            cfg.get("auth", {}).get("judge_a"), {"scores": {}, "comment": "probe"}, 403,
        ))
        submissions_path = routes.get(
            "submit", f"/api/v1/events/{event_slug}/projects"
        )
        direct_results.append(result(
            "participant create after deadline", base + submissions_path, "POST",
            cfg.get("auth", {}).get("participant"),
            {"title": "probe", "summary": "probe", "description": "probe",
             "repo_url": "https://example.invalid/probe"},
            403,
        ))
        project_path = f"/api/v1/events/{event_slug}/projects/{project_id}"
        direct_results.append(result(
            "participant update after deadline", base + project_path, "PATCH",
            cfg.get("auth", {}).get("participant"), {"title": "probe"}, 403,
        ))
        direct_results.append(result(
            "participant answers after deadline", base + project_path + "/answers", "PUT",
            cfg.get("auth", {}).get("participant"), {}, 403,
        ))
        export_path = routes.get(
            "csv_export", f"/api/v1/events/{event_slug}/exports/results.csv"
        )
        for role in ("judge_a", "participant"):
            direct_results.append(result(
                f"CSV export as {role}", base + export_path, "GET",
                cfg.get("auth", {}).get(role), None, 403,
            ))
    direct_results.append(result(
        "anonymous API", base + "/api/v1/judge/scores", "GET", "", None, 401,
    ))
    direct_results.append(result(
        "session POST without CSRF", base + "/api/v1/auth/login", "POST", "",
        {"email": "probe@example.invalid", "password": "invalid"}, 403,
    ))

    # Create, revoke, then immediately reuse a disposable token to test revocation over HTTP.
    participant_auth = cfg.get("auth", {}).get("participant", "")
    token_status, token_text = request(
        base + "/api/v1/me/tokens", participant_auth, "POST",
        {"name": "External integrity probe"},
    )
    temporary = {}
    try:
        if token_status == 201:
            try:
                temporary = json.loads(token_text)
            except json.JSONDecodeError:
                temporary = {}
        token_created = token_status == 201
        direct_results.append(token_created)
        print(f"HTTP create revocation test token ..... {'PASS' if token_created else 'FAIL'}")
        if not token_created:
            print(f"  POST {base}/api/v1/me/tokens")
            print(f"  response: {token_text[:300]}")
        token_info = temporary.get("token", {})
        plaintext = temporary.get("plaintext", "")
        prefix = token_info.get("prefix", "")
        if token_status == 201 and prefix and plaintext:
            revoke_status, revoke_text = request(
                base + f"/api/v1/me/tokens/{prefix}", participant_auth, "DELETE"
            )
            revoke_ok = revoke_status == 200
            direct_results.append(revoke_ok)
            print(f"HTTP revoke temporary token ..... {'PASS' if revoke_ok else 'FAIL'}")
            if revoke_status == 200:
                direct_results.append(result(
                    "revoked token reuse", base + "/api/v1/me", "GET",
                    f"Authorization: Bearer {plaintext}", None, 401,
                ))
            else:
                print(f"  cleanup failed: {revoke_text[:300]}")
                direct_results.append(False)
        else:
            direct_results.append(False)
            print("HTTP revoked token reuse ..... FAIL (token creation was unsuccessful)")
    except Exception:
        if temporary:
            token_info = temporary.get("token", {})
            prefix = token_info.get("prefix", "")
            if prefix:
                request(base + f"/api/v1/me/tokens/{prefix}", participant_auth, "DELETE")
        raise

    # Run the complete named matrix on the server as well, including disposable
    # drafts/media, team invites, CSV formula injection and cross-event reads.
    endpoint = base + "/api/v1/integrity/probe"
    status, text = request(
        endpoint, organizer, "POST", {"event": event_slug} if event_slug else {}
    )
    if status != 200:
        print(f"PROBE  authenticated live suite ..... FAIL {status or 'no response'}")
        print(f"POST {endpoint}")
        print(f"response: {text[:500]}")
        return 1
    try:
        report = json.loads(text)
    except json.JSONDecodeError:
        print(f"PROBE  authenticated live suite ..... FAIL {status} invalid JSON")
        return 1
    for case in report.get("cases", []):
        status = case.get("status_code") or 0
        verdict = ("REFUSED" if status >= 400 else "DEFUSED") if case.get("passed") else "LEAKED"
        print(
            f"{case.get('area', 'ATTACK'):<16} {case.get('description', case.get('key'))} "
            f"..... {verdict} {case.get('actual')} (expected {case.get('expected')})"
        )
        if not case.get("passed"):
            print(f"  {case.get('method')} {base}{case.get('path')} as {case.get('actor')}")
            print(f"  {case.get('reason')}")
    print(f"summary {report.get('summary', 'probe response contained no summary')}")
    direct_ok = all(direct_results)
    print(f"direct HTTP checks {sum(direct_results)}/{len(direct_results)} passed")
    return 0 if report.get("ok") and direct_ok else 1


if __name__ == "__main__":
    sys.exit(main())
