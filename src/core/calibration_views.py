"""Organizer-only calibration check for the synthetic showcase event.

The showcase (see :mod:`core.showcase`) is generated from a known truth, so an
organizer can check the scoring method against it in the app instead of on paper.
Nothing here is a write: the page computes live from the event's current included
reviews and the event's own lambda, using ``results.services.preview`` and the
pure ``results.engine`` functions, so the numbers can never disagree with the
results page.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from core import showcase
from events.policy import can_manage, visible_events
from judging import policy as judging_policy
from results import engine
from results import services as results_services

#: A project this many places away from its true rank counts as a recovery miss.
DISPLACEMENT = 3
TOP_K = 3


def _rank_number(rank: str | None) -> int | None:
    """'3' or '=3' as 3, None when the row is unranked."""
    if rank in (None, "", "unranked"):
        return None
    try:
        return int(str(rank).lstrip("="))
    except ValueError:
        return None


def _rank_text(rank: str | None) -> str:
    """'4' or '4 (tied)', in plain words, for a sentence."""
    if rank is None:
        return "unranked"
    number = _rank_number(rank)
    return f"{number} (tied)" if str(rank).startswith("=") else str(number)


def _criterion_scale(criteria) -> float:
    """0-100 points per rubric point, from the live rubric.

    ``engine.review_score`` maps criterion values to 0-100, so the planted
    offsets (which are in rubric points) only compare with the fitted offsets
    after this conversion. It is 25 for the fixture's equal 1-5 criteria.
    """
    if not criteria:
        return 0.0
    total = sum(c.weight for c in criteria)
    if total <= 0:
        return 0.0
    return sum(100.0 * c.weight / (c.max_score - c.min_score) for c in criteria) / total


def _ordered(ids, ranks: dict) -> list[str]:
    """Ids by competition rank, best first, ties broken by id for a stable display."""
    return sorted(ids, key=lambda key: (_rank_number(ranks[key]) or 0, key))


def _top(ids, ranks: dict, k: int = TOP_K) -> list[str]:
    """The official top k, including every project tied at the boundary."""
    return [key for key in _ordered(ids, ranks) if (_rank_number(ranks[key]) or 0) <= k]


def _displaced(ids, ranks: dict, truth_ranks: dict) -> int:
    """Projects DISPLACEMENT or more places from their true rank."""
    count = 0
    for key in ids:
        here, true = _rank_number(ranks.get(key)), _rank_number(truth_ranks.get(key))
        if here is None or true is None:
            continue
        if abs(here - true) >= DISPLACEMENT:
            count += 1
    return count


def _judge_rows(preview: dict, scale: float, truth: dict, directory: dict) -> list[dict]:
    """One row per judge: planted offset against the fitted one, weakest habit first."""
    planted_offsets = truth["judge_offsets"]
    rows = []
    for row in preview.get("judge_rows", []):
        judge_id = row["judge_id"]
        known = directory.get(judge_id)
        email = known["email"] if known else ""
        planted = planted_offsets.get(email)
        if planted is None:
            continue
        planted_points = planted * scale
        estimated = float(row["offset"])
        rows.append({
            "judge_id": judge_id,
            "name": known["name"] if known else judge_id,
            "n": row["n"],
            "mean": row["mean"],
            "planted": planted,
            "planted_points": planted_points,
            "estimated": estimated,
            "error": abs(planted_points - estimated),
            "habit": showcase.planted_habit(planted),
            "sign_ok": (planted < 0) == (estimated < 0),
        })
    rows.sort(key=lambda row: (row["planted"], row["judge_id"]))
    return rows


def _verdict(judges: list[dict], order: dict, rho: float) -> str:
    """One sentence, entirely computed: what was planted and what came back."""
    planted = [row for row in judges if row["habit"] != "neutral"]
    signs = sum(1 for row in planted if row["sign_ok"])
    worst = max((row["error"] for row in planted), default=0.0)
    parts = [
        f"Normalization put the sign right for {signs} of the {len(planted)} planted habits"
        f" and the worst of them came back within {worst:.1f} points on the 0-100 scale",
        f"judge offsets ordered by the truth at Spearman rho {rho:.2f}",
    ]
    if order["tau_norm"] > order["tau_raw"]:
        parts.append(
            f"order recovery against the true quality rose from Kendall tau "
            f"{order['tau_raw']:.2f} on raw means to {order['tau_norm']:.2f} normalized"
        )
    else:
        parts.append(
            f"order recovery did not improve on this data: Kendall tau {order['tau_raw']:.2f} "
            f"on raw means against {order['tau_norm']:.2f} normalized"
        )
    parts.append(
        f"projects sitting {DISPLACEMENT} or more places from their true rank fell from "
        f"{order['off_raw']} on raw means to {order['off_norm']} normalized"
    )
    winner = order["winner"]
    if winner is not None:
        parts.append(
            f"the true winner {winner['title']} moved from rank {_rank_text(winner['rank_raw'])} "
            f"on raw means to rank {_rank_text(winner['rank_norm'])} normalized"
        )
    return "; ".join(parts) + "."


def calibration_report(event) -> dict:
    """Everything the calibration page shows, computed live from the current data."""
    preview = results_services.preview(event)
    truth = showcase.truth()
    scale = _criterion_scale(judging_policy.engine_criteria(event))

    titles = {row["project_id"]: row["title"] for row in preview["rows"]}
    quality = {
        project_id: truth["quality"][title]
        for project_id, title in titles.items()
        if title in truth["quality"]
    }
    raw = {row["project_id"]: row["raw_mean"] for row in preview["rows"] if row["raw_mean"] is not None}
    normalized = {
        row["project_id"]: row["normalized"]
        for row in preview["rows"] if row["normalized"] is not None
    }
    ids = sorted(quality)
    # Only projects the truth knows about, and only those with a score on both
    # sides, take part: an edited title or a fully excluded project must not
    # break the comparison or shift the competition ranks of the rest.
    scored = [i for i in ids if i in raw and i in normalized]
    true_ranks = engine.rank(quality)
    raw_ranks = engine.rank({i: raw[i] for i in scored})
    norm_ranks = engine.rank({i: normalized[i] for i in scored})
    order = {
        "tau_raw": engine.kendall_tau([quality[i] for i in scored], [raw[i] for i in scored]),
        "tau_norm": engine.kendall_tau([quality[i] for i in scored], [normalized[i] for i in scored]),
        "rho_raw": engine.spearman([quality[i] for i in scored], [raw[i] for i in scored]),
        "rho_norm": engine.spearman([quality[i] for i in scored], [normalized[i] for i in scored]),
        "off_raw": _displaced(scored, raw_ranks, true_ranks),
        "off_norm": _displaced(scored, norm_ranks, true_ranks),
        "top_true": _top(scored, true_ranks),
        "top_raw": _top(scored, raw_ranks),
        "top_norm": _top(scored, norm_ranks),
        "n": len(scored),
        "winner": None,
    }
    if order["top_true"]:
        winner_id = order["top_true"][0]
        order["winner"] = {
            "project_id": winner_id,
            "title": titles[winner_id],
            "quality": quality[winner_id],
            "rank_raw": raw_ranks.get(winner_id) or "unranked",
            "rank_norm": norm_ranks.get(winner_id) or "unranked",
        }

    judges = _judge_rows(preview, scale, truth, showcase.judge_directory())
    planted_points = [row["planted_points"] for row in judges]
    estimated = [row["estimated"] for row in judges]
    rho = engine.spearman(planted_points, estimated) if judges else float("nan")
    top_rows = []
    for place in range(max(len(order["top_true"]), len(order["top_raw"]), len(order["top_norm"]))):
        true_id = order["top_true"][place] if place < len(order["top_true"]) else None
        raw_id = order["top_raw"][place] if place < len(order["top_raw"]) else None
        norm_id = order["top_norm"][place] if place < len(order["top_norm"]) else None
        top_rows.append({
            "place": place + 1,
            "true": ({"title": titles[true_id], "quality": quality[true_id]}
                     if true_id else None),
            "raw": titles[raw_id] if raw_id else None,
            "norm": titles[norm_id] if norm_id else None,
        })
    return {
        "judges": judges,
        "offset_rho": rho,
        "planted_count": sum(1 for row in judges if row["habit"] != "neutral"),
        "signs_ok": sum(1 for row in judges if row["habit"] != "neutral" and row["sign_ok"]),
        "worst_error": max((row["error"] for row in judges if row["habit"] != "neutral"), default=0.0),
        "order": order,
        "top_rows": top_rows,
        "verdict": _verdict(judges, order, rho),
        "design": truth["design"],
        "lam": preview["lam"],
        "lambda_source": preview["params"].get("lambda_source", "fixed"),
        "method": preview["method"],
        "n_reviews": preview["n_included"],
        "n_excluded": preview["n_excluded"],
        "components": preview.get("diagnostics", {}).get("n_components"),
        "scale": scale,
    }


@login_required
def calibration(request, slug: str):
    """/manage/{slug}/calibration: planted truth against the recovered numbers.

    Organizer only, like every other /manage/ page. 404 for any other event: the
    truth is a property of the generated showcase, and inventing one for a real
    event would be a lie.
    """
    event = get_object_or_404(visible_events(), slug=slug)
    if not showcase.is_showcase_event(event):
        raise Http404("The planted truth is only known for the calibration showcase.")
    if not can_manage(request.user, event):
        raise PermissionDenied("Only organizers of this event can open this page.")
    return render(request, "manage/calibration.html", {
        "event": event,
        "calibration": calibration_report(event),
    })
