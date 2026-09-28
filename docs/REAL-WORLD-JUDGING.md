# How judging is really done, where it breaks, and what VERDICT does about it

This note starts from practice, not from our code: how hackathons, peer review and
judged sports actually rank entries today, what goes wrong, and which part of
VERDICT answers each failure. Every VERDICT claim names the file, test or command
that shows it; every outside claim names its source. Where VERDICT does not solve
a problem, the note says so.

## 1. How judging is done today

**Online rubric scoring (Devpost-style).** Judges rate each submission 1-5 on up to
six criteria; organizers may weight the criteria (weights must total 100%) and the
overall score is the weighted average. A judge with a conflict clicks "I recuse
myself" and can edit earlier scores until judging ends. Nothing in the help pages
describes any correction for harsh or generous judges, and the online judging guide
says star ratings are the only feedback channel.
[Devpost: judging setup](https://help.devpost.team/article/230-how-to-set-up-judging),
[Devpost: how to judge](https://help.devpost.com/article/103-how-to-judge-an-online-hackathon)

**Expo or "science fair" judging (MLH).** MLH's organizer guide strongly recommends
judges walking between team tables: three judging rounds per project, about four
minutes each (two minutes of demo, one of questions, plus walking). Each judge
reports a top three worth 3/2/1 points ("stack ranking"), which the guide presents as
the way to cancel out scorer bias. Scores live in Google Sheets, preferred over
custom tools for reliability. Organizers then re-check the top three to five
projects for cheating before announcing. Judging takes two to three hours.
[MLH Hackathon Organizer Guide: Judging Plan](https://guide.mlh.com/general-information/judging-and-submissions/judging-plan)

**Pairwise expo judging (Gavel).** HackMIT replaced scores with comparisons. Judges
say which of two projects is better, and a Thurstone / Crowd-BT model turns
comparisons into a ranking. The reasons given: judges use score scales differently;
at HackMIT the average judge saw only about 5% of the projects; results depended on
judging order; z-score normalisation misleads when each judge sees a small slice.
Gavel ran HackMIT with 200+ projects and 100 judges and dozens of other events.
[Athalye: Designing a better judging system](https://www.anishathalye.com/2015/03/07/designing-a-better-judging-system/),
[Athalye: Gavel](https://www.anishathalye.com/2016/09/19/gavel-an-expo-judging-system/)

**Peer review at scale (NeurIPS).** Machine-learning conferences face the same
problem with thousands of reviewers. NIPS calibrated reviewer scores from 2006 to
2012 with the Platt–Burges model: score = paper quality + reviewer bias + noise,
fitted by least squares with a ridge penalty on the biases. NIPS 2013–2014 used a
Bayesian variant. [Ge, Welling and Ghahramani, A Bayesian Model for Calibrating
Reviewer Scores](https://mlg.eng.cam.ac.uk/hong/unpublished/nips-review-model.pdf)

**Judged sports.** Panels in diving and gymnastics discard extreme marks. Studies
of the 2002 Winter Olympics found nationalistic bias among judges, with figure
skating judges showing signs of vote trading and bloc judging
([Zitzewitz 2006](https://www.ssrn.com/abstract=319801)).

## 2. What goes wrong, with evidence

1. **Judges use the scale differently, and each sees a different slice.** A 7 from a
   harsh judge and a 4 from a generous one say nothing about the projects. When judge
   sets differ, raw averages reward the luck of assignment (Athalye; Ge et al.). The
   organizers' own fixture includes a judge who gave every project the same score.
2. **Few reviews per project make close ranks noise.** In the NeurIPS consistency
   experiments, a second independent committee would have rejected about half of the
   accepted papers: 49.5% in 2014 and 50.6% in 2021. Overall, 25.9% and 23.0% of
   duplicated papers got different decisions.
   ([NeurIPS 2021 consistency experiment](https://blog.neurips.cc/2021/12/08/the-neurips-2021-consistency-experiment/))
   Hackathons usually have three reviews per project (MLH), so neighbouring ranks are
   rarely distinguishable.
3. **Coverage is uneven.** Judges drop out and batches go unfinished (the organizers'
   fixture contains two unfinished review batches). Projects with fewer reviews are
   ranked with less information, silently.
4. **Conflicts and collusion.** Recusal depends on the judge's honesty (Devpost).
   Bloc judging is documented in elite sport (Zitzewitz).
5. **Order effects.** In song contests and figure skating, later performers got
   higher marks (Bruine de Bruin, *Save the last dance for me*, Acta Psychologica
   2005; figure skating follow-up 2006). Gavel's author reports order dependency too.
6. **Opaque results.** Teams rarely learn how they were scored. Devpost's online
   judging offers no feedback channel beyond star ratings.
7. **Late organizer decisions reshuffle prizes silently.** Disqualifications,
   discarded reviews and the final re-check of the top projects (MLH) happen in
   spreadsheets, with no record of what changed which award.
8. **Deadline disputes.** Late submissions and edits after the deadline are argued
   by timestamp, often the client's.
9. **Public voting gets gamed.** Duplicate accounts, repeated ballots, and early
   tallies that invite bandwagons.
10. **"Who changed this?"** Spreadsheets have no tamper-evident history, so a
    published ranking cannot be re-derived or checked by anyone else.

## 3. What VERDICT does about each

| # | Failure | VERDICT's answer | See it / check it |
|---|---|---|---|
| 1 | Scale differences, different slices | The Platt–Burges additive model NIPS used (quality + judge offset + noise, ridge penalty on offsets). Lambda is chosen by predeclared, seeded 5-fold cross-validation instead of by hand (fixture: lambda = 100). Raw and normalized rankings are shown side by side, with each judge's estimated offset. The calibration showcase event plants two harsh and two generous judges and shows the model finding them without being told. Pairwise mode uses Bradley–Terry (Gavel's idea), for events that prefer comparing to scoring. | `JUDGING.md` (Normalization, Pairwise mode), `docs/NORMALIZATION-PROOF.md`, `scripts/normalization_proof.py`, `/manage/showcase/calibration`, `docs/SHOWCASE.md` |
| 1 | The same-score judge | Detected and reported. That judge's reviews carry no ranking information, and the fit treats them as an offset. | `JUDGING.md` ("The judge who marks everything the same") |
| 2 | Close ranks are noise | Rank uncertainty: 90% rank ranges, chance of finishing first and of a top-k place, and "statistically tied" labels between neighbours. The publish dialog warns when 1st and 2nd are tied. The robustness certificate adds leave-one-judge-out, leave-one-review-out and the fewest changed reviews that flip the winner. On the fixture, the leader is first in only 22% of 200 re-runs and ahead of second in 46%: a decision, not a fact. | `docs/UNCERTAINTY.md`, `tests/test_uncertainty_integration.py`, `JUDGING.md` (Robustness analysis, Rank uncertainty) |
| 3 | Uneven coverage | Seeded, load-balanced assignment with a per-project target and optional anchor projects per track. The progress view and under-reviewed flags show gaps. Projects without reviews are listed as unranked and publishing requires an explicit acknowledgement. The readiness planner estimates how many reviews a decision needs; it is labelled experimental. | `JUDGING.md` (Assignment, Readiness and budget planner), `src/judging/assign.py` |
| 4 | Conflicts, collusion | Declared conflicts (by judge or organizer) block assignment. The database allows one role per person per event. Reviews far from consensus (residual over 2.5x the spread) are flagged, never excluded automatically. Leave-one-judge-out shows whether one judge decides the winner. Coordinated moderate inflation is *not* detectable, and `THREAT-MODEL.md` says so. | `src/judging/models.py` (`Conflict`), `JUDGING.md` (Robustness analysis), `THREAT-MODEL.md` |
| 5 | Order effects | Community ballots show projects in a per-ballot random order (`src/community/services.py`). Each judge's queue follows a per-judge deterministic order (sha256 of event, judge-role and project public ids, to-do before submitted), so position effects spread across projects instead of piling onto the same ones. The model does not correct order effects. | `src/community/services.py`, `src/judging/policy.py` (`order_judge_queue`), `tests/test_judge_pages.py` (`PerJudgeQueueOrderTests`) |
| 6 | Opaque results | Feedback release: each team sees its own weighted score and per-criterion averages. Private judge notes are never released. A decision record per publication, a public Verify, and certificates that name the publication version. | `docs/PUBLICATION-POLICY.md`, `/events/<slug>/results` |
| 7 | Silent late decisions | Consequence preview: before publishing, disqualifying, excluding a review or putting one back, the organizer sees the exact ranks and awards that change. The preview runs the same code path as publishing, so it is exact, not an estimate. If the data changed since the preview, the action is refused (409 `stale_preview`). Every exclusion needs a reason and is in the audit log. Re-publishing needs a note and creates a new version. | `JUDGING.md` (Consequence preview), `tests/test_consequences.py` |
| 8 | Deadline disputes | Server time only. Windows are half-open `[open, close)` and checked after row locks, so a save at the closing second is refused consistently. Every submission edit is kept as a revision. | `docs/ADVERSARIAL-T1-RACES.md`, `tests/test_adversarial_t1.py` |
| 9 | Gamed public voting | Access modes with identity gates, one ballot per identity (a second is refused with 409), tallies hidden until the window closes, random ballot order. Open-link mode deters casual repeats; it cannot establish one human, one vote, and the docs say so. | `THREAT-MODEL.md`, `docs/TIER-EVIDENCE.md` (T3) |
| 10 | "Who changed this?" | Hash-chained audit log. Immutable, versioned publication snapshots. Two-part Verify: reproducible from stored inputs, and unchanged since publication. Ed25519-signed judge records. A tested backup and restore that re-verifies every publication. | `docs/PUBLICATION-POLICY.md`, `manage.py verify_publication`, `docs/BACKUP-RESTORE.md` |

## 4. What we chose not to do, and why

- **Scale (multiplicative) differences are not corrected.** A judge who uses 2-4 while
  another uses 1-5 is only shifted, not stretched. Platt–Burges is offset-only too.
  With about three reviews per judge-project cell, a per-judge scale parameter would
  be fitted from noise. `JUDGING.md` (Limitations) states this.
- **No expo floor plan or stack-ranking mode.** MLH's 3/2/1 stack ranking removes
  scale bias by throwing away magnitude. Our pairwise mode is the statistical version
  of "compare, don't score". It keeps a likelihood, so its uncertainty can be
  reported.
- **Pairs are chosen for coverage, not information gain.** VERDICT gives the next
  comparison to the least-compared projects, with a hash tie-break (predictable and
  auditable). Crowd-BT-style active learning spends comparisons better but makes the
  schedule harder to explain.
- **The organizer stays the authority.** VERDICT does not auto-exclude or
  auto-disqualify. It makes each decision visible before it happens (consequence
  preview), accountable afterwards (audit log, versions, Verify), and honest about
  uncertainty (rank ranges, ties).

## 5. The short version

Real judging today is a weighted average in a spreadsheet, three reviews per
project, no correction for harsh or generous judges, and no record of late
decisions. VERDICT keeps the familiar rubric and adds five things:
- the reviewer-bias model a major ML conference used;
- an honest statement of how certain each rank is;
- a preview of every decision's consequences;
- a tamper-evident record anyone can re-verify;
- a controlled experiment that shows the correction working.
