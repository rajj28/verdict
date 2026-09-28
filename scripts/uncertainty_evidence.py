"""Reproduce fixture uncertainty; standard library only, no database writes."""
import json
import platform
import time

from normalization_proof import E, load_fixture


def main():
    criteria, reviews, *_ = load_fixture()
    scored = E.score_reviews(reviews, criteria)
    start = time.perf_counter()
    result = E.rank_uncertainty(scored, 100.0)
    elapsed = time.perf_counter() - start
    first = result.projects[result.order[0]]
    print(json.dumps({
        "python": platform.python_version(), "reviews": len(scored),
        "projects": len(result.order), "lambda": 100, "replicates": result.replicates,
        "seed": result.seed, "df": result.df, "sigma": result.sigma,
        "winner": first.project_id, "p_first": first.p_first,
        "rank_interval": [first.rank_low, first.rank_high],
        "p_above_next": first.p_above_next, "tied_pairs": len(result.tied_pairs),
        "group_sizes": [len(group) for group in result.groups],
        "summary": result.summary, "seconds": elapsed,
        "reversed_input_identical": result == E.rank_uncertainty(list(reversed(scored)), 100.0),
    }, indent=2))


if __name__ == "__main__":
    main()
