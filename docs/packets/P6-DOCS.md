# Packet P6-DOCS: documents a senior panel can audit (drafted by a worker, reviewed line by line by the orchestrator)

Write from the actual code (read it), never from the spec alone; every claim must point to a file, test or generated report.
- `docs/adr/0001-*.md` … `0010-*.md` (Context / Decision / Consequences, ≤ 1 page each): one role per event; API as the only write path; scoring lock at first review; additive-offset normalization as BLUP; latest submission supersedes duplicates; half-open windows on server time checked after the row lock; verifiable publications (stored inputs + digest + Verify); bearer tokens + session auth; Postgres + one app container, Python entrypoint; no Django admin.
- ARCHITECTURE.md: Mermaid C4-style context + container diagrams, request lifecycle (auth → policy → service → audit), module map, "Decisions worth stealing" linking the ADRs.
- DATA-MODEL.md: Mermaid `erDiagram`, each table with the invariant every constraint protects, import/export paths, never-exported fields.
- docs/THREAT-MODEL.md: STRIDE per trust boundary (visitor↔web, participant, judge, organizer, admin, webhook receivers, operators), each threat → control → test → attack-report line; explicit "not stopped" list (Sybil in open-link voting, shared/rotating IPs, organizer collusion, compromised host).
- docs/SCHEMA-DEFENSE.md: the 15 questions a database reviewer would ask, answered.
- CHANGELOG.md, CONTRIBUTING.md (how upstream PRs are welcome), README Reviewer's map + Operations + limitations.
Files: docs/**, *.md at repo root (not run.py/fixtures.json).
