#!/usr/bin/env python3
"""VERDICT backup, verify and restore. Standard library only, no Django import.

Run from the repository root as::

    python scripts/backup.py create [--out backups]
    python scripts/backup.py verify backups/verdict-20260928T101500Z
    python scripts/backup.py restore backups/verdict-20260928T101500Z --yes

``create`` writes three things into ``backups/verdict-<UTC stamp>/``:

* ``db.dump`` - ``pg_dump --format=custom`` piped out of the ``db`` service. The
  web image carries no PostgreSQL client, so the dump always goes through ``db``.
* ``appdata/`` - the ``appdata`` volume (``docker compose cp web:/data``):
  uploaded media, ``secret_key`` and the Ed25519 signing keys under ``keys/``.
* ``manifest.json`` - UTC timestamp, image id, commit, the sha256 of every other
  file, and the fingerprint from ``manage.py backup_fingerprint``.

The manifest is written last and an ``.incomplete`` marker is removed just before
it, so a directory that failed halfway can never be mistaken for a usable backup.

``restore`` is the inverse and proves itself: it verifies every checksum, restores
the database and the volume, waits for ``/healthz``, re-reads the fingerprint and
requires it to equal the manifest byte for byte, then re-runs
``manage.py verify_publication`` for every publication the manifest lists.

The backup contains the database *and* the credentials: the Django secret key and
the record signing keys. Anyone holding this directory can read the portal and
mint signed records. Store it like a password.

Exports (``/exports/*.csv``, the decision record download) are not backups: they
are read-only views for judges and organizers, not the portal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

MANIFEST_NAME = "manifest.json"
DUMP_NAME = "db.dump"
APPDATA_NAME = "appdata"
INCOMPLETE_NAME = ".incomplete"
MANIFEST_VERSION = 1

WEB_SERVICE = "web"
DB_SERVICE = "db"
WORKER_SERVICE = "webhook-worker"
DATA_MOUNT = "/data"
HEALTH_PATH = "/healthz"
DEFAULT_PORT = 8080
HEALTH_TIMEOUT_SECONDS = 180
CHUNK = 1024 * 1024

REPO_ROOT = Path(__file__).resolve().parent.parent

#: ``docker compose cp`` copies a directory *into* its destination, so a build
#: where the volume landed at ``/data/appdata`` has to be lifted back to ``/data``
#: before the portal can read its own secret key. ``secret_key`` is the tell: it
#: is always present at the top of an intact appdata volume.
APPDATA_LAYOUT_CODE = """\
import os, shutil, sys

data = sys.argv[1]
nested = os.path.join(data, "appdata")
if not (os.path.isdir(nested) and os.path.isfile(os.path.join(nested, "secret_key"))):
    print("layout ok: " + ", ".join(sorted(os.listdir(data))))
    sys.exit(0)
for name in os.listdir(nested):
    source = os.path.join(nested, name)
    target = os.path.join(data, name)
    if os.path.isdir(target) and not os.path.islink(target):
        shutil.rmtree(target)
    elif os.path.lexists(target):
        os.remove(target)
    shutil.move(source, target)
os.rmdir(nested)
print("moved up: " + data)
"""


class BackupError(RuntimeError):
    """A backup or restore step failed. The message names the step."""


class Step(NamedTuple):
    """One external command in a plan.

    ``required`` steps abort the run on failure; optional ones (the image id, the
    git commit) are recorded as best effort because a backup that depends on a git
    checkout would refuse to run from a release tarball.
    """

    description: str
    command: list[str]
    stdin: Path | None = None
    stdout: Path | None = None
    cwd: Path | None = None
    required: bool = True
    display: str | None = None


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


class Database(NamedTuple):
    name: str
    user: str


class VerifyReport(NamedTuple):
    manifest: dict
    problems: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


# --------------------------------------------------------------------------- env


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=value`` file. Quoted values are unwrapped, blanks ignored."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def compose_env(cwd: Path | None = None) -> dict[str, str]:
    """The environment Compose would use: the process, then ``.env``, then the example."""
    merged: dict[str, str] = {}
    for directory in (cwd or Path.cwd(), REPO_ROOT):
        if not directory.is_dir():
            continue
        for name in (".env", ".env.example"):
            merged.update(read_env_file(directory / name))
    merged.update(os.environ)
    return merged


def _service_setting(compose_text: str, variable: str) -> str | None:
    """The value Compose gives ``variable`` in the ``db`` service, e.g. ``${X:-y}``."""
    match = re.search(
        rf"^\s*{re.escape(variable)}:\s*(.+?)\s*$", compose_text, re.MULTILINE
    )
    return match.group(1) if match else None


def unpack_compose_value(raw: str | None, env: dict[str, str], variable: str, default: str) -> str:
    """Resolve ``${VAR}`` / ``${VAR:-default}`` / a literal the way Compose does."""
    if raw is None:
        return env.get(variable) or default
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}", raw)
    if match is None:
        return raw
    found, fallback = match.group(1), match.group(2)
    return env.get(found) or (fallback or "") or default


def resolve_database(cwd: Path | None = None, env: dict[str, str] | None = None) -> Database:
    """The database name and role Compose creates, from the compose file then ``.env``."""
    env = compose_env(cwd) if env is None else env
    search = [cwd or Path.cwd(), REPO_ROOT]
    compose_text = ""
    for directory in search:
        for name in ("docker-compose.yml", "compose.yaml", "compose.yml"):
            try:
                compose_text = (directory / name).read_text(encoding="utf-8")
                break
            except OSError:
                continue
        if compose_text:
            break
    return Database(
        name=unpack_compose_value(_service_setting(compose_text, "POSTGRES_DB"), env, "POSTGRES_DB", "verdict"),
        user=unpack_compose_value(_service_setting(compose_text, "POSTGRES_USER"), env, "POSTGRES_USER", "verdict"),
    )


def resolve_port(env: dict[str, str] | None = None) -> int:
    env = compose_env() if env is None else env
    try:
        return int(env.get("VERDICT_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def resolve_project(explicit: str | None) -> str | None:
    """``None`` means "whatever Compose calls the project in this directory"."""
    return explicit or os.environ.get("COMPOSE_PROJECT_NAME") or None


def compose_command(project: str | None, *args: str) -> list[str]:
    command = ["docker", "compose"]
    if project:
        command += ["-p", project]
    return command + list(args)


# ------------------------------------------------------------------ command layer


def _quote(token: str) -> str:
    return f'"{token}"' if any(char in token for char in ' \t"') else token


def format_command(command: list[str], stdin: Path | None = None, stdout: Path | None = None) -> str:
    """A copy-pasteable rendering of a step, with any redirection spelled out."""
    text = " ".join(_quote(token) for token in command)
    if stdin is not None:
        text += f" < {_quote(str(stdin))}"
    if stdout is not None:
        text += f" > {_quote(str(stdout))}"
    return text


def show_command(step: Step) -> str:
    """How a step is printed: a verbatim command, or the step's own summary line."""
    if step.display is not None:
        return step.display
    return format_command(step.command, step.stdin, step.stdout)


def run_command(
    command: list[str],
    *,
    stdin: Path | None = None,
    stdout: Path | None = None,
    cwd: Path | None = None,
) -> CommandResult:
    """Run one command. Raises BackupError if it cannot be started or fails."""
    stdin_handle = stdin.open("rb") if stdin is not None else None
    stdout_handle = stdout.open("wb") if stdout is not None else subprocess.PIPE
    try:
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdin=stdin_handle,
                stdout=stdout_handle,
                stderr=subprocess.PIPE,
                cwd=str(cwd) if cwd is not None else None,
            )
        except FileNotFoundError as exc:
            raise BackupError(f"{command[0]} was not found on PATH.") from exc
    finally:
        for handle in (stdin_handle, stdout_handle):
            if handle not in (None, subprocess.PIPE):
                handle.close()
    out = completed.stdout.decode("utf-8", "replace") if completed.stdout else ""
    return CommandResult(completed.returncode, out, completed.stderr.decode("utf-8", "replace"))


def run_step(step: Step) -> CommandResult:
    """Run one planned step, or fail the whole operation with a readable reason."""
    print(f"  $ {show_command(step)}")
    result = run_command(
        step.command, stdin=step.stdin, stdout=step.stdout, cwd=step.cwd
    )
    if result.returncode != 0 and step.required:
        detail = (result.stderr or result.stdout).strip().splitlines()
        tail = detail[-1] if detail else f"exit status {result.returncode}"
        raise BackupError(f"{step.description} failed: {tail}")
    if result.returncode != 0:
        print(f"    (optional step failed, continuing: {step.description})")
    return result


def print_plan(steps: list[Step], headline: str) -> None:
    print(headline)
    for step in steps:
        print(f"  {step.description}:")
        print(f"    {show_command(step)}")
    print("Dry run: nothing was executed.")


# ----------------------------------------------------------------------- manifest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_files(root: Path, *, exclude: tuple[str, ...] = ()) -> dict[str, dict]:
    """``relative/path`` -> ``{"bytes": n, "sha256": hex}`` for every file under ``root``."""
    files: dict[str, dict] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in exclude:
            continue
        files[relative] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    return dict(sorted(files.items()))


def build_manifest(
    *,
    created_at: str,
    files: dict[str, dict],
    fingerprint: dict,
    database: Database,
    project: str | None,
    commit: str | None,
    version: str | None,
    image_id: str | None,
) -> dict:
    return {
        "app": "verdict",
        "commit": commit,
        "compose_project": project,
        "contents": {
            "appdata": "uploaded media, secret_key and Ed25519 signing keys (the appdata volume)",
            "db": f"pg_dump --format=custom of {database.name} (the whole database)",
        },
        "contains_secrets": True,
        "created_at_utc": created_at,
        "database": {"name": database.name, "user": database.user},
        "fingerprint": fingerprint,
        "files": files,
        "manifest_version": MANIFEST_VERSION,
        "verdict_version": version,
        "web_image_id": image_id,
    }


def write_manifest(directory: Path, manifest: dict) -> Path:
    path = directory / MANIFEST_NAME
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def check_fingerprint(document: dict) -> dict:
    """Reject anything that is not a usable fingerprint, by name.

    The manifest arrives from disk, so a truncated or hand-edited file must fail
    here rather than halfway through a restore.
    """
    fingerprint = document.get("fingerprint")
    if not isinstance(fingerprint, dict) or not isinstance(fingerprint.get("rows"), dict):
        raise BackupError("the manifest carries no fingerprint with row counts")
    if not isinstance(fingerprint.get("audit"), dict) or "rows_total" not in fingerprint:
        raise BackupError("the manifest fingerprint is missing its audit head or total")
    for row in fingerprint.get("publications", []):
        if not isinstance(row, dict) or not isinstance(row.get("public_id"), str):
            raise BackupError("the manifest fingerprint has a malformed publication entry")
    return fingerprint


def read_manifest(directory: Path) -> dict:
    path = directory / MANIFEST_NAME
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BackupError(f"{path} is missing: this directory is not a complete backup.") from exc
    except (OSError, ValueError) as exc:
        raise BackupError(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("files"), dict):
        raise BackupError(f"{path} is not a VERDICT backup manifest.")
    check_fingerprint(document)
    return document


def verify_backup(directory: Path) -> VerifyReport:
    """Check every checksum in the manifest; an unlisted file is a warning, not a failure."""
    manifest = read_manifest(directory)
    problems: list[str] = []
    warnings: list[str] = []
    listed = manifest["files"]
    for relative, expected in sorted(listed.items()):
        path = directory / relative
        if not path.is_file():
            problems.append(f"missing file: {relative}")
            continue
        if sha256_file(path) != expected.get("sha256"):
            problems.append(f"checksum mismatch: {relative}")
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(directory).as_posix()
        if relative == MANIFEST_NAME or relative in listed:
            continue
        warnings.append(f"not listed in the manifest: {relative}")
    if (directory / INCOMPLETE_NAME).exists():
        # A marker that survived means the backup aborted before the manifest was
        # written; without this a hand-assembled directory would look verified.
        problems.append(f"{INCOMPLETE_NAME} is still present: the backup was never finished")
    return VerifyReport(manifest=manifest, problems=problems, warnings=warnings)


# --------------------------------------------------------------------- comparison


def fingerprints_match(expected: dict, actual: dict) -> tuple[bool, str]:
    """Compare two fingerprints and name the first difference.

    A restore is only proven when the whole document matches: the row counts, the
    audit head and every publication digest.
    """
    if expected == actual:
        return True, "identical"

    problems: list[str] = []
    for label in sorted(set(expected.get("rows", {})) | set(actual.get("rows", {}))):
        want = expected.get("rows", {}).get(label)
        have = actual.get("rows", {}).get(label)
        if want != have:
            problems.append(f"{label}: expected {want} rows, found {have}")
    want_audit = expected.get("audit", {})
    have_audit = actual.get("audit", {})
    if want_audit.get("head_sequence") != have_audit.get("head_sequence"):
        problems.append(
            f"audit head sequence: expected {want_audit.get('head_sequence')}, "
            f"found {have_audit.get('head_sequence')}"
        )
    if want_audit.get("head_hash") != have_audit.get("head_hash"):
        problems.append("audit head hash differs")
    if expected.get("publications") != actual.get("publications"):
        problems.append("publication digests differ")
    if want_audit.get("chains") != have_audit.get("chains"):
        problems.append("audit chain heads differ")
    shown = problems[:5]
    if len(problems) > len(shown):
        shown.append(f"... and {len(problems) - len(shown)} more differences")
    return False, "; ".join(shown) or "fingerprint documents differ"


#: ``manage.py verify_publication`` prints one line per check. These are the two
#: lines that must both appear for its overall verdict to be "identical".
REPRODUCIBLE_LINE = "Reproducible: reproducible"
UNCHANGED_LINE = "Unchanged since publication: unchanged since publication"


def publication_reports_identical(output: str) -> tuple[bool, str]:
    """Decide whether a ``verify_publication`` report means the publication is identical."""
    differences = [line.strip() for line in output.splitlines() if line.strip().startswith("-")]
    if REPRODUCIBLE_LINE not in output:
        detail = differences[0] if differences else "the publication no longer reproduces"
        return False, f"not reproducible ({detail})"
    if UNCHANGED_LINE not in output:
        detail = differences[0] if differences else "the live inputs moved"
        return False, f"changed since publication ({detail})"
    return True, "identical"


# --------------------------------------------------------------------------- plans


def plan_create(
    *, directory: Path, project: str | None, database: Database
) -> list[Step]:
    """Dump, fingerprint, copy the volume. The dump goes through ``db``: the web
    image has no PostgreSQL client and must not grow one.

    The fingerprint is taken right after the dump, because rows written between
    the two are in the live database but not in the backup; take a backup while
    the portal is idle and the two agree exactly.
    """
    return [
        Step(
            "dump the database",
            compose_command(
                project, "exec", "-T", DB_SERVICE,
                "pg_dump", "-U", database.user, "-d", database.name, "--format=custom",
            ),
            stdout=directory / DUMP_NAME,
        ),
        Step(
            "capture the fingerprint",
            compose_command(
                project, "exec", "-T", WEB_SERVICE, "python", "manage.py", "backup_fingerprint",
            ),
        ),
        Step(
            "copy the appdata volume out of web",
            compose_command(project, "cp", f"{WEB_SERVICE}:{DATA_MOUNT}", str(directory / APPDATA_NAME)),
        ),
        Step(
            "record the web image id",
            compose_command(project, "images", "-q", WEB_SERVICE),
            required=False,
        ),
        Step(
            "record the source commit",
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            required=False,
        ),
        Step(
            "record the source version",
            ["git", "describe", "--tags", "--always"],
            cwd=REPO_ROOT,
            required=False,
        ),
    ]


def plan_restore(*, directory: Path, project: str | None, database: Database) -> list[Step]:
    """Stop the writers, put the database and the volume back, start again."""
    return [
        Step(
            "stop the services that write",
            compose_command(project, "stop", WORKER_SERVICE, WEB_SERVICE),
        ),
        Step(
            "restore the database",
            compose_command(
                project, "exec", "-T", DB_SERVICE,
                "pg_restore", "-U", database.user, "-d", database.name,
                "--clean", "--if-exists", "--no-owner",
            ),
            stdin=directory / DUMP_NAME,
        ),
        Step(
            "copy the appdata volume back into web",
            compose_command(project, "cp", str(directory / APPDATA_NAME), f"{WEB_SERVICE}:{DATA_MOUNT}"),
        ),
        Step(
            "start the services",
            compose_command(project, "up", "-d", WEB_SERVICE, WORKER_SERVICE),
        ),
    ]


# ---------------------------------------------------------------------------- create


def create_backup(args: argparse.Namespace) -> int:
    directory = Path(args.out).resolve() / f"verdict-{utc_stamp()}"
    database = resolve_database()
    project = resolve_project(args.project)
    dump, fingerprint_step, appdata, image, commit, version = plan_create(
        directory=directory, project=project, database=database
    )
    steps = [dump, fingerprint_step, appdata, image, commit, version]

    if args.dry_run:
        print_plan(
            steps,
            f"Would create {directory} "
            f"(database {database.name}, project {project or 'the current directory'}).",
        )
        return 0

    if directory.exists():
        print(f"BACKUP FAILED: {directory} already exists.")
        return 1
    try:
        directory.mkdir(parents=True)
        (directory / INCOMPLETE_NAME).write_text("in progress\n", encoding="utf-8", newline="\n")
        (directory / APPDATA_NAME).mkdir()
        print(f"Writing {directory}")

        run_step(dump)
        if not (directory / DUMP_NAME).is_file():
            raise BackupError(f"{DUMP_NAME} was not written; the dump step produced nothing.")
        fingerprint = _parse_fingerprint(run_step(fingerprint_step).stdout)
        run_step(appdata)
        _flatten_nested_appdata(directory / APPDATA_NAME)
        image_id = run_step(image).stdout.strip() or None
        source_commit = run_step(commit).stdout.strip() or None
        source_version = run_step(version).stdout.strip() or None

        files = collect_files(directory, exclude=(MANIFEST_NAME, INCOMPLETE_NAME))
        manifest = build_manifest(
            created_at=utc_now(),
            files=files,
            fingerprint=fingerprint,
            database=database,
            project=project,
            commit=source_commit,
            version=source_version,
            image_id=image_id,
        )
        (directory / INCOMPLETE_NAME).unlink()
        write_manifest(directory, manifest)
    except (BackupError, OSError) as exc:
        print(f"BACKUP FAILED: {exc}")
        print(f"{directory} is incomplete (no {MANIFEST_NAME}); delete it or retry.")
        return 1

    rows = manifest["fingerprint"]["rows_total"]
    head = manifest["fingerprint"]["audit"]["head_sequence"]
    print()
    print(f"BACKUP OK: {directory}")
    print(f"  {len(files)} files, {rows} rows, audit head {head}, "
          f"{len(manifest['fingerprint']['publications'])} publications.")
    print("  It contains the database, uploaded media, the Django secret key and the")
    print("  Ed25519 signing keys. Store it like a password, and keep it out of git.")
    return 0


def _flatten_nested_appdata(appdata: Path) -> None:
    """Lift ``appdata/appdata/*`` up when ``compose cp`` nested the volume."""
    nested = appdata / APPDATA_NAME
    if not (nested.is_dir() and (nested / "secret_key").is_file()):
        return
    for child in sorted(nested.iterdir()):
        child.replace(appdata / child.name)
    nested.rmdir()
    print(f"  note: the volume landed in {APPDATA_NAME}/{APPDATA_NAME}; lifted it up.")


def _parse_fingerprint(output: str) -> dict:
    try:
        document = json.loads(output)
    except ValueError as exc:
        raise BackupError(f"backup_fingerprint did not return JSON: {exc}") from exc
    if not isinstance(document, dict) or "rows" not in document:
        raise BackupError("backup_fingerprint returned an unexpected document.")
    return document


# --------------------------------------------------------------------------- verify


def verify_command(args: argparse.Namespace) -> int:
    directory = Path(args.directory).resolve()
    try:
        report = verify_backup(directory)
    except BackupError as exc:
        print(f"VERIFY FAILED: {exc}")
        return 1
    for warning in report.warnings:
        print(f"  warning: {warning}")
    if not report.ok:
        for problem in report.problems:
            print(f"  {problem}")
        print(f"VERIFY FAILED: {len(report.problems)} problem(s) in {directory}")
        return 1
    files = report.manifest["files"]
    print(
        f"VERIFY OK: {len(files)} files match {directory / MANIFEST_NAME} "
        f"({report.manifest['created_at_utc']})."
    )
    return 0


# ------------------------------------------------------------------------- restore


def restore_backup(args: argparse.Namespace) -> int:
    directory = Path(args.directory).resolve()
    database = resolve_database()
    project = resolve_project(args.project)
    steps = plan_restore(directory=directory, project=project, database=database)
    layout_step = Step(
        "check the appdata layout inside web",
        compose_command(project, "exec", "-T", WEB_SERVICE, "python", "-c",
                        APPDATA_LAYOUT_CODE, DATA_MOUNT),
        display=f"docker compose exec -T {WEB_SERVICE} python -c "
                "<inline appdata layout check, see APPDATA_LAYOUT_CODE>",
    )

    if not args.dry_run and not args.yes:
        print("RESTORE FAILED: --yes is required. A restore replaces the live database "
              "and the appdata volume; re-run with --yes once you are sure.")
        return 1

    try:
        report = verify_backup(directory)
    except BackupError as exc:
        print(f"RESTORE FAILED: {exc}")
        return 1
    for warning in report.warnings:
        print(f"  warning: {warning}")
    if not report.ok:
        for problem in report.problems:
            print(f"  {problem}")
        print(f"RESTORE FAILED: the backup in {directory} is not intact.")
        return 1
    manifest = report.manifest
    print(f"Checksums OK: {len(manifest['files'])} files.")

    if args.dry_run:
        print_plan(
            [*steps, layout_step],
            f"Would restore {directory} into the running Compose project "
            f"({project or 'the current directory'}), then compare the fingerprint and "
            f"re-verify {len(manifest['fingerprint'].get('publications', []))} publication(s).",
        )
        return 0

    port = resolve_port()
    try:
        for step in steps:
            run_step(step)
        layout = run_step(layout_step)
        if "moved up" in layout.stdout:
            # The secret key changed under a running process: restart so the app
            # signs with the restored key rather than a freshly generated one.
            run_step(Step("restart after lifting the appdata directory",
                          compose_command(project, "restart", WEB_SERVICE, WORKER_SERVICE)))
        _wait_for_health(port, args.timeout)
        fingerprint = _parse_fingerprint(
            run_step(
                Step("re-read the fingerprint",
                     compose_command(project, "exec", "-T", WEB_SERVICE,
                                     "python", "manage.py", "backup_fingerprint"))
            ).stdout
        )
        matched, reason = fingerprints_match(manifest["fingerprint"], fingerprint)
        if not matched:
            print(f"RESTORE FAILED: the restored portal is not the backed-up portal: {reason}")
            return 1
        print("Fingerprint matches the manifest exactly.")
        verified, total = _verify_publications(manifest, project)
    except (BackupError, OSError) as exc:
        print(f"RESTORE FAILED: {exc}")
        print("The services were left as they are; fix the cause and re-run, or restore again.")
        return 1

    if verified != total:
        print(f"RESTORE FAILED: {verified}/{total} publications verified as identical.")
        return 1
    print(
        f"RESTORE OK: {manifest['fingerprint']['rows_total']} rows, "
        f"audit head {manifest['fingerprint']['audit']['head_sequence']}, "
        f"{total}/{total} publications verified"
    )
    return 0


def _verify_publications(manifest: dict, project: str | None) -> tuple[int, int]:
    """Re-run ``verify_publication`` for every publication the manifest lists."""
    publications = manifest["fingerprint"].get("publications", [])
    verified = 0
    for row in publications:
        result = run_step(
            Step(
                f"verify publication {row['public_id']}",
                compose_command(project, "exec", "-T", WEB_SERVICE,
                                "python", "manage.py", "verify_publication", row["public_id"]),
                )
        )
        ok, reason = publication_reports_identical(result.stdout)
        print(f"  {row['public_id']}: {reason}")
        verified += 1 if ok else 0
    return verified, len(publications)


def _wait_for_health(port: int, timeout: int) -> None:
    """Poll /healthz on the published port until the restored portal answers."""
    url = f"http://localhost:{port}{HEALTH_PATH}"
    deadline = time.monotonic() + timeout
    print(f"Waiting for {url} (up to {timeout}s)")
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    print("  healthz OK")
                    return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            raise BackupError(f"{url} did not answer within {timeout}s.")
        time.sleep(2)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backup.py", description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="A backup contains the secret key and the signing keys: store it like a password.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Back up the database and the appdata volume.")
    create.add_argument("--out", default="backups", help="Directory to write the backup into.")
    create.add_argument("--project", default=None, help="Compose project (default: this directory).")
    create.add_argument("--dry-run", action="store_true", help="Print the commands and stop.")
    create.set_defaults(handler=create_backup)

    verify = subparsers.add_parser("verify", help="Check every checksum in a backup.")
    verify.add_argument("directory", help="Backup directory to verify.")
    verify.set_defaults(handler=verify_command)

    restore = subparsers.add_parser("restore", help="Restore a backup onto this Compose project.")
    restore.add_argument("directory", help="Backup directory to restore.")
    restore.add_argument("--yes", action="store_true", help="Required: this replaces live data.")
    restore.add_argument("--project", default=None, help="Compose project (default: this directory).")
    restore.add_argument("--timeout", type=int, default=HEALTH_TIMEOUT_SECONDS,
                         help="Seconds to wait for /healthz (default: %(default)s).")
    restore.add_argument("--dry-run", action="store_true", help="Print the commands and stop.")
    restore.set_defaults(handler=restore_backup)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (BackupError, OSError) as exc:
        print(f"{args.command.upper()} FAILED: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
