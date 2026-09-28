# Judging

> Outline.

## Assignment strategy
## Rubric and review score
## Normalization method
## The judge who marks everything the same
## Publication and verification
## Pairwise mode

Choose Pairwise or Both in event settings, assign at least two eligible projects
to each judge, and open judging. The judge console links to `/judge/{slug}/pairwise`.
Both cards include the assigned projects' private evidence. Judges can only
compare their own submitted, in-track assignments without declared conflicts.

Each judge compares a pair once. Selection prioritizes projects with the fewest
event-wide comparisons; ties use a deterministic hash of the event, judge,
completed-pair count, and candidate pair. A judge finishes when every assigned
project has reached the configured comparison target (default three), or all
distinct pairs are exhausted. Three comparisons per project per judge is a
coverage guideline, not a precision guarantee. “Too close” is an abstention:
it counts as a seen pair and creates no win, half-win, or ranking edge.

The latest choice can be retracted for 30 seconds, strictly before the boundary
and while judging remains open. Retraction preserves the original record and
creates an audit entry; the pair becomes available again. Event row locks and
a database constraint prevent duplicate active verdicts, including reversed pairs.
The first comparison locks the event's scoring settings.

Live outcomes use the existing Bradley–Terry MM estimator: each project has a
positive strength, and the fitted log strengths determine the order. A virtual
opponent contributes one win and one loss for each observed project so unbeaten
projects remain finite. Log strengths are not percentages or win-confidence
estimates. Equal strengths after rounding to two decimals share a rank; titles
only control display order within a shared rank.

Results distinguish live comparisons from the separate rubric-derived pairwise
cross-check. Selecting Pairwise as the official ranking uses only live outcomes,
including an explicitly empty set; it never substitutes derived rubric outcomes.
Projects without decisive comparisons remain unranked. Disconnected observed
comparison groups are shown separately and cannot support an overall official
order: pairwise publication is blocked until additional comparisons connect them.
The virtual opponent stabilizes the estimator; it does not supply missing evidence
between disconnected groups.

Publications snapshot the exact active comparisons, including abstentions, and
verification recomputes that snapshot and checks its digest against live data.
Organizer export `pairwise.csv` includes original and retracted judgments.
Pairwise choices sidestep differences in numeric scoring baselines, but they do
not remove differences in taste, strategic judgments, intransitive preferences,
or sparse coverage. No winner probability or calibrated confidence is claimed.

## Limitations
