"""Read-only decision support, not a new scoring system or publication bypass.

Findings describe concrete missing evidence and lawful next actions. A green
room is not a fairness guarantee: publication still rechecks its own guards.
"""
from core.clock import now
from core.decision_policy import managed_event
from core.errors import ApiError
from judging.services import command_center
from results import policy, services


def decision_room(user, slug):
    event = managed_event(user, slug)
    stamp = now()
    base = f"/manage/{event.slug}"
    findings = []

    def finding(code, severity, title, detail, path, label, count=None):
        findings.append({
            "code": code, "severity": severity, "title": title,
            "detail": detail, "count": count,
            "action": {"href": base + path, "label": label},
        })

    if event.judging_close_at is None or stamp < event.judging_close_at:
        finding("judging_open", "blocker", "Judging is not closed",
                "Publication is blocked while judges can still submit. Close judging only when the panel is finished.",
                "/", "Review event controls")
    voting_configured = (event.voting_open_at is not None or event.voting_close_at is not None
                         or hasattr(event, "voting_config"))
    if voting_configured and (
        event.voting_close_at is None or stamp < event.voting_close_at
    ):
        finding("voting_open", "blocker", "Community voting is not closed",
                "Results must stay hidden until the configured voting window closes.",
                "/voting", "Review voting")

    preview = None
    try:
        preview = services.preview(event)
    except ApiError as error:
        if error.code != "no_rubric":
            raise
        finding("no_rubric", "blocker", "No scoring rubric",
                "Configure the event's scoring rules before calculating results.",
                "/rubric", "Configure rubric")

    coverage = []
    unranked = []
    if preview is not None:
        eligible = [row for row in preview["rows"]
                    if row["status"] not in {"withdrawn", "disqualified", "superseded", "draft"}]
        if not eligible:
            finding("no_projects", "blocker", "No eligible submitted projects",
                    "An empty result does not demonstrate a completed event.",
                    "/assignments", "Review project eligibility")
        unranked = [row for row in eligible if row["status"] != "ranked"]
        if unranked:
            finding("unranked_projects", "blocker", "Projects have no comparable result",
                    "Collect missing evidence. If the rules permit publication without these projects, explicitly acknowledge the omission in Results; this page never does that for you.",
                    "/results", "Review unranked projects", len(unranked))
        if preview["method"] != "pairwise":
            coverage = [row for row in eligible if row["n_reviews"] < event.reviews_per_project]
            if coverage:
                finding("coverage_shortfall", "warning", "Review coverage is below target",
                        "Counts include eligible, non-excluded submitted reviews, not assignment promises. Preview legal assignments before asking for more reviews.",
                        "/assignments", "Preview assignments", len(coverage))
        ties = [row for row in preview.get("unawarded", []) if len(row.get("projects", [])) > 1]
        if ties:
            finding("prize_ties", "warning", "Prizes have unresolved ties",
                    "Equal official scores are not broken by project name. Follow a predeclared tie policy or leave the award explicitly unresolved; do not invent a post-hoc scoring rule.",
                    "/results", "Inspect tied awards", len(ties))
        other_unawarded = len(preview.get("unawarded", [])) - len(ties)
        if other_unawarded:
            finding("unawarded_prizes", "warning", "Some prizes have no eligible award",
                    "Check track scope, eligibility and the one-prize-per-team policy before announcing awards.",
                    "/results", "Inspect prize allocation", other_unawarded)
        components = preview.get("diagnostics", {}).get("n_components", 0)
        if components > 1:
            finding("disconnected_evidence", "warning", "The evidence network is disconnected",
                    "Cross-group comparisons depend on assumptions not established by shared reviews. Inspect the graph and its limitations before treating the global order as conclusive.",
                    "/results", "Inspect judging diagnostics", components)

    command = command_center(user, event)
    forecast = command["forecast"]
    at_risk = forecast["at_risk_count"]
    if at_risk:
        finding("panel_at_risk", "warning", "Judging work needs attention",
                "Pace is estimated only from live submissions. Review a rebalance preview; drafted or submitted work must stay with its judge.",
                "/command-center", "Preview safe rebalance", at_risk)

    publication = policy.latest_publication(event)
    publication_summary = None
    if publication:
        raw_rows = publication.rows
        raw_params = publication.params
        row_count = len(raw_rows) if isinstance(raw_rows, list) else None
        params = raw_params if isinstance(raw_params, dict) else {}
        publication_summary = {
            "id": publication.public_id,
            "verification": "not_run",
            "published_at": publication.published_at,
            "row_count": row_count,
            "component_hint": params.get("n_components"),
        }
        finding("publication_unverified", "warning", "Published record has not been re-verified here",
                "This page never re-runs verification automatically, so it cannot say whether the published "
                "record still matches recomputation or the current data. Open the decision record and click "
                "Verify to check that explicitly before treating this result as current.",
                f"/results/publications/{publication.public_id}", "Open decision record and verify")
    blockers = sum(row["severity"] == "blocker" for row in findings)
    warnings = sum(row["severity"] == "warning" for row in findings)
    state = "blocked" if blockers else "review" if warnings else "ready_for_review"
    return {
        "event": event.slug, "event_name": event.name, "as_of": stamp,
        "state": state, "blocker_count": blockers, "warning_count": warnings,
        "findings": findings,
        "method": event.ranking_method,
        "preview_digest": preview["input_digest"] if preview else None,
        "coverage": [{"project": row["project_id"], "title": row["title"],
                      "reviews": row["n_reviews"], "target": event.reviews_per_project}
                     for row in coverage[:25]],
        "coverage_count": len(coverage),
        "unranked_count": len(unranked),
        "panel": {"at_risk": at_risk, "proposed_moves": len(command["proposal"]["moves"]),
                  "unknown_pace": sum(row["remaining"] > 0 and row["pace_minutes"] is None
                                      for row in forecast["judges"])},
        "publication": publication_summary,
        "limits": [
            "This is an advisory read of current data, not authorization to publish or a fairness score.",
            "Winner sensitivity is not a probability of being best. Inspect named perturbations in Results; pairwise sensitivity may be unavailable.",
            "Preview data can change after this page loads. Publication rechecks deadlines and permissions and stores its own decision snapshot.",
            "No scoring rules, judge assignments, ballots or publication records are changed by viewing this page.",
        ],
        "links": {"results": base + "/results", "audit": base + "/audit",
                  "exports": base + "/exports", "command_center": base + "/command-center"},
    }
