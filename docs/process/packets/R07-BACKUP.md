# R07: Tested backup and restore

Read `AGENTS.md` first. PostgreSQL only; use the `DATABASE_URL` you are given.

## Why

Adoptability and operability are a fifth of the score. An organizer running a
real event needs one command to back up everything that matters and one to
restore it onto a fresh machine, with proof that the restored portal is the same
portal: same rows, same audit chain, same publications that still verify.

## What a backup contains

- The PostgreSQL database (`pg_dump --format=custom`), taken from the `db`
  service with `docker compose exec -T db pg_dump ...` (the web image has no
  PostgreSQL client; do not add one).
- The `appdata` volume mounted at `/data` in `web` (uploaded media,
  `secret_key`, Ed25519 signing keys under `keys/`), copied with
  `docker compose cp web:/data <dir>/appdata`.
- `manifest.json`: UTC timestamp, VERDICT version/commit if available, the web
  image id, sha256 of every file in the backup, and the fingerprint below.
- Credentials and keys are inside the backup: say so in the docs and in the
  command output ("store this like a password").

## Fingerprint: `python manage.py backup_fingerprint` (new, in `src/core`)

Prints deterministic JSON (sorted keys): row counts for every VERDICT model,
the audit chain head (latest sequence number and hash), and every result
publication's `public_id`, version and stored input digest. Read-only. Unit
tested: output stable across two calls; changes when a row is added; includes
publication digests; no secrets (no password hashes, tokens, key material or
emails) in the output.

## `scripts/backup.py` (host-side, standard library only, Windows/macOS/Linux)

- `python scripts/backup.py create [--out backups]` -> writes
  `backups/verdict-<UTC stamp>/` with `db.dump`, `appdata/`, `manifest.json`
  (fingerprint captured with `docker compose exec -T web python manage.py
  backup_fingerprint`). Fails loudly if any step fails; never leaves a
  half-written directory looking complete (write the manifest last).
- `python scripts/backup.py verify <dir>` -> checks every checksum in the
  manifest; exit 1 and name the file on any mismatch.
- `python scripts/backup.py restore <dir> --yes` -> refuses without `--yes`;
  runs `verify` first; stops `web` and `webhook-worker`; restores the database
  with `pg_restore --clean --if-exists --no-owner` through the `db` service;
  copies `appdata` back into the web container's `/data`; starts the services
  and waits for `/healthz`; captures the fingerprint again and requires it to
  equal the manifest fingerprint exactly; then runs `python manage.py
  verify_publication <pub_id>` for every publication in the manifest and
  requires each to report identical. Prints one final line:
  `RESTORE OK: <n> rows, audit head <seq>, <k>/<k> publications verified` or
  `RESTORE FAILED: <reason>` (exit 1).
- `--dry-run` on create and restore prints the exact docker commands without
  running them.
- Uses the compose project in the current directory (respect
  `COMPOSE_PROJECT_NAME` / `-p` if given via `--project`), reads DB name/user
  from the compose environment (`.env` / `.env.example` defaults), never prints
  passwords.

## Tests

- `tests/test_backup_fingerprint.py` (Django, PostgreSQL) for the command.
- `tests/test_backup_script.py` (plain unittest, no Docker): manifest checksum
  verification (tamper one byte -> refused), `--dry-run` command lines for
  create and restore, restore without `--yes` refused, the fingerprint
  comparison helper. Import the script module by path.

## Docs: `docs/BACKUP-RESTORE.md`

What is included and excluded (exports are not backups), security of the
backup (it contains the secret key and signing keys), the three commands, how to
move a portal to a new machine (backup -> copy dir -> fresh `docker compose up`
-> restore), what "RESTORE OK" proves, and a "Recorded run" section left with a
placeholder heading only: the orchestrator runs the real end-to-end restore on
Docker and pastes the actual output there. Do not invent output.

## Scope

Touch only `scripts/backup.py` (new), `src/core/management/commands/backup_fingerprint.py`
(new), `tests/test_backup_fingerprint.py` (new), `tests/test_backup_script.py`
(new), `docs/BACKUP-RESTORE.md` (new). Do not run Docker. Other workers are
editing `src/results/**`, `src/judging/**`, `src/projects/**`,
`src/core/showcase.py`, `src/core/bootstrap.py`, templates and `JUDGING.md`:
do not touch them. Never git commit.

## Done means

`manage.py test tests.test_backup_fingerprint` and
`.venv\Scripts\python.exe -m unittest tests.test_backup_script` pass. Finish with
SUMMARY / FILES / TESTS / GAPS as `AGENTS.md` says.
