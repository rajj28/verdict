# QA1: Documentation sweep (every path, link and command a judge might try)

Read `AGENTS.md` first. PostgreSQL only; use the `DATABASE_URL` you are given.

## Why

Judges read `README.md` first, then follow its commands and links. One broken path or a
command that does not exist costs more trust than it took to write. Check everything a
judge could click or copy.

## What to check

In `README.md`, `ARCHITECTURE.md`, `DATA-MODEL.md`, `JUDGING.md`, `THREAT-MODEL.md`,
`docs/*.md` (not `docs/process/`):

1. Every relative Markdown link resolves to an existing file (and anchor, if one is
   given).
2. Every repository path written in backticks (`src/...`, `scripts/...`, `tests/...`,
   `docs/...`, `docker-compose*.yml`, `.dogfood.toml`) exists.
3. Every test module and test name cited exists (`grep`).
4. Every `manage.py <command>` cited is a real management command (it appears in
   `manage.py help`).
5. Every `python scripts/<x>.py` cited exists and either prints usage with `--help` or
   runs to completion without a server where that makes sense.
6. Numbers that appear in more than one document agree (for example test counts,
   `28/28`, `59`, `lambda = 100`, `19 of 40`, `0.906`, `22%`). Report every mismatch;
   the source of truth is the most recent evidence (`docs/TIER-EVIDENCE.md`,
   `docs/NORMALIZATION-PROOF.md`, `docs/UNCERTAINTY.md`, README's claims table).

## What to change

Fix only broken links, wrong paths, and typos in documentation. Do not edit code, tests,
numbers backed by evidence, or `docs/process/`. Anything you are not sure about goes
under GAPS with file:line.

## Done means

A table in your summary: document | line | problem | fixed or reported. Never git commit.
Finish with SUMMARY / FILES / TESTS / GAPS.
