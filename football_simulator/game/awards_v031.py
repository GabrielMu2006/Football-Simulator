"""V0.3.1 awards for new saves, based exclusively on recorded match facts."""
from copy import deepcopy

from football_simulator.domain.formulas import calculate_player_rating_v031
from football_simulator.game import editions, rules

EVP_COUNTS = {rules.PREMIER: 8, "aureole": 5, "starpath": 5,
              "ascendant": 4, "ember": 4, "confluence": 3, "zenith": 1}
# The proposed prestige scale follows the confirmed award tiers, rather than
# treating the number of awards as a count of a player's achievements.
PRESTIGE = {competition: count / 8 for competition, count in EVP_COUNTS.items()}


def _finalists(world, competition):
    from football_simulator.game import world as game_world
    if competition == rules.PREMIER:
        table = world["season"].get("final_tables", {}).get("premier") or game_world.league_tables(world)["premier"]
        return {row["team_id"] for row in table[:2]}
    cup = world["season"].get("cups", {}).get(competition, {})
    return {team for team in (cup.get("champion"), cup.get("runner_up")) if team}


def competition_awards(world, competition_ids=None):
    from football_simulator.game import world as game_world
    result = {}
    ids = competition_ids if competition_ids is not None else [rules.PREMIER, rules.SECOND, *rules.CUP_IDS]
    for competition in ids:
        frozen = world.get("edition_archives", {}).get(editions.key(world["season_number"], competition))
        if frozen is not None:
            result[competition] = deepcopy(frozen.get("awards") or {})
            continue
        if competition == rules.PLAYOFF or not editions.finished(world, competition):
            continue
        matches = game_world.completed_matches(world, competition)
        team_counts = {}
        for match in matches:
            for field in ("home_team_id", "away_team_id"):
                team_counts[match[field]] = team_counts.get(match[field], 0) + 1
        totals = game_world._aggregate_player_facts(world, matches)
        finalists = _finalists(world, competition) if competition in EVP_COUNTS else set()
        rows = []
        for pid, stats in totals.items():
            data = world["players"].get(pid)
            if not data or not data.get("is_real") or not stats["appearances"]:
                continue
            player = game_world._player_object(world, pid)
            raw = calculate_player_rating_v031(game_world._stats_namespace(player, stats), stats["appearances"], rounded=False)
            # Shrink short samples towards an average rather than banning them.
            effective = 7 + (raw - 7) * stats["appearances"] / (stats["appearances"] + 4)
            current = data.get("team_id")
            team = current if current in stats["teams"] else max(stats["teams"], key=stats["teams"].get, default=None)
            rows.append({"player_id": pid, "label": player.display_name, "position": player.position,
                         "team_id": team, "team_name": world["teams"].get(team, {}).get("club_en", ""),
                         "teams": dict(stats["teams"]), "appearances": stats["appearances"],
                         "goals": stats["goals"], "assists": stats["assists"],
                         "rating": round(raw, 2), "_rating": effective,
                         "_mvp_eligible": current in finalists and current in stats["teams"]})
        scoring = game_world._award_winners(rows, lambda r: (-r["goals"], -r["assists"], r["appearances"]), value_field="goals", require_positive=True)
        assists = game_world._award_winners(rows, lambda r: (-r["assists"], -r["goals"], r["appearances"]), value_field="assists", require_positive=True)
        order = lambda r: (-r["_rating"], -r["appearances"], r["player_id"])
        ranked = sorted(rows, key=order)
        winner = next((row for row in ranked if row["_mvp_eligible"]), None)
        mvp = [winner] if winner else []
        evp = [row for row in ranked if row is not winner][:EVP_COUNTS.get(competition, 0)]
        def payload(selected, field):
            out = game_world._award_payload(selected, field)
            for item, row in zip(out, selected):
                item["position"] = row["position"]
                item["teams"] = row["teams"]
            return out
        result[competition] = {"rule_version": "0.3.1", "max_team_matches": max(team_counts.values(), default=0),
                               "evp_slots": EVP_COUNTS.get(competition, 0),
                               "top_scorer": payload(scoring, "goals"), "assist_leader": payload(assists, "assists"),
                               "mvp": payload(mvp, "rating"), "evp": payload(evp, "rating")}
    return result


def season_awards(world):
    from football_simulator.game import world as game_world
    totals = game_world._aggregate_player_facts(world, game_world.completed_matches(world))
    awards = competition_awards(world)
    award_points = {}
    for competition, values in awards.items():
        for category, factor in (("mvp", 3), ("evp", 1)):
            for item in values.get(category, []):
                pid = item["player_id"]
                award_points[pid] = award_points.get(pid, 0) + factor * PRESTIGE.get(competition, 0)
    # Credit only the team registered at this edition's end, and only when
    # the player actually appeared for it in the edition.
    team_points = {}
    for honor in world["season"].get("honor_rows", []):
        competition = honor["competition"]
        if competition not in team_points:
            team_points[competition] = game_world._aggregate_player_facts(world, game_world.completed_matches(world, competition))
        event_stats = team_points[competition]
        edition = world.get("edition_archives", {}).get(editions.key(world["season_number"], competition))
        registration = (edition or {}).get("player_teams")
        if registration is None:
            registration = world["season"].get("award_team_snapshots", {}).get(competition, {})
        for pid, stats in event_stats.items():
            if registration.get(pid) == honor["team_id"] and honor["team_id"] in stats["teams"]:
                stats.setdefault("_honor", {})[honor["team_id"]] = honor["points"]
    rows = []
    for pid, stats in totals.items():
        data = world["players"].get(pid)
        if not data or not data.get("is_real") or not stats["appearances"]:
            continue
        player = game_world._player_object(world, pid)
        rating = calculate_player_rating_v031(game_world._stats_namespace(player, stats), stats["appearances"], rounded=False)
        performance = max(0.0, min(1.0, (rating - 4.6) / 4.8)) * stats["appearances"] / (stats["appearances"] + 4)
        honor = sum(sum(event.get(pid, {}).get("_honor", {}).values()) for event in team_points.values())
        team = data.get("team_id") or next(iter(stats["teams"]), None)
        if not team:
            continue
        rows.append({"player_id": pid, "label": player.display_name, "position": player.position,
                     "team_id": team, "team_name": world["teams"].get(team, {}).get("club_en", ""),
                     "ability": player.ability, "rating": round(rating, 2), "market_value": data.get("market_value"),
                     "goals": stats["goals"], "assists": stats["assists"], "appearances": stats["appearances"],
                     "_performance": performance, "_award": award_points.get(pid, 0), "_honor": honor})
    max_award = max((r["_award"] for r in rows), default=0) or 1
    max_honor = max((r["_honor"] for r in rows), default=0) or 1
    for row in rows:
        parts = {"performance": row.pop("_performance"), "awards": row.pop("_award") / max_award,
                 "team_honor": row.pop("_honor") / max_honor}
        row["score"] = 100 * (.35 * parts["performance"] + .55 * parts["awards"] + .10 * parts["team_honor"])
        row["components"] = parts
    rows.sort(key=lambda r: (-r["score"], -r["rating"], -r["appearances"], r["player_id"]))
    for rank, row in enumerate(rows[:20], 1):
        row["rank"] = rank
    return {"top20": rows[:20], "competitions": awards}
