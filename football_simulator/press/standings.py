"""Week-boundary comparisons for reporting, never changes sporting rankings."""
from __future__ import annotations


def season_records(team_ids, played, previous_rank):
    """Complete current-league evidence, including losses before an unbeaten run.

    Leadership here means the table at the end of a completed week, not an
    initial seed or a temporary position between two matches in the same week.
    """
    from football_simulator.game.calendar import standings_from_matches
    from football_simulator.press.builder import field_result, order

    games = sorted(played, key=order)
    rows = standings_from_matches(team_ids, games, previous_rank=previous_rank)
    first_leads = {}
    for week in sorted({m["week"] for m in games}):
        table = standings_from_matches(team_ids, [m for m in games if m["week"] <= week], previous_rank=previous_rank)
        first_leads.setdefault(table[0]["team_id"], week)
    return {r["team_id"]: {
        **{k: r[k] for k in ("wins", "draws", "losses", "goal_diff")},
        "first_loss_match_id": next((m["match_id"] for m in games
            if r["team_id"] in (m["home_team_id"], m["away_team_id"]) and field_result(m, r["team_id"]) == "L"), None),
        "first_lead_week": first_leads.get(r["team_id"]),
    } for r in rows}


def compare_tables(before, after):
    """Input rows already use the game's official tie-break order.

    A zero-match initial seed order is not an achieved league ranking. Gap
    growth describes the same leader only; a new leader gets no invented gain.
    """
    ranked_before = any(row["played"] for row in before)
    ranked_after = any(row["played"] for row in after)
    old = {row["team_id"]: (i, row) for i, row in enumerate(before, 1)}
    changes = {}
    for rank, row in enumerate(after, 1):
        previous_rank, previous = old[row["team_id"]]
        changes[row["team_id"]] = {
            "basis": "completed_week_start_to_end",
            "before_rank": previous_rank if ranked_before else None,
            "before_points": previous["points"], "before_played": previous["played"],
            "rank_change": previous_rank - rank if ranked_before and ranked_after else None,
            "points_change": row["points"] - previous["points"],
        }
    old_leader = before[0]["team_id"] if ranked_before else None
    new_leader = after[0]["team_id"] if ranked_after else None
    old_gap = before[0]["points"] - before[1]["points"] if ranked_before and len(before) > 1 else None
    new_gap = after[0]["points"] - after[1]["points"] if ranked_after and len(after) > 1 else None
    leader_changed = old_leader != new_leader if old_leader and new_leader else None
    context = {"before_leader_team_id": old_leader, "leader_changed": leader_changed,
               "before_gap": old_gap, "after_gap": new_gap,
               "same_leader_gap_change": new_gap - old_gap if leader_changed is False and old_gap is not None and new_gap is not None else None}
    return changes, context


def equivalent_streaks(facts):
    """Group only records backed by the exact same ordered match evidence."""
    groups = {}
    for fact in facts:
        if fact["kind"] != "streak":
            continue
        d = fact["data"]
        keys = tuple(s["key"] for s in fact["sources"] if s["kind"] == "match")
        signature = (d["team_id"], d["kind"], d["affected_match_id"], d["before"], d["after"], keys)
        groups.setdefault(signature, []).append(fact)
    result = []
    for items in groups.values():
        if len(items) < 2:
            continue
        # When league and overall sequences are identical, say it once using
        # the league scope. Distinct cup sequences never merge on count alone.
        preferred = next((f for f in items if f["data"]["scope"] == "league_all_levels"), items[0])
        result.append({"preferred_fact_id": preferred["fact_id"], "equivalent_fact_ids": [f["fact_id"] for f in items]})
    return result
