"""Tests for ``scripts/backup.py``: the host-side backup, verify and restore tool.

No Docker and no Django: everything here runs against a synthetic backup
directory built with the script's own manifest helpers. The point of the tests is
that a tampered backup is refused, a dry run prints exactly what it would run, a
restore cannot start without ``--yes``, and the fingerprint comparison names the
difference it found instead of just saying "no".
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    """Import scripts/backup.py by path; it is a script, not an installed module."""
    spec = importlib.util.spec_from_file_location("verdict_backup_script", REPO_ROOT / "scripts" / "backup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup = _load_script()

FINGERPRINT = {
    "audit": {
        "chains": [
            {"chain_id": "ach_test1", "event_slug": "demo", "head_hash": "c" * 64, "sequence": 4}
        ],
        "entries": 4,
        "head_chain_id": "ach_test1",
        "head_hash": "c" * 64,
        "head_sequence": 4,
    },
    "fingerprint_version": 1,
    "publications": [
        {"event": "demo", "input_digest": "a" * 64, "public_id": "pub_test1", "version": 1}
    ],
    "rows": {"accounts.user": 2, "events.event": 1, "results.resultpublication": 1},
    "rows_total": 4,
}

#: The report manage.py verify_publication prints for a publication that still
#: reproduces from its stored inputs and whose live data has not moved.
IDENTICAL_REPORT = """\
Verifying publication pub_test1 ...
  Event:        demo
  Method:       borda
  Published at: 2026-09-28T09:00:00+00:00
  Stored digest: aaaa
  Reproducible: reproducible
    Stored inputs reproduce the publication rows and awards.
  Unchanged since publication: unchanged since publication
  Stored digest: aaaa
  Live digest:   aaaa
"""

MOVED_REPORT = """\
Verifying publication pub_test1 ...
  Reproducible: reproducible
    Stored inputs reproduce the publication rows and awards.
  Unchanged since publication: changed since publication
    - judging_review 5: score 4 became 5
"""


class BackupScriptTestCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.addCleanup(self._temp.cleanup)
        self.out = self.root / "out"
        self.directory = self.make_backup()

    # ------------------------------------------------------------------ helpers
    def make_backup(self, fingerprint=None) -> Path:
        """A complete backup directory: db.dump, appdata/ and a manifest."""
        directory = self.root / f"verdict-20260928T101500Z-{len(list(self.root.iterdir()))}"
        (directory / "appdata" / "keys").mkdir(parents=True)
        (directory / "db.dump").write_bytes(b"PGDMP\x00fake custom format dump")
        (directory / "appdata" / "secret_key").write_text("s" * 64, encoding="utf-8")
        (directory / "appdata" / "keys" / "kid_test1.json").write_text("{}", encoding="utf-8")
        manifest = backup.build_manifest(
            created_at="2026-09-28T10:15:00Z",
            files=backup.collect_files(directory),
            fingerprint=json.loads(json.dumps(fingerprint if fingerprint is not None else FINGERPRINT)),
            database=backup.Database("verdict", "verdict"),
            project=None,
            commit="deadbeef",
            version="v1.0.0",
            image_id="sha256:local",
        )
        backup.write_manifest(directory, manifest)
        return directory

    def run_main(self, argv) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = backup.main(argv)
        return code, out.getvalue()

    def no_docker(self):
        """Fail the test if anything tries to execute a command."""
        def forbidden(*args, **kwargs):
            raise AssertionError(f"the script executed a command: {args!r}")

        backup.run_command = forbidden
        self.addCleanup(setattr, backup, "run_command", backup.run_command)


class ManifestTests(BackupScriptTestCase):
    def test_a_complete_backup_verifies(self):
        code, output = self.run_main(["verify", str(self.directory)])
        self.assertEqual(code, 0)
        self.assertIn("VERIFY OK", output)
        self.assertIn("3 files", output)

    def test_manifest_lists_every_file_and_the_fingerprint(self):
        manifest = backup.read_manifest(self.directory)
        self.assertEqual(
            sorted(manifest["files"]),
            ["appdata/keys/kid_test1.json", "appdata/secret_key", "db.dump"],
        )
        self.assertEqual(manifest["fingerprint"], FINGERPRINT)
        self.assertTrue(manifest["contains_secrets"])
        self.assertEqual(manifest["database"], {"name": "verdict", "user": "verdict"})

    def test_one_byte_tampered_in_the_dump_is_refused_and_named(self):
        path = self.directory / "db.dump"
        data = bytearray(path.read_bytes())
        data[3] ^= 0x01
        path.write_bytes(bytes(data))
        code, output = self.run_main(["verify", str(self.directory)])
        self.assertEqual(code, 1)
        self.assertIn("checksum mismatch: db.dump", output)
        self.assertIn("VERIFY FAILED", output)

    def test_one_byte_tampered_in_appdata_is_refused_and_named(self):
        path = self.directory / "appdata" / "secret_key"
        text = path.read_text(encoding="utf-8")
        path.write_text("S" + text[1:], encoding="utf-8")
        code, output = self.run_main(["verify", str(self.directory)])
        self.assertEqual(code, 1)
        self.assertIn("checksum mismatch: appdata/secret_key", output)

    def test_a_missing_file_is_refused(self):
        (self.directory / "appdata" / "secret_key").unlink()
        code, output = self.run_main(["verify", str(self.directory)])
        self.assertEqual(code, 1)
        self.assertIn("missing file: appdata/secret_key", output)

    def test_an_unfinished_backup_is_refused(self):
        (self.directory / backup.INCOMPLETE_NAME).write_text("in progress\n", encoding="utf-8")
        code, output = self.run_main(["verify", str(self.directory)])
        self.assertEqual(code, 1)
        self.assertIn("never finished", output)

    def test_an_unlisted_file_is_a_warning_not_a_failure(self):
        (self.directory / "appdata" / "leftover.txt").write_text("x", encoding="utf-8")
        code, output = self.run_main(["verify", str(self.directory)])
        self.assertEqual(code, 0)
        self.assertIn("warning: not listed in the manifest: appdata/leftover.txt", output)

    def test_a_directory_without_a_manifest_is_refused(self):
        empty = self.root / "not-a-backup"
        empty.mkdir()
        code, output = self.run_main(["verify", str(empty)])
        self.assertEqual(code, 1)
        self.assertIn("not a complete backup", output)

    def test_a_tampered_manifest_is_refused_before_any_restore(self):
        manifest = backup.read_manifest(self.directory)
        manifest["files"]["db.dump"]["sha256"] = "0" * 64
        backup.write_manifest(self.directory, manifest)
        self.no_docker()
        code, output = self.run_main(["restore", str(self.directory), "--yes", "--dry-run"])
        self.assertEqual(code, 1)
        self.assertIn("checksum mismatch: db.dump", output)
        self.assertIn("RESTORE FAILED", output)


class DryRunTests(BackupScriptTestCase):
    def test_create_dry_run_prints_the_docker_commands_and_writes_nothing(self):
        self.no_docker()
        code, output = self.run_main(["create", "--out", str(self.out), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("Would create", output)
        self.assertIn(
            "docker compose exec -T db pg_dump -U verdict -d verdict --format=custom", output
        )
        self.assertIn("docker compose exec -T web python manage.py backup_fingerprint", output)
        self.assertIn("docker compose cp web:/data", output)
        self.assertIn("appdata", output)
        self.assertIn("Dry run: nothing was executed.", output)
        self.assertFalse(self.out.exists())

    def test_create_dry_run_respects_the_requested_project(self):
        self.no_docker()
        _, output = self.run_main(
            ["create", "--out", str(self.out), "--dry-run", "--project", "verdict-demo"]
        )
        self.assertIn("docker compose -p verdict-demo exec -T db pg_dump", output)
        self.assertIn("docker compose -p verdict-demo cp web:/data", output)

    def test_create_dry_run_never_prints_the_database_password(self):
        self.no_docker()
        previous = os.environ.get("POSTGRES_PASSWORD")
        os.environ["POSTGRES_PASSWORD"] = "correct-horse-battery-staple"
        self.addCleanup(os.environ.pop, "POSTGRES_PASSWORD", None)
        if previous is not None:
            self.addCleanup(os.environ.__setitem__, "POSTGRES_PASSWORD", previous)
        _, output = self.run_main(["create", "--out", str(self.out), "--dry-run"])
        self.assertNotIn("correct-horse-battery-staple", output)
        self.assertNotIn("POSTGRES_PASSWORD", output)

    def test_restore_dry_run_prints_the_restore_commands(self):
        self.no_docker()
        code, output = self.run_main(["restore", str(self.directory), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("Checksums OK", output)
        self.assertIn("docker compose stop webhook-worker web", output)
        self.assertIn(
            "docker compose exec -T db pg_restore -U verdict -d verdict "
            "--clean --if-exists --no-owner", output
        )
        self.assertIn("db.dump", output)
        self.assertIn(f"docker compose cp {self.directory / 'appdata'} web:/data", output)
        self.assertIn("docker compose up -d web webhook-worker", output)
        self.assertIn("Dry run: nothing was executed.", output)

    def test_restore_dry_run_does_not_need_confirmation(self):
        self.no_docker()
        code, _ = self.run_main(["restore", str(self.directory), "--dry-run"])
        self.assertEqual(code, 0)


class RestoreGuardTests(BackupScriptTestCase):
    def test_restore_without_yes_is_refused_and_runs_nothing(self):
        self.no_docker()
        code, output = self.run_main(["restore", str(self.directory)])
        self.assertEqual(code, 1)
        self.assertIn("RESTORE FAILED: --yes is required", output)

    def test_restore_verifies_the_backup_before_anything_else(self):
        (self.directory / "db.dump").write_bytes(b"tampered")
        self.no_docker()
        code, output = self.run_main(["restore", str(self.directory), "--yes", "--dry-run"])
        self.assertEqual(code, 1)
        self.assertIn("checksum mismatch: db.dump", output)
        self.assertNotIn("pg_restore", output)


class FingerprintComparisonTests(BackupScriptTestCase):
    def test_equal_fingerprints_match(self):
        matched, reason = backup.fingerprints_match(FINGERPRINT, json.loads(json.dumps(FINGERPRINT)))
        self.assertTrue(matched)
        self.assertEqual(reason, "identical")

    def test_a_missing_row_is_named(self):
        actual = json.loads(json.dumps(FINGERPRINT))
        actual["rows"]["events.event"] = 0
        matched, reason = backup.fingerprints_match(FINGERPRINT, actual)
        self.assertFalse(matched)
        self.assertIn("events.event: expected 1 rows, found 0", reason)

    def test_a_missing_table_is_named(self):
        actual = json.loads(json.dumps(FINGERPRINT))
        del actual["rows"]["accounts.user"]
        matched, reason = backup.fingerprints_match(FINGERPRINT, actual)
        self.assertFalse(matched)
        self.assertIn("accounts.user: expected 2 rows, found None", reason)

    def test_a_shortened_audit_chain_is_named(self):
        actual = json.loads(json.dumps(FINGERPRINT))
        actual["audit"]["head_sequence"] = 3
        actual["audit"]["head_hash"] = "d" * 64
        matched, reason = backup.fingerprints_match(FINGERPRINT, actual)
        self.assertFalse(matched)
        self.assertIn("audit head sequence: expected 4, found 3", reason)
        self.assertIn("audit head hash differs", reason)

    def test_a_changed_publication_digest_is_named(self):
        actual = json.loads(json.dumps(FINGERPRINT))
        actual["publications"][0]["input_digest"] = "b" * 64
        matched, reason = backup.fingerprints_match(FINGERPRINT, actual)
        self.assertFalse(matched)
        self.assertIn("publication digests differ", reason)

    def test_a_missing_publication_is_named(self):
        actual = json.loads(json.dumps(FINGERPRINT))
        actual["publications"] = []
        matched, reason = backup.fingerprints_match(FINGERPRINT, actual)
        self.assertFalse(matched)
        self.assertIn("publication digests differ", reason)

    def test_rewritten_chain_heads_are_named(self):
        actual = json.loads(json.dumps(FINGERPRINT))
        actual["audit"]["chains"][0]["head_hash"] = "e" * 64
        matched, reason = backup.fingerprints_match(FINGERPRINT, actual)
        self.assertFalse(matched)
        self.assertIn("audit chain heads differ", reason)


class PublicationReportTests(BackupScriptTestCase):
    def test_an_identical_report_is_accepted(self):
        matched, reason = backup.publication_reports_identical(IDENTICAL_REPORT)
        self.assertTrue(matched)
        self.assertEqual(reason, "identical")

    def test_a_changed_report_is_refused_with_the_difference(self):
        matched, reason = backup.publication_reports_identical(MOVED_REPORT)
        self.assertFalse(matched)
        self.assertIn("changed since publication", reason)
        self.assertIn("score 4 became 5", reason)

    def test_a_report_that_does_not_reproduce_is_refused(self):
        report = IDENTICAL_REPORT.replace(
            "  Reproducible: reproducible", "  Reproducible: not reproducible"
        ).replace("Reproducible: not reproducible", "Reproducible: not reproducible")
        matched, reason = backup.publication_reports_identical(report)
        self.assertFalse(matched)
        self.assertIn("not reproducible", reason)

    def test_an_empty_report_is_refused(self):
        matched, reason = backup.publication_reports_identical("")
        self.assertFalse(matched)
        self.assertIn("not reproducible", reason)


class AppDataLayoutTests(BackupScriptTestCase):
    """The check that runs inside the container after appdata is copied back.

    ``docker compose cp <dir> web:/data`` can drop the tree at ``/data/appdata``
    instead of merging it into ``/data``; if that happens the portal would come up
    with a freshly generated secret key, so the restore lifts the tree and says so.
    """

    def run_layout_check(self, data: Path) -> str:
        result = subprocess.run(
            [sys.executable, "-c", backup.APPDATA_LAYOUT_CODE, str(data)],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_a_correct_layout_is_left_alone(self):
        data = self.directory / "appdata"
        output = self.run_layout_check(data)
        self.assertIn("layout ok", output)
        self.assertTrue((data / "secret_key").is_file())

    def test_a_nested_copy_is_lifted_into_the_volume(self):
        data = self.directory / "appdata"
        nested = data / "appdata"
        nested.mkdir()
        (nested / "secret_key").write_text("r" * 64, encoding="utf-8")
        (nested / "keys").mkdir()
        (nested / "keys" / "kid_test1.json").write_text("{}", encoding="utf-8")
        output = self.run_layout_check(data)
        self.assertIn("moved up", output)
        self.assertFalse(nested.exists())
        self.assertEqual((data / "secret_key").read_text(encoding="utf-8"), "r" * 64)
        self.assertTrue((data / "keys" / "kid_test1.json").is_file())


class ComposeSettingsTests(BackupScriptTestCase):
    def test_the_database_comes_from_the_compose_file(self):
        self.assertEqual(
            backup.resolve_database(REPO_ROOT), backup.Database(name="verdict", user="verdict")
        )

    def test_a_compose_variable_is_resolved_from_the_environment(self):
        # The compose file names the database literally; the same ${VAR:-default}
        # shape it uses for the password must resolve the same way.
        self.assertEqual(
            backup.unpack_compose_value("${POSTGRES_DB:-verdict}", {"POSTGRES_DB": "live"},
                                        "POSTGRES_DB", "verdict"),
            "live",
        )
        self.assertEqual(
            backup.unpack_compose_value("${POSTGRES_DB:-verdict}", {}, "POSTGRES_DB", "verdict"),
            "verdict",
        )
        self.assertEqual(
            backup.unpack_compose_value("${POSTGRES_DB}", {}, "POSTGRES_DB", "verdict"), "verdict"
        )
        # A literal in the compose file is the deployment's answer; env cannot override it.
        self.assertEqual(
            backup.unpack_compose_value("verdict", {"POSTGRES_DB": "live"}, "POSTGRES_DB", "verdict"),
            "verdict",
        )

    def test_the_project_is_only_passed_when_it_was_asked_for(self):
        self.assertEqual(backup.compose_command(None, "ps"), ["docker", "compose", "ps"])
        self.assertEqual(backup.compose_command("demo", "ps"), ["docker", "compose", "-p", "demo", "ps"])

    def test_paths_with_spaces_are_quoted(self):
        rendered = backup.format_command(
            ["docker", "compose", "cp", "web:/data", str(self.directory / "my backups")],
            stdout=self.directory / "db dump",
        )
        self.assertIn(f'"{self.directory / "my backups"}"', rendered)
        self.assertIn(f'> "{self.directory / "db dump"}"', rendered)

    def test_a_plan_never_embeds_a_password(self):
        steps = backup.plan_create(
            directory=self.directory,
            project=None,
            database=backup.Database(name="verdict", user="verdict"),
        )
        rendered = " ".join(backup.show_command(step) for step in steps)
        self.assertNotIn("PGPASSWORD", rendered)
        self.assertIn("pg_dump", rendered)


if __name__ == "__main__":
    unittest.main()
