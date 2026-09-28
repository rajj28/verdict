# Publication, correction and feedback policy

This is the contract every guard, page, export, verifier and test in VERDICT follows. If the code and this page disagree, the code is wrong.

## 1. Ballots are final when judging closes
- Judges can save and resubmit their own reviews only while the judging window is open (`judging_open_at <= now < judging_close_at`, server time, checked after the row lock).
- After judging closes nobody can edit a submitted ballot, organizers and admins included. There is no organizer shortcut that rewrites scores.
- Before any publication exists, an organizer may extend or reopen the judging window; the change is audited with the old and new times. After a publication exists the judging window cannot be reopened.

## 2. Corrections change inputs, never history
The only ways to change a result after judging closes are explicit, reasoned and audited:
- exclude or re-include a review (reason required);
- disqualify a project (reason required).

Neither action changes anything the public can see. Public results change only when an organizer publishes a **new publication version** with a mandatory note. The new version supersedes the previous one; the previous version is never edited or deleted.

## 3. Publications are immutable, versioned snapshots
Each publication stores, at the moment it is created:
- the canonical inputs (every included review with its criterion values, rubric weights and scales, the λ procedure and the chosen λ, exclusions with reasons, and the snapshot of every ranked or listed project's public fields: title, team display name, track, status and status reason);
- the resulting rows and awards;
- the SHA-256 digest of the canonical inputs.

The public results page always shows the **official version** (the latest non-superseded one) with its version number, publication time and, when it replaced an earlier one, "supersedes version N" and the correction note. The public version history lists version, time, note and digest only (no judge identities, no per-review data).

## 4. What "Verify" proves (two separate answers)
- **Reproducible:** recomputing from the publication's stored inputs with the current engine yields exactly the stored rows and awards. This uses only the stored snapshot and does not read the live project roster.
- **Unchanged since publication:** the live database, projected the same way, still hashes to the stored digest. A difference is reported field by field (for example "review rev_x excluded after publication", "project prj_y disqualified after publication") and means a correction is pending a new version; it is not tampering evidence by itself.

## 5. Awards and certificates
- Awards belong to a publication version. Winner certificates name that version.
- If a later version changes an award, the earlier certificate still verifies as authentic but its verification page states "superseded by publication version N". Nothing is silently rewritten.

## 6. Feedback to teams
- A judge's comment field is private to that judge and the event's organizers, permanently. It is never released to teams, published, exported publicly or shown on certificates.
- After publication an organizer may release **score feedback**: each team sees, for its own project only, the official score, rank and per-criterion averages. No written judge text and no judge identities are released.
- Other teams, judges and visitors are refused (403) and nothing about another team's feedback leaks through pages, JSON, exports, widgets, caches or error messages.
