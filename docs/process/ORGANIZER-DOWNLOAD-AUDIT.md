# Organizer download audit

Checked: 2026-09-27. Scope: all five downloadable inputs linked from the public [Dogfood specification page](https://dogfoodhack.com/spec/), plus locally saved kickoff text. Remote bytes were downloaded into memory and compared by SHA-256. No official file was overwritten.

## Download inventory

| Published file | Bytes | Comparison with saved copy | Use |
|---|---:|---|---|
| [spec.md](https://dogfoodhack.com/spec/spec.md) | 14,868 | Changed: three weekday descriptions | Product/checker contract |
| [run.py](https://dogfoodhack.com/spec/run.py) | 8,855 | Identical | Unmodified official checker |
| [fixtures.json](https://dogfoodhack.com/spec/fixtures.json) | 46,687 | Identical | Unmodified seed input |
| [example.dogfood.toml](https://dogfoodhack.com/spec/example.dogfood.toml) | 1,082 | Identical | Checker configuration example |
| [context.txt](https://dogfoodhack.com/spec/context.txt) | 29,082 | Changed: four date/weekday descriptions | Consolidated briefing |

Remote SHA-256:

```text
spec.md
07e479728e7e6961fcf5053e159e6dc807bae3e4b371ee088e4a17897950d290

run.py
aa98963841bc8e18e8e5d76f0499697c093dd3c0055f9d73a459f592f4dcf09d

fixtures.json
252896bc45d49fca69ad413be40c6bfde9d9b9f9dd8db702b3ff74eaaa181121

example.dogfood.toml
58c974da4f0faa6d1a4fbb158770b34c470f2893d3169405ed73deca0f3a9e44

context.txt
b277b297d7fa0e11152b01cdef69db7c08fb0e44398b2b8900e50007041a2b0c
```

Previous saved `spec.md`: 14,861 bytes, SHA-256 `644b92eb50a37215cb992589e451850ab95cb14803bbad3905fd8b071bfbd696`.

Previous saved `context.txt`: 29,080 bytes, SHA-256 `8459375702498df64eb377bf5c8088628cf1cef9daa6d5926e7c1129ab588ad0`.

Comparison locations were the workspace-root saved spec, runner and fixture, and the saved `planning/official/` context and example configuration. Application runner/fixture copies had also been checked against the same immutable hashes. Paths outside this repository are research notes, not runtime dependencies.

## What changed

The current spec changes Friday kickoff/new-code wording to Saturday, and Friday-to-Monday wording to Saturday-to-Tuesday. The current context specifies:

- Kickoff: Saturday September 26, 2026, 18:00 UTC.
- Code freeze: Tuesday September 29, 2026, 18:00 UTC (23:30 IST).
- Judging: September 29 through October 8.
- No project code before Saturday kickoff.

The earlier September 25-28 dates in the user's original pasted listing and local `rule.txt` are stale. The saved kickoff announcement corroborates the absolute revised dates; a weekday typo in that saved text is not a different deadline. The [current downloadable context](https://dogfoodhack.com/spec/context.txt) now independently confirms the revised window.

Do not silently update official input snapshots without recording their new provenance. In particular, `run.py` and `fixtures.json` are unchanged and must remain untouched.

## Findings that affect product decisions

- The checker has seven T1/T2 checks; it is not a comprehensive feature or T3/T4 certifier. Saved organizer clarification says T3/T4 are reviewed manually. Keep our extended test evidence separate from official output.
- Reading `run.py` shows that process exit status alone is insufficient: it can finish with status zero despite failed checks. Inspect the actual report; do not create a green CI gate from exit status alone.
- The closed-submission check accepts a 4xx response without inspecting the reason. Our own tests must establish real deadline enforcement, not just a failing request.
- The fixture is synthetic, with no objective project-quality labels. It contains 41 project records, 40 teams, 30 judges, 8 tracks and 126 score records. "Forty projects" in the overview is not the raw record count.
- There is no pending-assignment section: root keys are `event`, `tracks`, `judges`, `teams`, `projects`, `scores`. Missing-review identities cannot be recovered just from a default target.
- Our latest-duplicate policy excludes `prj_07` and its five reviews, leaving 40 included projects and 121 reviews. Other defensible duplicate policies can produce different counts; a count difference is not automatically a competitor defect.
- Keep the fixture's historical submission close date, `2026-03-01T18:00:00Z`. Use a separate event or an explicitly in-memory scenario for live demonstrations.
- Current scoring guidance treats optional bonuses as tie-breakers. Do not present the older headline '+16' as an automatic addition to the weighted score. Saved sources also disagree on prize allocation; this audit makes no new prize claim.

The fixture-derived coverage and reversal experiment are reproduced in [FEATURES.md](../FEATURES.md); their calculations use our declared scoring policy, not a claim that the organizer supplied that policy.

## Access limits

The saved kickoff deck/announcement was reviewed locally. Its live [Gamma deck](https://gamma.app/docs/DogFood-2026-Kickoff-zfdj8g99ttdrwqu) was not retrievable during this research. We did not access private Discord discussions or claim to have checked unpublished attachments. "All downloads checked" means the five publicly linked files above, not every message an organizer may have sent.

No acceptance report was regenerated by this document-only audit. The existing report is from an older build and must be refreshed after the combined repair/deployment gate.
