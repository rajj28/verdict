# VERDICT

**A self-hosted hackathon portal whose judging you can check.** Teams submit, judges
score under assignment and conflict rules, organizers publish ranked awards, and
anyone can re-verify the result. One command, no network, no accounts.

Django 5.2 + Django REST Framework + PostgreSQL 16, server-rendered pages, no
JavaScript build step, MIT licensed.

**Demo video:** _link added at submission_. **Try it yourself:** run it, open
<http://localhost:8080> and press **Take the 5-minute tour**.

## Run it

```bash
docker compose up --build --wait      # then open http://localhost:8080
python run.py .dogfood.toml           # the organizers' checker
```

- **Offline, build included.** All 31 Python wheels are vendored in `vendor/wheels`
  (Linux x86_64 and aarch64). The image builds with `network: none` and
  `pip --no-index`, and runs with no outbound calls (no CDN, fonts vendored, CSP
  `script-src 'self'`). The only outside inputs are the two base images
  (`python:3.12-slim`, `postgres:16-alpine`); on an air-gapped laptop use
  `python scripts/offline_images.py save|load`.
- **Seeded on first boot** (`python manage.py runportal` waits for the database,
  migrates, seeds, then starts gunicorn):
  - `sample-hack-2026`: the organizers' `fixtures.json`, closed, with 41 projects,
    126 reviews and 30 judges, including its awkward cases;
  - `demo-hack`: an open practice event;
  - `showcase`: a synthetic calibration event with planted harsh and generous
    judges (see below).
- **Demo logins** (password `verdict-demo`, `DEMO_MODE=1` only; the bearer tokens
  are in `.dogfood.toml`):

| Role | Email |
|---|---|
| Organizer | `organizer@verdict.local` |
| Judge A / Judge B | `diego.herrera@example.org` / `jonas.vogel@example.org` |
| Participant | `priya1@example.org` |

The **guided tour** creates a private sandbox for your browser: a full copy of the
showcase with its own accounts. It switches roles in one click and walks one event
from submission to a verified publication. Every click is a real API call.
`docs/TOUR.md` explains it.

## Check every claim

Every number below comes from a command you can run, and we ran each one on a clean
`git archive` of this repository.

| Claim | Command | Our last run |
|---|---|---|
| Official acceptance checks | `python run.py .dogfood.toml` | 7/7 PASS, "claimed T1 T2 T3 T4, verified T1 T2" (run.py has no T3/T4 checks; those tiers are judged by hand) |
| Every T1–T4 bullet, request by request | `python scripts/verify_tiers.py .dogfood.toml` | 59/59 passed; 2 skipped, see note |
| Attacks refused | `python scripts/attack.py .dogfood.toml` | 28/28 refused, 14/14 direct HTTP checks |
| Runs with the network off | `docker compose -f docker-compose.yml -f docker-compose.offline.yml up -d --wait` on fresh volumes, then `docker compose exec web python run.py .dogfood.toml` (steps in `docker-compose.offline.yml`) | seeded portal, 7/7 PASS, no internet inside |
| Backup, destroy, restore | `python scripts/backup.py create`, `docker compose down -v`, `up`, `python scripts/backup.py restore <dir> --yes` | `RESTORE OK: 1921 rows, audit head 7, 1/1 publications verified` |
| Normalization proof | `docker compose exec web python scripts/normalization_proof.py` | regenerates `docs/NORMALIZATION-PROOF.md` byte for byte in the image (Python 3.12); Python 3.11's float `sum()` differs in the last digit of a few simulation statistics |
| Rank uncertainty on the fixture | `python scripts/uncertainty_evidence.py` | noise SD 15.43; the leader is first in 22% of 200 re-runs |
| Test suite (PostgreSQL) | `DATABASE_URL=postgres://... python scripts/gate.py` | system check and migrations clean; 1,128 Django tests and 65 pure engine tests, 0 failures |

Note on the skips: webhook delivery to the checker's own local receiver is refused by
the SSRF guard in the default configuration. Restart with `WEBHOOKS_ALLOW_PRIVATE=1`
to watch a signed delivery. `docs/TIER-EVIDENCE.md` maps every bullet of the brief to
its check, its code and a one-minute manual test.

## What it does

- **T1 core.** Sessions and bearer tokens; five roles (visitor, participant, judge,
  organizer, admin), one role per person per event, enforced by the database. Events
  with dates, tracks, prizes and custom questions. Teams by invite link. Projects
  have every field the brief lists (tagline, long description, thumbnail, gallery,
  video, repository and live links, tech tags, track, answers) and are drafted and
  edited until the deadline. The deadline is enforced on server time, after row
  locks, as a half-open window. Public gallery with search and filters.
- **T2 judging.** Judge invitations, and assignment that is seeded, load-balanced,
  conflict-aware and track-scoped, with optional anchor projects. A weighted rubric
  the organizer configures. Judges can never read another judge's work, or another
  track, and curl gets the same 403 as the page. A live command center (refreshes
  every 30 s) shows who has not started. Cross-judge normalization is documented
  and defended. CSV export at every stage: participants, teams, projects, judges,
  assignments, reviews, progress, results, votes, pairwise, audit, certificates.
- **T3 public.** Community voting by open link, verified email or account, with
  plain or quadratic ballots. Comments with moderation. Tallies hidden until the
  window closes. Ballots in a per-voter random order. Rate limits, duplicate-ballot
  refusal and an audit trail.
- **T4 stretch.** A REST API covering every UI action (137 documented operations,
  `docs/API.md`, `docs/openapi.yaml`, Swagger at `/api/docs/` served offline).
  Webhooks for every audited change, HMAC-signed, retried by a worker, SSRF-guarded.
  Certificates. Ed25519-signed, publicly verifiable judge participation records.
  An embeddable gallery. Bulk import and export (`event.json`, CSV, the fixture
  format).

## What makes the judging different

1. **The reviewer-bias model a major ML conference used.** A review is modelled as
   quality + judge offset + noise, with a ridge penalty on the offsets. That is the
   Platt–Burges objective NIPS used to calibrate reviewer scores from 2006 to 2012.
   The penalty is chosen by predeclared, seeded cross-validation, not by hand.
   Raw and normalized rankings sit side by side, and every judge's estimated
   habit is shown. (`JUDGING.md`, `docs/NORMALIZATION-PROOF.md`)
2. **Proof you can watch.** The `showcase` event plants two harsh and two generous
   judges in synthetic data. `/manage/showcase/calibration` shows VERDICT finding
   all four without being told:
   - rank agreement with the truth rises from Kendall tau 0.669 (raw) to 0.877;
   - the true winner moves from a tie for 4th to 1st.
   
   (`docs/SHOWCASE.md`)
3. **Honest certainty.** With about three reviews per project, neighbours are often
   indistinguishable. VERDICT re-runs the fitted model 200 times and shows each
   project's rank range, its chance of first place and of a prize place, and
   "statistically tied" labels. On the organizers' fixture the leader is first in
   only 22% of re-runs, and publishing warns when 1st and 2nd are tied.
   (`docs/UNCERTAINTY.md`)
4. **Decisions with visible consequences.** Before publishing, disqualifying,
   excluding a review or putting one back, the organizer sees exactly which ranks
   and awards change ("changes 4 ranks and 1 award"). The preview is computed by
   the same code as the real action. If the data changes in between, the action
   is refused (`409 stale_preview`). (`JUDGING.md`, Consequence preview)
5. **Results anyone can re-verify.** Every publication is an immutable, versioned
   snapshot. Verify recomputes it from the stored inputs (bit for bit) and checks
   that the live data still matches; a changed score is named field by field.
   Certificates name the version they belong to, and a hash-chained audit log
   records every decision. (`docs/PUBLICATION-POLICY.md`)

We also added a robustness certificate (leave-one-judge-out, leave-one-review-out,
fewest review changes that flip the winner), a readiness planner (labelled
experimental), a per-judge review order that spreads serial-position effects, and a
pairwise Bradley–Terry mode. `docs/REAL-WORLD-JUDGING.md` maps how hackathons judge
today (Devpost, MLH expo judging, Gavel, NeurIPS review calibration) to what breaks,
and to what VERDICT does about it.

## Documentation

| Document | Contents |
|---|---|
| `ARCHITECTURE.md` | How the system fits together and the decisions worth stealing |
| `DATA-MODEL.md` | Schema, the invariant each constraint protects, import and export paths |
| `JUDGING.md` | Assignment strategy, scoring maths, normalization defended, pairwise, uncertainty, consequence preview |
| `THREAT-MODEL.md` | Sybil votes, ballot stuffing, collusion, deadline gaming, STRIDE, and the attacks we did not stop |
| `docs/API.md`, `docs/openapi.yaml` | The API, every operation and the UI control that uses it |
| `docs/TIER-EVIDENCE.md` | Every tier bullet mapped to its check, its code and a one-minute manual test |
| `docs/NORMALIZATION-PROOF.md`, `docs/UNCERTAINTY.md` | The normalization proof and the uncertainty method, both regenerated from code |
| `docs/PUBLICATION-POLICY.md` | What can change after judging, and how Verify works |
| `docs/BACKUP-RESTORE.md` | Backup and restore, with a recorded end-to-end run |
| `docs/SHOWCASE.md`, `docs/TOUR.md` | The calibration showcase and the guided tour |
| `docs/REAL-WORLD-JUDGING.md` | How judging is done today, where it breaks, what VERDICT does |
| `docs/ADVERSARIAL-*.md`, `docs/ASTRA-FINAL-AUDIT.md` | Independent attempts to break it, and what they fixed |
| `docs/process/` | How it was built (AI-assisted, packet by packet), with the kickoff spec |

## Operations

- **Configuration** is environment only; `.env.example` lists every variable.
  `DATABASE_URL` must be PostgreSQL (SQLite is refused at startup). If `SECRET_KEY`
  is unset, one is generated into `/data/secret_key` on the `appdata` volume.
- **Production mode:** set `DEMO_MODE=0`, `ALLOWED_HOSTS`, `SECRET_KEY` and
  `ADMIN_EMAIL`/`ADMIN_PASSWORD`. Demo passwords and tokens are then retired, and
  the tour is disabled.
- **Mail:** with no `EMAIL_HOST` (or in demo mode) messages go to an organizer
  outbox page instead of SMTP.
- **Webhooks:** delivered by the `webhook-worker` service. Keep
  `WEBHOOKS_ALLOW_PRIVATE=0` in production so destinations must resolve to public
  addresses.
- **Health:** `GET /healthz`. Migrations run on every start and are idempotent.
- **Backup and restore:** `scripts/backup.py` backs up the database, uploads, the
  secret key and the signing keys in one directory. Restore requires the portal's
  fingerprint to match and every publication to verify identical.
  `docs/BACKUP-RESTORE.md` has a recorded run.
- **Data in and out:** fixture-format import (`POST /api/v1/imports`), a
  whole-event `event.json` export and import, and CSV at every stage. Exports are
  not backups: they never contain credentials, keys or audit hashes.

## Honest limitations

- Normalization removes each judge's level habit, not their scale habit (someone
  who uses only 2–4). Strategic or coordinated bias is flagged, not modelled.
- Rank uncertainty comes from simulations of the fitted model. It ignores rubric
  bounds and rounding, and approximates rather than guarantees.
- The readiness and review-budget planner is labelled experimental: it assumes a
  noise level.
- Verify proves reproducibility and consistency. It cannot stop a database
  administrator who rewrites both data and digests; `THREAT-MODEL.md` lists what we
  did not stop.
- Open-link voting deters casual repeats; it cannot establish one human, one vote.
  Use email or account mode for binding tallies.
- There is no in-person expo mode (table maps, judge walking routes).
- Performance evidence is small samples, not a load test.

## Provenance

The rules require new code written during the window (kickoff Sat 26 Sep 2026 18:00
UTC). Four commits predate kickoff:
- the organizers' published files and our planning documents (Fri 25 Sep);
- an empty scaffold at 17:30 UTC Sat: folders, document outlines, pinned
  requirements, and a 10-line generic Dockerfile and compose skeleton, with no
  application code;
- two edits to the planning spec at 17:31 and 17:45 UTC.

All application code starts with `2ebd6dc` at 18:25 UTC, after kickoff.
`docs/process/README.md` describes the AI-assisted workflow.

## License

MIT (`LICENSE`), © 2026 VERDICT contributors. Runtime dependencies are pinned in
`requirements.txt`. Vendored assets:
- Bootstrap 5.3 (MIT) in `src/static/vendor/`;
- the Geist and Geist Mono fonts (`src/static/fonts/LICENSE-Geist.txt`);
- Instrument Serif (`src/static/fonts/LICENSE-InstrumentSerif.txt`), both under the
  SIL Open Font License.
