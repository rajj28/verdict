#!/usr/bin/env python3
"""VERDICT quality gate (standard library only).

Run from the repo root as::

    .venv/Scripts/python.exe scripts/gate.py [--docker]

Steps, in order:

1. ``manage.py check``
2. ``manage.py makemigrations --check --dry-run``
3. ``manage.py test tests``
4. ``python -m unittest tests.test_engine`` (pure, no Django)
5. with ``--docker``: ``docker compose up --build -d --wait`` then
   ``python run.py .dogfood.toml``

Prints ONE compact line per step (name, PASS/FAIL, key number, seconds);
step 3 additionally lists failing test names (max 15) on failure so the
full traceback is not dumped. Exit code is 0 iff every executed step
passed, else 1.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
EXPECTED_CHECKS = 7
MAX_FAILED_NAMES = 15


class TestSummary(NamedTuple):
    """Parsed ``manage.py test`` / ``unittest`` output."""

    ran: int
    failures: int
    errors: int
    failed_tests: tuple[str, ...]


class AcceptanceSummary(NamedTuple):
    """Parsed ``run.py`` acceptance output."""

    passed: int
    total: int
    verified: str


_RAN_RE = re.compile(r"^Ran (\d+) tests?\b", re.MULTILINE)
_FAILURES_RE = re.compile(r"\bfailures=(\d+)")
_ERRORS_RE = re.compile(r"\berrors=(\d+)")
_FAILED_TEST_RE = re.compile(r"^(?:FAIL|ERROR): (.+?)\s*$", re.MULTILINE)
_PASS_LINE_RE = re.compile(r"\bPASS\s*$", re.MULTILINE)
_CHECK_LINE_RE = re.compile(r"^\S.*\b(?:PASS|FAIL)\s*$", re.MULTILINE)
_VERIFIED_RE = re.compile(r"^claimed\s.*\bverified\b.*$", re.MULTILINE)


def parse_test_output(text: str) -> TestSummary:
    """Parse Django test runner / unittest output.

    Reads the ``Ran N tests`` count, the ``failures=``/``errors=`` counts
    from the ``FAILED (...)`` footer (0 when the run is ``OK``), and the
    ``FAIL:``/``ERROR:`` test identifiers in order of appearance.
    """
    text = text or ""
    ran_match = _RAN_RE.search(text)
    failures_match = _FAILURES_RE.search(text)
    errors_match = _ERRORS_RE.search(text)
    ran = int(ran_match.group(1)) if ran_match else 0
    failures = int(failures_match.group(1)) if failures_match else 0
    errors = int(errors_match.group(1)) if errors_match else 0
    names: list[str] = []
    for name in _FAILED_TEST_RE.findall(text):
        if name not in names:
            names.append(name)
    return TestSummary(ran, failures, errors, tuple(names))


def parse_acceptance_output(text: str) -> AcceptanceSummary:
    """Parse ``run.py`` output: PASS check lines and the verified line."""
    text = text or ""
    passed = len(_PASS_LINE_RE.findall(text))
    total = len(_CHECK_LINE_RE.findall(text))
    verified_match = _VERIFIED_RE.search(text)
    verified = verified_match.group(0).strip() if verified_match else ""
    return AcceptanceSummary(passed, total, verified)


def format_step(name: str, ok: bool, detail: str, seconds: float) -> str:
    """Render one compact gate line: name, PASS/FAIL, detail, seconds."""
    status = "PASS" if ok else "FAIL"
    return f"{name:16s} {status} {detail} ({seconds:.1f}s)"


def last_line(text: str, limit: int = 120) -> str:
    """Return the last non-blank line, whitespace-collapsed and truncated."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    line = re.sub(r"\s+", " ", lines[-1]) if lines else ""
    if len(line) > limit:
        line = line[: limit - 3] + "..."
    return line


def _run(cmd: list[str], timeout: float) -> tuple[int, str, str, float]:
    """Run *cmd* in the repo root, capturing stdout/stderr separately."""
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or "", (
            time.perf_counter() - start
        )
    except FileNotFoundError as exc:
        return 127, "", f"{type(exc).__name__}: {exc}", (
            time.perf_counter() - start
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        err = exc.stderr if isinstance(exc.stderr, str) else ""
        return 124, out, (err + f"\nTIMEOUT after {timeout}s"), (
            time.perf_counter() - start
        )


def _database_url_error() -> str | None:
    """Return an actionable error message if DATABASE_URL is missing/invalid."""
    url = os.environ.get("DATABASE_URL") or ""
    if not url:
        return (
            "DATABASE_URL is not set. VERDICT requires PostgreSQL: set it to "
            "postgres://user:pass@host:port/name (see .env.example). If the "
            "Compose 'db' service has no published host port, run gate steps "
            "inside the container instead: docker compose exec web ..."
        )
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if scheme not in {"postgres", "postgresql"}:
        return (
            f"DATABASE_URL scheme {scheme or '<none>'!r} is not supported; use "
            "postgres:// or postgresql://. SQLite is not supported anywhere."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    """Run the gate steps in order; return 0 iff every step passed."""
    parser = argparse.ArgumentParser(description="VERDICT quality gate.")
    parser.add_argument(
        "--docker",
        action="store_true",
        help="also run docker acceptance (compose up + run.py)",
    )
    args = parser.parse_args(argv)

    overall = True

    db_error = _database_url_error()
    if db_error:
        print(format_step("database", False, db_error, 0.0), flush=True)
        return 1

    # (1) Django system check.
    code, out, err, seconds = _run([PY, "manage.py", "check"], 180)
    ok = code == 0
    detail = "0 issues" if ok else f"rc={code} {last_line(out + chr(10) + err)}"
    print(format_step("check", ok, detail, seconds), flush=True)
    overall = overall and ok

    # (2) No pending model changes.
    code, out, err, seconds = _run(
        [PY, "manage.py", "makemigrations", "--check", "--dry-run"], 180
    )
    ok = code == 0
    if ok:
        detail = "0 changes"
    else:
        detail = f"rc={code} changes-pending {last_line(out + chr(10) + err)}"
    print(format_step("migrations", ok, detail, seconds), flush=True)
    overall = overall and ok

    # (3) Full Django test suite.
    code, out, err, seconds = _run([PY, "manage.py", "test", "tests"], 1200)
    summary = parse_test_output(out + "\n" + err)
    ok = code == 0 and summary.failures == 0 and summary.errors == 0
    detail = (
        f"Ran {summary.ran} tests "
        f"failures={summary.failures} errors={summary.errors}"
    )
    print(format_step("django-tests", ok, detail, seconds), flush=True)
    if not ok:
        for name in summary.failed_tests[:MAX_FAILED_NAMES]:
            print(f"  {name}", flush=True)
    overall = overall and ok

    # (4) Pure engine unit tests (no Django).
    code, out, err, seconds = _run(
        [PY, "-m", "unittest", "tests.test_engine"], 300
    )
    summary = parse_test_output(out + "\n" + err)
    ok = code == 0 and summary.failures == 0 and summary.errors == 0
    detail = (
        f"Ran {summary.ran} tests "
        f"failures={summary.failures} errors={summary.errors}"
    )
    print(format_step("engine-pure", ok, detail, seconds), flush=True)
    overall = overall and ok

    # (5) Docker acceptance (opt-in).
    if args.docker:
        started = time.perf_counter()
        up_code, up_out, up_err, _ = _run(
            ["docker", "compose", "up", "--build", "-d", "--wait"], 900
        )
        if up_code != 0:
            elapsed = time.perf_counter() - started
            detail = f"compose rc={up_code} {last_line(up_out + chr(10) + up_err)}"
            print(format_step("acceptance", False, detail, elapsed), flush=True)
            overall = False
        else:
            run_code, run_out, run_err, _ = _run(
                [PY, "run.py", ".dogfood.toml"], 180
            )
            acc = parse_acceptance_output(run_out + "\n" + run_err)
            ok = (
                run_code == 0
                and acc.passed == EXPECTED_CHECKS
                and acc.total == EXPECTED_CHECKS
            )
            verified = acc.verified or "no verified line"
            detail = f"{acc.passed}/{EXPECTED_CHECKS} PASS; {verified}"
            if run_code != 0:
                detail += f" (run.py rc={run_code})"
            elapsed = time.perf_counter() - started
            print(format_step("acceptance", ok, detail, elapsed), flush=True)
            if ok:
                report = REPO_ROOT / "acceptance-report.txt"
                report.write_text(run_out, encoding="utf-8", newline="\n")
            overall = overall and ok

    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
