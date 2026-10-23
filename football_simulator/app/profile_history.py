"""Read-only season views built from frozen results and persisted match facts."""
from collections import defaultdict

from football_simulator.app import records
from football_simulator.game import rules, world as game_world
from football_simulator.persistence_v2 import store

COMPETITIONS = (rules.PREMIER, rules.SECOND, *rules.CUP_IDS, rules.PLAYOFF)
FINISH_LABELS = {
    "champion": "冠军", "runner_up": "亚军", "semifinal": "四强", "quarterfinal": "八强",
    "group_third": "小组第三", "group_fourth": "小组第四", "round_16": "十六强", "round_32": "三十二强",
    "swiss_2_3": "瑞士轮 2–3 出局", "swiss_1_3": "瑞士轮 1–3 出局", "swiss_0_3": "瑞士轮 0–3 出局",
    "third": "季军", "fourth": "第四名", "fifth_sixth": "并列第五", "seventh_eighth": "并列第七",
    "play_in_exit": "突围赛出局", "nirvana_sixth": "涅槃组第六",
}


def season_options(world):
    return sorted({int(world["season_number"]), *(int(a["season_number"]) for a in world.get("archives", []))}, reverse=True)


def player_season_stats(world, save_name, player_id, season_number):
    selected = int(season_number)
    splits = records.career_splits(save_name, player_id)
    actual = {row["competition"]: row for row in splits["season_competition"] if int(row["season"]) == selected}
    rows = [{"competition": comp, "label": rules.display_name(comp), **{key: int(actual.get(comp, {}).get(key, 0)) for key in ("appearances", "goals", "assists")}} for comp in COMPETITIONS]
    team_rows = player_team_seasons(world, save_name, player_id, season=selected, by_competition=True)
    for comp in COMPETITIONS:
        if not any(row["competition"] == comp for row in team_rows):
            team_rows.append({"season": selected, "competition": comp, "label": rules.display_name(comp), "team": "—", "team_id": None, "club": "", "appearances": 0, "goals": 0, "assists": 0})
    team_rows.sort(key=lambda row: (COMPETITIONS.index(row["competition"]), row.get("first_week", 0), row.get("team_id") or ""))
    return {"season": selected, "options": season_options(world), "rows": rows, "team_rows": team_rows,
            "totals": {key: sum(row[key] for row in rows) for key in ("appearances", "goals", "assists")}}


def player_team_seasons(world, save_name, player_id, *, season=None, by_competition=False):
    facts = [row for row in records._facts(save_name) if row["player_id"] == player_id and (season is None or int(row["season_number"]) == int(season))]
    grouped = {}
    for fact in facts:
        number, team_id = int(fact["season_number"]), fact["team_id"]
        comp = fact["competition"] if by_competition else None
        row = grouped.setdefault((number, comp, team_id), {"season": number, "competition": comp,
            "label": rules.display_name(comp) if comp else "", "team_id": team_id, "team": world["teams"].get(team_id, {}).get("club_en", "历史球队"),
            "club": world["teams"].get(team_id, {}).get("club_en", ""), "first_week": int(fact["week"]), "appearances": 0, "goals": 0, "assists": 0})
        row["first_week"] = min(row["first_week"], int(fact["week"]))
        row["appearances"] += int(fact["appeared"])
        row["goals"] += int(fact["goals"])
        row["assists"] += int(fact["assists"])
    return sorted(grouped.values(), key=lambda row: (-row["season"], row["first_week"], row["team_id"]))


def draft_history(world):
    """Archive-first dedupe; the live edition's latest log takes precedence."""
    logs = {}
    for archive in world.get("archives", []):
        for log in archive.get("draft", []):
            logs[int(log["season"])] = log
    for log in world["season"].get("draft_log", []):
        logs[int(log["season"])] = log
    return [logs[number] for number in sorted(logs, reverse=True)]


def player_origin(world, player_id):
    stored = world["players"].get(player_id, {}).get("draft_origin")
    if stored:
        return {**stored, "label": f"第 {stored['season']} 赛季选秀 · 第 {stored['pick']} 顺位"}
    for log in draft_history(world):
        for pick, row in enumerate(log.get("results", []), 1):
            if row.get("player_id") == player_id:
                actual = int(row.get("pick") or pick)
                return {"season": int(log["season"]), "pick": actual, "team_id": row["team_id"], "label": f"第 {log['season']} 赛季选秀 · 第 {actual} 顺位"}
    return {"season": None, "pick": None, "team_id": None, "label": "初始球员"}


def team_season_results(world, save_name, team_id):
    conn = store.open_readonly(save_name)
    try:
        matches = [dict(row) for row in conn.execute(
            "SELECT season_number, competition, stage, status, home_team_id, away_team_id, home_goals, away_goals, home_goals_et, away_goals_et, winner_team_id FROM matches WHERE home_team_id = ? OR away_team_id = ?",
            (team_id, team_id),
        )]
    finally:
        conn.close()
    by_event = defaultdict(list)
    for match in matches:
        by_event[(int(match["season_number"]), match["competition"])].append(match)
    archives = {int(archive["season_number"]): archive for archive in world.get("archives", [])}
    current = int(world["season_number"])
    live_tables = world["season"].get("final_tables") or game_world.league_tables(world)
    result = []
    for season in season_options(world):
        archive = archives.get(season)
        tables = {"premier": archive.get("premier_table", []), "second": archive.get("second_table", [])} if archive else live_tables
        finalized = bool(archive or world["season"].get("finalized"))
        for competition in COMPETITIONS:
            fixtures = by_event[(season, competition)]
            completed = [match for match in fixtures if match["status"] == "completed"]
            row = {"season": season, "competition": competition, "label": rules.display_name(competition), "result": "未参赛",
                   "played": len(completed), "wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0}
            for match in completed:
                home = match["home_team_id"] == team_id
                goals = [int(match[key] or 0) + int(match[key + "_et"] or 0) for key in ("home_goals", "away_goals")]
                gf, ga = goals if home else goals[::-1]
                row["goals_for"] += gf
                row["goals_against"] += ga
                row["wins" if gf > ga else "losses" if gf < ga else "draws"] += 1
            if competition in (rules.PREMIER, rules.SECOND):
                ranking = [entry["team_id"] for entry in tables[competition]]
                if team_id in ranking:
                    rank = ranking.index(team_id) + 1
                    row["result"] = f"第 {rank} 名" if finalized else f"暂列第 {rank} 名" if completed else "未开赛"
            elif competition in rules.CUP_IDS:
                placements = (archive.get("cup_placements", {}).get(competition, {}) if archive else world["season"].get("cups", {}).get(competition, {}).get("placements", {}))
                rank = placements.get(team_id)
                if rank is not None:
                    row["result"] = FINISH_LABELS[rules.honor_key_for_rank(competition, int(rank))]
                elif not rules.cup_active(competition, season):
                    row["result"] = "未举办"
                elif fixtures or (season == current and not archive and team_id in world["season"].get("cups", {}).get(competition, {}).get("participants", [])):
                    row["result"] = "进行中" if completed else "待开赛"
            elif fixtures:
                transition = (archive or world["season"]).get("transition", {})
                winner = transition.get("playoff_winner") or (world["season"].get("promotion_playoff", {}).get("winner") if season == current else None)
                if winner == team_id:
                    row["result"] = "附加赛冠军 · 升级"
                elif finalized:
                    row["result"] = "亚军" if any("final" in match["stage"] and "semifinal" not in match["stage"] for match in fixtures) else "四强"
                else:
                    row["result"] = "进行中" if completed else "待开赛"
            result.append(row)
    return result


def team_season_matrix(world, save_name, team_id):
    """One row per season, with all nine saved competition results as columns."""
    seasons = {}
    for result in team_season_results(world, save_name, team_id):
        row = seasons.setdefault(result["season"], {"season": result["season"]})
        text = result["result"]
        if result["competition"] in (rules.PREMIER, rules.SECOND) and text == "第 1 名":
            text = "冠军"
        text = text.replace("瑞士轮 ", "").replace("附加赛冠军 · 升级", "冠军 · 升级")
        row[result["competition"]] = text
    archives = {int(a["season_number"]): a for a in world.get("archives", [])}
    for number, row in seasons.items():
        edition = archives.get(number) or (world["season"] if number == int(world["season_number"]) and world["season"].get("finalized") else None)
        row["honor_points"] = int(edition.get("honor_totals", {}).get(team_id, 0)) if edition else "待结算"
    return [seasons[number] for number in sorted(seasons, reverse=True)]
