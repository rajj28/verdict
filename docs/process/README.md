# How VERDICT was built

The DOGFOOD rules expect AI tools ("Claude Code, Cursor, Aider, Copilot, all
expected"). This folder is the honest record of how we used them. These files record
the process, not the current product. Where they disagree with the code or with the
top-level documents (`README.md`, `ARCHITECTURE.md`, `DATA-MODEL.md`, `JUDGING.md`,
`THREAT-MODEL.md`), the code and those documents win.

## Workflow

1. At kickoff we wrote `BUILD-SPEC.md`: the data model, permission rules, API
   contract and engine maths the whole build follows.
2. Work was cut into packets (`packets/`). Each one names the files it may touch,
   the behaviour to build and the tests that prove it, and ends with a fixed
   SUMMARY / FILES / TESTS / GAPS report (see `AGENTS.md`).
3. Coding agents implemented the packets: GitHub Copilot CLI for the hardest
   cross-cutting work, OpenCode models for well-specified work, and OpenAI Codex
   for independent audits.
4. One orchestrator (Claude Code) planned the packets, read every diff, re-ran the
   affected test suites on PostgreSQL, and wrote every commit. Findings from review
   went back as follow-up packets. An example is F5B, which made tour sandboxes
   full private copies after review found shared accounts.
5. Independent adversarial passes tried to break the build. Every defect was
   reproduced by a failing test before it was fixed:
   - `../ADVERSARIAL-T1-RACES.md`
   - `../ADVERSARIAL-T2-PRIVACY.md`
   - `../ADVERSARIAL-INTEGRATION-20260927.md`
   - `../ASTRA-FINAL-AUDIT.md`

## What is here

| File | What it is |
|---|---|
| `BUILD-SPEC.md` | The kickoff specification the packets were written against |
| `packets/` | Every work packet, in the order the product was built |
| `REPAIR-EVIDENCE.md` | Commands and results from the repair round after the first audit |
| `TIER-EVALUATION-20260927.md`, `evaluation-20260927/` | A dated mid-build evaluation (historical: several gaps it lists were fixed later) |
| `ORGANIZER-DOWNLOAD-AUDIT.md` | Proof that the organizers' `run.py`, `fixtures.json` and example configuration in this repository are byte-identical to the published files |
