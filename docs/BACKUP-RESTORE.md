# Backup and restore

One command backs up everything that matters about a running VERDICT portal, and
one command puts it back on a machine that has never seen it - and then proves,
rather than claims, that the restored portal is the same portal.

```
python scripts/backup.py create [--out backups]     # back up
python scripts/backup.py verify <dir>                # check the archive
python scripts/backup.py restore <dir> --yes         # put it back, and prove it
```

`scripts/backup.py` is standard-library only and runs on the host with Python 3.10+
(Windows, macOS or Linux); the portal itself runs on 3.12. It needs `docker` and
`docker compose` on `PATH`, and it operates on the Compose project in the current
directory. Pass `--project NAME` (or set `COMPOSE_PROJECT_NAME`) if the project is
not the current directory's.

## What a backup contains

`create` writes `backups/verdict-<UTC timestamp>/`:

| Path | What it is | How it is taken |
| --- | --- | --- |
| `db.dump` | The whole PostgreSQL database in `pg_dump --format=custom` | `docker compose exec -T db pg_dump ...` |
| `appdata/` | The `appdata` volume: uploaded media, `secret_key`, Ed25519 signing keys under `keys/`, collected static files | `docker compose cp web:/data <dir>/appdata` |
| `manifest.json` | UTC timestamp, web image id, source commit, the sha256 and size of every file above, and the portal fingerprint | written last |

The dump always goes through the `db` service: the web image deliberately carries
no PostgreSQL client, and adding one would weaken that.

The `manifest.json` is written last, and an `.incomplete` marker is removed
immediately before it. A backup that failed halfway has no manifest and keeps its
marker, so `verify` and `restore` both refuse it. There is no such thing as a
half-written directory that looks like a backup.

### The fingerprint

`manifest.json` carries the output of `python manage.py backup_fingerprint`
(same command, inside the container). It is deterministic JSON with sorted keys
and no timestamp, so the same portal state always produces the same bytes:

```json
{
  "audit": {"chains": [...], "entries": 412, "head_chain_id": "ach_...",
            "head_hash": "<64 hex>", "head_sequence": 412},
  "fingerprint_version": 1,
  "publications": [{"event": "...", "input_digest": "<64 hex>",
                    "public_id": "pub_...", "version": 2}],
  "rows": {"accounts.user": 84, "events.event": 3, "...": 0},
  "rows_total": 2093
}
```

* `rows` - one count per concrete model, keyed by `app_label.model_name`.
* `audit` - the latest audit entry's sequence and hash, plus every chain head.
* `publications` - every result publication's public id, version and stored input
  digest, so each decision record can be re-verified after the restore.

The command is read-only and never reads secrets: no password hashes, no API or
reset tokens, no email addresses, no key material. Run it yourself at any time:

```
docker compose exec -T web python manage.py backup_fingerprint
```

### What is *not* in a backup

* **Exports are not backups.** The CSV exports, the decision record download and
  the audit chain export are read-only views for judges and organizers. They carry
  no accounts, no media, no secret key and no ability to restore anything.
* The source tree and the Docker image are not in the archive. The manifest
  records the commit and the image id when they are available, so rebuild the
  same version with `docker compose up --build` on the new machine.
* Nothing outside the `db` service's PostgreSQL database and the `appdata` volume
  is captured. Compose networks, volumes you added yourself, and anything mounted
  from the host are out of scope.

## Security: a backup is a credential

The archive contains the whole database **and** the `appdata` volume, which holds:

* the Django `SECRET_KEY` - the key that signs sessions and any signed cookie;
* the Ed25519 signing keys under `keys/` - with them, anyone can mint a signed
  judge record that `scripts/verify_record.py` will accept;
* every account's email address and password hash, and every API token hash.

Anyone who holds this directory owns the portal. Treat it exactly like a password
file:

* keep it out of version control - add `backups/` to `.gitignore` (and to whatever
  your CI uploads);
* encrypt it at rest, and keep one copy somewhere the machine cannot reach;
* do not email it, paste it in chat, or attach it to a ticket;
* when you no longer need a backup, delete the whole directory, not just the dump.

`create` says so in its output, and `manifest.json` carries
`"contains_secrets": true` so a script reading the archive cannot miss it.

## The three commands

### create

```
python scripts/backup.py create
python scripts/backup.py create --out /media/backup-disk --project my-verdict
python scripts/backup.py create --dry-run        # print every command, run nothing
```

The `web` service must be running (the fingerprint is read through it). Take a
backup while the portal is idle - before submissions open, or after the event. The
dump and the fingerprint are two separate reads, so rows written *between* them
exist in the live database but not in the backup; the restore comparison will then
report the difference instead of hiding it. Idle is the only way to get an exact
match, and the run is a couple of seconds.

Any failed step aborts with `BACKUP FAILED: <reason>` and exit status 1. Nothing
partial is ever presented as complete.

### verify

```
python scripts/backup.py verify backups/verdict-20260928T101500Z
```

Recomputes the sha256 of every file in the manifest. Any mismatch, missing file,
leftover `.incomplete` marker or absent manifest exits 1 and names the file. A file
that is present but *not* listed in the manifest is reported as a warning: it is
not part of the verified backup, and it will still be copied back by a restore.

### restore

```
python scripts/backup.py restore backups/verdict-20260928T101500Z            # refuses
python scripts/backup.py restore backups/verdict-20260928T101500Z --dry-run  # print only
python scripts/backup.py restore backups/verdict-20260928T101500Z --yes      # do it
```

`--yes` is mandatory. A restore destroys the live database and overwrites the live
`appdata` volume; the script will not do that on a guess. In order, a restore:

1. verifies every checksum in the manifest and stops on the first problem;
2. stops `webhook-worker` and `web` so nothing writes during the swap;
3. restores the database with `pg_restore --clean --if-exists --no-owner` through
   the `db` service;
4. copies `appdata/` back into the web container's `/data`;
5. starts `web` and `webhook-worker` and waits for `/healthz` (up to
   `--timeout` seconds, default 180);
6. re-reads the fingerprint and requires it to equal the manifest fingerprint
   exactly;
7. runs `python manage.py verify_publication <pub_id>` for every publication the
   manifest lists and requires each to report identical.

Then it prints exactly one line:

```
RESTORE OK: 2093 rows, audit head 412, 2/2 publications verified
```

or `RESTORE FAILED: <reason>` with exit status 1. When a restore fails, the
services are left running; fix the cause and run it again - restores are
idempotent, and the database step starts by cleaning what is there.

## Moving a portal to a new machine

1. **On the old machine**, with the portal idle:
   ```
   python scripts/backup.py create
   python scripts/backup.py verify backups/verdict-<stamp>
   ```
2. **Copy the directory** to the new machine (encrypted, see above):
   ```
   scp -r backups/verdict-<stamp> new-host:~/verdict-restore/
   ```
3. **On the new machine**, install Docker with Compose, check out the same VERDICT
   version (`manifest.json` records the commit and the image id), and start a fresh
   portal:
   ```
   cd verdict && docker compose up --build -d
   ```
4. **Restore over it**:
   ```
   python scripts/backup.py restore ~/verdict-restore/verdict-<stamp> --yes
   ```
   The restored rows replace the fresh database; the restored `secret_key` and
   signing keys replace the ones the fresh start generated.
5. Read the final line. If it is `RESTORE OK`, the portal on the new machine is
   the portal from the old one.

If the new machine uses a different Compose project name, add `--project NAME` to
`restore` so it addresses the running services.

## What "RESTORE OK" proves

- Every byte of the archive is the byte that was written when the backup was made
  (sha256 over every file in the manifest).
- The live database has the same number of rows in every table, the same audit
  head - latest sequence number and entry hash - and the same chain heads. Rows
  that were added, deleted or rewritten after the dump, or a chain that was
  truncated or re-linked, change the fingerprint and fail the restore.
- Every result publication in the manifest still reproduces from its own stored
  inputs, and the live inputs still hash to the stored digest, so the published
  results and awards on the new machine are the ones the judges saw.

What it does not prove: that nobody ever had access to the machine it came from.
Keep the archive encrypted and treat a restore as a normal privileged operation.

## Recorded run
