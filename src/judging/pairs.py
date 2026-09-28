"""Deterministic, coverage-first pair selection without database dependencies."""
from collections import Counter
from hashlib import sha256
from itertools import combinations


def select_pair(projects, compared, totals, *, minimum=3, seed="") -> dict:
    """Stop at per-project coverage or exhausted distinct pairs; abstentions count as seen."""
    projects = sorted(set(projects))
    allowed = set(projects)
    seen = {tuple(sorted(pair)) for pair in compared if set(pair) <= allowed and len(set(pair)) == 2}
    counts = Counter(project for pair in seen for project in pair)
    candidates = [pair for pair in combinations(projects, 2) if pair not in seen]
    completed = sum(counts[project] >= minimum for project in projects)
    done = not candidates or completed == len(projects)
    progress = {
        "assigned_projects": len(projects), "comparisons": len(seen),
        "total_pairs": len(projects) * (len(projects) - 1) // 2,
        "target_per_project": minimum, "projects_complete": completed,
        "project_counts": {project: counts[project] for project in projects},
        "done": done, "reason": "coverage_reached" if completed == len(projects)
        else "pairs_exhausted" if done else "in_progress",
    }
    if done:
        return {"pair": None, "progress": progress}

    def priority(pair):
        coverage = [totals.get(project, 0) for project in pair]
        tie = sha256(f"{seed}:{len(seen)}:{pair[0]}:{pair[1]}".encode()).hexdigest()
        return sum(coverage), max(coverage), tie

    return {"pair": min(candidates, key=priority), "progress": progress}
