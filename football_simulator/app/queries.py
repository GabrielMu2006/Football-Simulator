"""只读查询层：把世界事实组装成页面 DTO。

- 不写盘、不模拟、不抽签、不消费随机数。
- 当前赛季使用权威 world JSON；历史赛季使用规范化 matches /
  player_match_stats 表（跨赛季保留）。
- 页面不接触 SQL，只使用这里返回的 DTO。
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from football_simulator.game import rules, world as game_world
from football_simulator.game.calendar import completed_matches, standings_from_matches
from football_simulator.persistence_v2 import store

STAT_FIELDS = ("goals", "assists", "chances_created", "successful_defenses", "successful_saves", "clean_sheets")


# ---------------------------------------------------------------------------
# 基础格式
# ---------------------------------------------------------------------------


def team_label(world: dict, team_id: Optional[str]) -> str:
    if not team_id or team_id not in world["teams"]:
        return "待定"
    return world["teams"][team_id]["club_en"]


def team_short(world: dict, team_id: Optional[str]) -> str:
    if not team_id or team_id not in world["teams"]:
        return "待定"
    return world["teams"][team_id]["club_en"]


def player_name(world: dict, player_id: str) -> str:
    data = world["players"].get(player_id)
    if not data:
        return player_id
    return data.get("name") or f"默认球员 {data['position']}{data['slot_number']}"


def stadium_name(stadium_id: Optional[str], *, with_club: bool = True) -> str:
    item = rules.stadium_by_id(stadium_id) if stadium_id else None
    if not item:
        return "待定球场"
    text = f"{item['name_en']} · {item['name_zh']}"
    if with_club:
        text += f"（{item['club_en']}）"
    return text


def stage_text(competition: str, stage: str) -> str:
    if stage == "league":
        return ""
    if stage.startswith("group_"):
        return f"小组赛第 {stage.split('_')[-1]} 轮"
    if stage.startswith("cross_group_"):
        return f"组外循环第 {stage.split('_')[-1]} 轮"
    if stage.startswith("swiss_"):
        return f"瑞士轮第 {stage.split('_')[-1]} 轮"
    if stage.endswith("_leg_1"):
        return f"{_base_stage_label(stage[:-6])}首回合"
    if stage.endswith("_leg_2"):
        return f"{_base_stage_label(stage[:-6])}次回合"
    labels = {
        "round_32": "32强",
        "round_16": "16强",
        "quarterfinal": "八强",
        "semifinal": "半决赛",
        "final": "决赛",
        "play_in": "突围赛",
        "wb_quarterfinal": "胜者组首轮",
        "wb_semifinal": "胜者组次轮",
        "wb_final": "胜者组决赛",
        "lb_round_1": "败者组首轮",
        "lb_round_2": "败者组次轮",
        "lb_round_3": "败者组第三轮",
        "lb_final": "败者组决赛",
        "grand_final": "总决赛",
        "promotion_semifinal_leg_1": "升级附加赛半决赛首回合",
        "promotion_semifinal_leg_2": "升级附加赛半决赛次回合",
        "promotion_final_leg_1": "升级附加赛决赛首回合",
        "promotion_final_leg_2": "升级附加赛决赛次回合",
    }
    return labels.get(stage, stage)


def _base_stage_label(stage: str) -> str:
    return {
        "quarterfinal": "八强",
        "semifinal": "半决赛",
        "final": "决赛",
        "promotion_semifinal": "升级附加赛半决赛",
        "promotion_final": "升级附加赛决赛",
    }.get(stage, stage)


def score_text(fixture: dict) -> str:
    home = int(fixture.get("home_goals") or 0) + int(fixture.get("home_goals_et") or 0)
    away = int(fixture.get("away_goals") or 0) + int(fixture.get("away_goals_et") or 0)
    return f"{home}–{away}"


def fixture_status(fixture: dict) -> str:
    if fixture.get("status") != "completed":
        return "未赛"
    if fixture.get("home_pk") is not None:
        return "点球"
    if fixture.get("extra_time_played"):
        return "加时"
    return "已完赛"


def fixture_dto(world: dict, fixture: dict) -> dict:
    home_id = fixture["home_team_id"]
    away_id = fixture["away_team_id"]
    return {
        "match_id": fixture["match_id"],
        "competition": fixture["competition"],
        "competition_label": rules.competition_label(fixture["competition"]) if fixture["competition"] in rules.CUP_IDS else rules.display_name(fixture["competition"]),
        "stage": fixture.get("stage"),
        "stage_text": stage_text(fixture["competition"], fixture.get("stage", "")),
        "week": int(fixture.get("week", 0)),
        "home_team_id": home_id,
        "away_team_id": away_id,
        "home_name": team_short(world, home_id),
        "away_name": team_short(world, away_id),
        "home_label": team_label(world, home_id),
        "away_label": team_label(world, away_id),
        "home_goals": int(fixture.get("home_goals") or 0),
        "away_goals": int(fixture.get("away_goals") or 0),
        "home_goals_et": int(fixture.get("home_goals_et") or 0),
        "away_goals_et": int(fixture.get("away_goals_et") or 0),
        "total_home": int(fixture.get("home_goals") or 0) + int(fixture.get("home_goals_et") or 0),
        "total_away": int(fixture.get("away_goals") or 0) + int(fixture.get("away_goals_et") or 0),
        "home_pk": fixture.get("home_pk"),
        "away_pk": fixture.get("away_pk"),
        "status": fixture.get("status"),
        "status_text": fixture_status(fixture),
        "decided_by": fixture.get("decided_by"),
        "score": score_text(fixture) if fixture.get("status") == "completed" else "vs",
        "venue_mode": fixture.get("venue_mode"),
        "venue": stadium_name(fixture.get("stadium_id")),
        "neutral_reason": fixture.get("neutral_reason"),
        "stadium_id": fixture.get("stadium_id"),
        "leg": fixture.get("leg", 1),
        "tie_id": fixture.get("tie_id"),
        "winner_team_id": fixture.get("winner_team_id"),
        "has_extra_time": bool(fixture.get("extra_time_played")),
    }


# ---------------------------------------------------------------------------
# 首页 / 周报
# ---------------------------------------------------------------------------


def dashboard(world: dict, save_name: str) -> dict:
    season = world["season"]
    week = int(season["week"])
    last_week = week - 1
    tables = game_world.league_tables(world)
    last_results = [
        fixture_dto(world, fixture)
        for fixture in completed_matches(world)
        if int(fixture["week"]) == last_week
    ]
    last_results.sort(key=lambda item: (0 if item["competition"] == rules.PREMIER else 1, item["match_id"]))
    upcoming = [
        fixture_dto(world, fixture)
        for fixture in season["fixtures"]
        if fixture["status"] != "completed" and int(fixture["week"]) == week
    ]
    upcoming.sort(key=lambda item: (0 if item["competition"] == rules.PREMIER else 1, item["stage_text"], item["match_id"]))
    season_events = [
        event
        for event in world.get("story_events", [])
        if int(event.get("season", 0)) == int(world["season_number"])
    ]
    # “本周重点”只取最近一个已完赛周；更早的阶段结论留给赛季/赛事页。
    cutoff = max(1, last_week)
    recent_events = [event for event in season_events if int(event.get("week", 0)) >= cutoff]
    key_events = [event for event in recent_events if event.get("type") != "match_result"][-3:][::-1]
    results_fill = [event for event in reversed(recent_events) if event.get("type") == "match_result"][: max(0, 3 - len(key_events))]
    stories = (key_events + results_fill)[:3]
    standings = []
    previous_tables = _standings_before(world, last_week)
    previous_rank = {row["team_id"]: index + 1 for index, row in enumerate(previous_tables.get("premier", []))}
    for index, row in enumerate(tables["premier"][:6], start=1):
        standings.append(
            {
                "rank": index,
                "previous_rank": previous_rank.get(row["team_id"]),
                "team_id": row["team_id"],
                "team": team_short(world, row["team_id"]),
                "club": (world["teams"].get(row["team_id"]) or {}).get("club_en", ""),
                "played": row["played"],
                "points": row["points"],
                "goal_diff": row["goal_diff"],
            }
        )
    watchlist = world.get("settings", {}).get("watchlist", [])
    watch_rows = []
    for entity_id in watchlist:
        if entity_id in world["teams"]:
            table = next((row for row in tables["premier"] + tables["second"] if row["team_id"] == entity_id), None)
            watch_rows.append(
                {
                    "type": "team",
                    "id": entity_id,
                    "label": team_label(world, entity_id),
                    "detail": f"第 {tables['premier'].index(table) + 1} 名" if table in tables["premier"] else "次级联赛",
                }
            )
        elif entity_id in world["players"]:
            data = world["players"][entity_id]
            team_id = data.get("team_id")
            totals = _season_totals(world).get(entity_id, {})
            watch_rows.append(
                {
                    "type": "player",
                    "id": entity_id,
                    "label": player_name(world, entity_id),
                    "detail": f"{team_short(world, team_id)} · {data['position']} · {totals.get('goals', 0)} 球",
                }
            )
    pending = season["pending"]
    pending_items = []
    if pending.get("ability_review"):
        pending_items.append({"id": "resolve_ability", "label": f"能力审核 · {len(pending['ability_review'])} 项", "detail": "季末能力变化等待确认"})
    if pending.get("draft", {}).get("status") == "awaiting_input":
        pending_items.append({"id": "run_draft", "label": f"选秀 · {len(pending['draft'].get('candidates', []))} 名候选", "detail": "按倒序选秀顺序分配"})
    if pending.get("transfer_review"):
        pending_items.append({"id": "resolve_transfers", "label": f"转会审核 · {len(pending['transfer_review'])} 笔", "detail": "批准或拒绝提案"})
    headline = _headline(world, tables, last_results, stories)
    return {
        "season": world["season_number"],
        "week": week,
        "last_week": last_week,
        "completed_week": max(0, week - 1),
        "next_week": week,
        "week_started": week > 1,
        "headline": headline,
        "stories": stories,
        "last_results": last_results[:16],
        "last_result_count": len(last_results),
        "played_week": max(0, min(52, last_week)),
        "played_results": last_results,
        "played_count": len(last_results),
        "upcoming": upcoming[:16],
        "upcoming_count": len(upcoming),
        "standings": standings,
        "pending": pending_items,
        "watchlist": watch_rows,
        "season_complete": bool(season.get("season_complete")),
        "finalized": bool(season.get("finalized")),
        "champions": _champion_rows(world),
        "transition": season.get("transition") or {},
        "next_week_slots": _week_slots(week, world["season_number"]),
    }


def _headline(world: dict, tables: dict, last_results: List[dict], stories: List[dict]) -> str:
    season = world["season"]
    if season.get("season_complete"):
        return "赛季已结束：回顾结算并开始下一赛季。"
    if season["pending"].get("ability_review"):
        return "赛季末能力审核等待确认。"
    if season["pending"].get("draft", {}).get("status") == "awaiting_input":
        return "选秀候选已经就绪，等待分配。"
    if season["pending"].get("transfer_review"):
        return "转会窗口有新的提案等待批准。"
    if stories:
        return stories[0].get("text", "")
    premier = tables.get("premier") or []
    if premier and any(int(row.get("played", 0)) > 0 for row in premier):
        leader = premier[0]
        return f"{team_short(world, leader['team_id'])} 以 {leader['points']} 分领跑一级联赛。"
    return "新世界已经建立，等待第 1 周比赛。"


def weekly_report(world: dict, week: Optional[int] = None) -> dict:
    season = world["season"]
    week = int(week or max(1, season["week"] - 1))
    fixtures = [fixture for fixture in completed_matches(world) if int(fixture["week"]) == week]
    groups: List[dict] = []
    for competition in (rules.PREMIER, rules.SECOND, *rules.CUP_IDS, rules.PLAYOFF):
        competition_fixtures = [fixture for fixture in fixtures if fixture["competition"] == competition]
        if not competition_fixtures:
            continue
        competition_fixtures.sort(key=lambda item: item.get("stage", ""))
        groups.append(
            {
                "competition": competition,
                "label": rules.competition_label(competition) if competition in rules.CUP_IDS else rules.display_name(competition),
                "matches": [fixture_dto(world, fixture) for fixture in competition_fixtures],
            }
        )
    goals = []
    for fixture in fixtures:
        home_name = team_short(world, fixture["home_team_id"])
        away_name = team_short(world, fixture["away_team_id"])
        for event in fixture.get("events", []):
            if event.get("type") == "goal":
                goals.append(
                    {
                        "minute": event.get("minute"),
                        "text": event.get("text", ""),
                        "match_id": fixture["match_id"],
                        "competition": fixture["competition"],
                        "score": f"{home_name} {score_text(fixture)} {away_name}",
                        "player_id": event.get("player_id"),
                    }
                )
    goals.sort(key=lambda item: (-int(item["minute"] or 0), item["match_id"]))
    return {
        "week": week,
        "season": world["season_number"],
        "groups": groups,
        "match_count": len(fixtures),
        "goals": goals[:12],
        "stories": [
            event
            for event in world.get("story_events", [])
            if int(event.get("season", 0)) == int(world["season_number"]) and int(event.get("week", 0)) == week
        ][:8],
        "empty": not fixtures,
        "slots": _week_slots(week, world["season_number"]),
    }


def multi_week_review(world: dict, end_week: int, count: int = 4) -> dict:
    """连续推进回顾：按周汇总真实赛果与故事，可逐周展开到对应周报。"""
    season_events = [
        event
        for event in world.get("story_events", [])
        if int(event.get("season", 0)) == int(world["season_number"])
    ]
    weeks = []
    for week in range(max(1, int(end_week) - count + 1), int(end_week) + 1):
        fixtures = [fixture for fixture in completed_matches(world) if int(fixture["week"]) == week]
        events = [event for event in season_events if int(event.get("week", 0)) == week]
        key = next((event for event in events if event.get("type") != "match_result"), None)
        headline = key["text"] if key else (events[-1]["text"] if events else "本周没有比赛。")
        weeks.append(
            {
                "week": week,
                "match_count": len(fixtures),
                "competitions": sorted({fixture["competition"] for fixture in fixtures}),
                "headline": headline,
                "match_id": (key or {}).get("match_id"),
                "empty": not fixtures,
            }
        )
    return {"weeks": weeks, "start_week": weeks[0]["week"], "end_week": weeks[-1]["week"]}


# ---------------------------------------------------------------------------
# 比赛
# ---------------------------------------------------------------------------


def matches_page(world: dict, save_name: str, *, competition: Optional[str] = None, team_id: Optional[str] = None, status: Optional[str] = None, week: Optional[int] = None, search: str = "") -> dict:
    fixtures = list(world["season"]["fixtures"])
    if competition:
        fixtures = [fixture for fixture in fixtures if fixture["competition"] == competition]
    if team_id:
        fixtures = [fixture for fixture in fixtures if team_id in (fixture["home_team_id"], fixture["away_team_id"])]
    if status == "completed":
        fixtures = [fixture for fixture in fixtures if fixture["status"] == "completed"]
    elif status == "scheduled":
        fixtures = [fixture for fixture in fixtures if fixture["status"] != "completed"]
    if week:
        fixtures = [fixture for fixture in fixtures if int(fixture["week"]) == int(week)]
    rows = [fixture_dto(world, fixture) for fixture in fixtures]
    if search:
        needle = search.strip().lower()
        rows = [
            row
            for row in rows
            if needle in row["home_label"].lower() or needle in row["away_label"].lower() or needle in row["competition_label"].lower()
        ]
    rows.sort(key=lambda row: (row["week"] if status != "scheduled" else -row["week"], row["match_id"]), reverse=False)
    if status == "scheduled":
        rows.sort(key=lambda row: (row["week"], row["match_id"]))
    return {
        "rows": rows,
        "total": len(rows),
        "teams": sorted(
            [{"id": team_id_value, "label": team_label(world, team_id_value)} for team_id_value in world["teams"]],
            key=lambda item: item["label"],
        ),
        "competitions": _competition_filter_options(),
        "weeks": list(range(1, 53)),
        "filters": {"competition": competition, "team_id": team_id, "status": status, "week": week, "search": search},
    }


def _competition_filter_options() -> List[dict]:
    options = [{"id": rules.PREMIER, "label": "一级联赛"}, {"id": rules.SECOND, "label": "次级联赛"}]
    options.extend({"id": cup_id, "label": rules.competition_label(cup_id)} for cup_id in rules.CUP_IDS)
    options.append({"id": rules.PLAYOFF, "label": "升级附加赛"})
    return options


def match_detail(world: dict, save_name: str, match_id: str) -> Optional[dict]:
    fixture = next((item for item in world["season"]["fixtures"] if item["match_id"] == match_id), None)
    archived = False
    if fixture is None:
        fixture = store.fetch_match(save_name, match_id)
        if fixture is None:
            return None
        archived = True
    if archived:
        fixture = dict(fixture)
        fixture["events"] = fixture.get("events_json") or []
        fixture["shootout"] = fixture.get("shootout_json") or []
    dto = fixture_dto(world, fixture)
    events = [event for event in (fixture.get("events") or []) if event.get("type") != "note"]
    events.sort(key=lambda event: (int(event.get("minute") or 0), event.get("period", "")))
    for event in events:
        if event.get("player_id"):
            event["player_name"] = player_name(world, event["player_id"])
        if event.get("assist_id"):
            event["assist_name"] = player_name(world, event["assist_id"])
    if archived:
        facts_rows = store.fetch_match_player_stats(save_name, match_id)
        player_facts = {row["player_id"]: row for row in facts_rows}
    else:
        player_facts = fixture.get("player_facts") or {}
    roster_rows = []
    for team_id in (fixture["home_team_id"], fixture["away_team_id"]):
        team_rows = []
        for player_id, fact in player_facts.items():
            if fact.get("team_id") != team_id:
                continue
            team_rows.append(
                {
                    "player_id": player_id,
                    "name": player_name(world, player_id),
                    "position": world["players"].get(player_id, {}).get("position", ""),
                    "goals": int(fact.get("goals", 0)),
                    "assists": int(fact.get("assists", 0)),
                    "chances_created": int(fact.get("chances_created", 0)),
                    "successful_defenses": int(fact.get("successful_defenses", 0)),
                    "successful_saves": int(fact.get("successful_saves", 0)),
                    "clean_sheets": int(fact.get("clean_sheets", 0)),
                    "appeared": bool(fact.get("appeared", 1)),
                }
            )
        team_rows.sort(key=lambda row: (row["position"], row["name"]))
        roster_rows.append(
            {
                "team_id": team_id,
                "team": team_label(world, team_id),
                "players": team_rows,
            }
        )
    tie = _tie_context(world, fixture, save_name)
    if tie:
        matched_leg = next((leg for leg in tie.get("legs", []) if leg.get("match_id") == dto["match_id"]), None)
        if matched_leg:
            dto["leg"] = matched_leg["leg"]
    shootout = fixture.get("shootout") or []
    for index, kick in enumerate(shootout, start=1):
        kick["number"] = index
        kick["player_name"] = player_name(world, kick["player_id"])
        kick["team"] = team_short(world, kick["team_id"])
    return {
        "fixture": dto,
        "archived": archived,
        "events": events,
        "roster_rows": roster_rows,
        "tie": tie,
        "shootout": shootout,
        "venue": {
            "stadium": stadium_name(fixture.get("stadium_id")),
            "mode": fixture.get("venue_mode"),
            "neutral_reason": fixture.get("neutral_reason"),
            "home_advantage_team_id": fixture.get("home_advantage_team_id"),
            "home_advantage_team": team_short(world, fixture.get("home_advantage_team_id")) if fixture.get("home_advantage_team_id") else None,
        },
        "story_events": [
            event
            for event in world.get("story_events", [])
            if event.get("match_id") == match_id
        ],
    }


TWO_LEG_STAGES = {
    "aureole": {"quarterfinal", "semifinal", "final"},
    "starpath": {"quarterfinal", "semifinal"},
    "playoff": {"promotion_semifinal", "promotion_final"},
}


def two_leg_stage_base(competition: str, stage: Optional[str]) -> Optional[str]:
    """只把已确认的淘汰赛阶段归为两回合；联赛/小组/瑞士/双败返回 None。"""
    allowed = TWO_LEG_STAGES.get(competition)
    if not allowed:
        return None
    base = str(stage or "").rsplit("_leg_", 1)[0]
    return base if base in allowed else None


def _leg_number(leg: dict, fallback: int) -> int:
    raw = leg.get("leg")
    if raw in (1, 2, "1", "2"):
        return int(raw)
    stage = str(leg.get("stage") or "")
    if stage.endswith("_leg_2"):
        return 2
    if stage.endswith("_leg_1"):
        return 1
    return fallback


def _tie_context(world: dict, fixture: dict, save_name: Optional[str] = None) -> Optional[dict]:
    if not two_leg_stage_base(str(fixture.get("competition") or ""), fixture.get("stage")):
        return None
    tie_id = fixture.get("tie_id")
    legs = []
    if tie_id:
        legs = sorted(
            [item for item in world["season"]["fixtures"] if item.get("tie_id") == tie_id],
            key=lambda item: int(item.get("leg", 1)),
        )
    elif save_name and fixture.get("season_number") and fixture.get("competition"):
        # 归档 matches 表没有 tie_id/leg；用同赛季、同赛事、同阶段和相同两队
        # 的真实已完成记录重建两回合上下文，禁止用当前赛季签表代替历史。
        season = int(fixture["season_number"])
        competition = fixture["competition"]
        stage = fixture.get("stage") or ""
        stage_base = stage.rsplit("_leg_", 1)[0]
        teams = {fixture.get("home_team_id"), fixture.get("away_team_id")}
        rows = [
            row
            for row in store.fetch_matches(save_name, season=season, competition=competition)
            if two_leg_stage_base(competition, row.get("stage")) == stage_base
            and {row.get("home_team_id"), row.get("away_team_id")} == teams
        ]
        legs = sorted(rows, key=lambda item: int(item.get("week") or 0))
    if len(legs) < 2:
        return None
    totals = {legs[0]["home_team_id"]: 0, legs[0]["away_team_id"]: 0}
    for leg in legs:
        totals[leg["home_team_id"]] = totals.get(leg["home_team_id"], 0) + int(leg.get("home_goals") or 0) + int(leg.get("home_goals_et") or 0)
        totals[leg["away_team_id"]] = totals.get(leg["away_team_id"], 0) + int(leg.get("away_goals") or 0) + int(leg.get("away_goals_et") or 0)
    order = [fixture.get("home_team_id"), fixture.get("away_team_id")]
    order = [team_id for index, team_id in enumerate(order) if team_id and team_id not in order[:index]]
    order += [team_id for team_id in totals if team_id not in order]
    leg_dtos = []
    for index, leg in enumerate(legs):
        dto = fixture_dto(world, leg)
        dto["leg"] = _leg_number(leg, index + 1)
        leg_dtos.append(dto)
    leg_dtos.sort(key=lambda item: item["leg"])
    return {
        "tie_id": tie_id or f"archive-tie:{legs[0].get('match_id')}",
        "legs": leg_dtos,
        "totals": [
            {"team_id": team_id, "team": team_short(world, team_id), "goals": totals.get(team_id, 0)}
            for team_id in order
        ],
        "aggregate_text": " – ".join(f"{team_short(world, team_id)} {totals.get(team_id, 0)}" for team_id in order),
        "completed_legs": sum(1 for leg in legs if leg.get("status") == "completed"),
        "no_away_goals": True,
        "archived": tie_id is None,
    }


# ---------------------------------------------------------------------------
# 赛事
# ---------------------------------------------------------------------------


def competitions_page(world: dict) -> dict:
    tables = game_world.league_tables(world)
    entries = []
    season = world["season"]
    from football_simulator.game import editions
    def archived(comp):
        return editions.key(world["season_number"], comp) in world.get("edition_archives", {})
    for key, competition in (("premier", rules.PREMIER), ("second", rules.SECOND)):
        rows = tables[key]
        completed = len([match for match in completed_matches(world, competition)])
        leader = rows[0] if rows else None
        entries.append(
            {
                "id": competition,
                "label": rules.display_name(competition),
                "type": "league",
                "active": True,
                "status": "已归档" if season.get("finalized") or archived(competition) else "进行中",
                "detail": f"第 {season['week']} 周 · {completed} 场已完赛",
                "leader": team_short(world, leader["team_id"]) if leader else "—",
                "champion": team_short(world, rows[0]["team_id"]) if season.get("finalized") and rows else None,
                "played": completed,
                "total": 380,
            }
        )
    for cup_id in rules.CUP_IDS:
        meta = rules.CUP_META[cup_id]
        state = season.get("cups", {}).get(cup_id)
        active = state is not None
        matches = [fixture for fixture in season["fixtures"] if fixture["competition"] == cup_id]
        completed = len([fixture for fixture in matches if fixture["status"] == "completed"])
        if active:
            if state.get("champion"):
                status = "已归档" if archived(cup_id) else "已结束"
                detail = f"冠军：{team_short(world, state['champion'])}"
            else:
                current_stage = _current_cup_stage(world, cup_id)
                status = "进行中"
                detail = f"{current_stage} · {completed}/{meta['match_count']} 场"
            champion = state.get("champion")
            entrants = len(state.get("participants", []))
        else:
            status = "未开放"
            detail = f"第 {meta['start_season']} 赛季起举办"
            champion = None
            entrants = meta["entrants"]
            completed = 0
        entries.append(
            {
                "id": cup_id,
                "label": rules.competition_label(cup_id),
                "type": "cup",
                "active": active,
                "status": status,
                "detail": detail,
                "leader": team_short(world, champion) if champion else "—",
                "champion": team_short(world, champion) if champion else None,
                "played": completed,
                "total": meta["match_count"],
                "entrants": entrants,
            }
        )
    return {"entries": entries, "season": world["season_number"], "week": season["week"]}


def _current_cup_stage(world: dict, cup_id: str) -> str:
    season = world["season"]
    fixtures = [fixture for fixture in season["fixtures"] if fixture["competition"] == cup_id]
    pending = [fixture for fixture in fixtures if fixture["status"] != "completed"]
    if pending:
        pending.sort(key=lambda fixture: (int(fixture["week"]), fixture["match_id"]))
        return stage_text(cup_id, pending[0]["stage"])
    completed = [fixture for fixture in fixtures if fixture["status"] == "completed"]
    if completed:
        completed.sort(key=lambda fixture: int(fixture["week"]))
        return stage_text(cup_id, completed[-1]["stage"])
    return "等待抽签"


def competition_detail(world: dict, save_name: str, competition: str, *, live_standings: bool = False) -> dict:
    if competition in (rules.PREMIER, rules.SECOND):
        return _league_detail(world, competition)
    if competition in rules.CUP_IDS:
        return _cup_detail(world, competition, live_standings=live_standings)
    if competition == rules.PLAYOFF and live_standings:
        state = world["season"].get("promotion_playoff", {})
        matches = sorted((m for m in world["season"]["fixtures"] if m["competition"] == competition), key=lambda m: (m["week"], m["match_id"]))
        return {"kind": "playoff", "id": competition, "label": rules.display_name(competition), "season": world["season_number"],
                "active": bool(state.get("semifinals")), "winner": team_short(world, state["winner"]) if state.get("winner") else None,
                "rounds": _tie_rounds(world, [("半决赛", state.get("semifinals", [])), ("决赛", [state["final"]] if state.get("final") else [])]),
                "fixtures": [fixture_dto(world, m) for m in matches]}
    return {"kind": "unknown", "id": competition, "label": competition}


def competition_players(world: dict, competition: str) -> List[dict]:
    """Real-player current-season production, merged across recorded clubs."""
    matches = completed_matches(world, competition)
    totals = game_world._aggregate_player_facts(world, matches)
    return [{"player_id": pid, "player": player_name(world, pid), "team_id": data.get("team_id"),
             "team": team_short(world, data.get("team_id")), "appearances": stats["appearances"], "goals": stats["goals"], "assists": stats["assists"]}
            for pid, stats in totals.items() if (data := world["players"].get(pid, {})).get("is_real") and stats["appearances"] > 0]


def _league_detail(world: dict, competition: str) -> dict:
    key = "premier" if competition == rules.PREMIER else "second"
    tables = world["season"].get("final_tables") or game_world.league_tables(world)
    rows = tables[key] if isinstance(tables, dict) and key in tables else game_world.league_tables(world)[key]
    team_rows = [
        {
            "rank": index + 1,
            "team_id": row["team_id"],
            "team": team_short(world, row["team_id"]),
            "club": (world["teams"].get(row["team_id"]) or {}).get("club_en", ""),
            "played": row["played"],
            "wins": row["wins"],
            "draws": row["draws"],
            "losses": row["losses"],
            "goals_for": row["goals_for"],
            "goals_against": row["goals_against"],
            "goal_diff": row["goal_diff"],
            "points": row["points"],
        }
        for index, row in enumerate(rows)
    ]
    fixtures = [fixture for fixture in world["season"]["fixtures"] if fixture["competition"] == competition]
    by_round = {}
    for fixture in fixtures:
        by_round.setdefault(int(fixture.get("round", 0)), []).append(fixture_dto(world, fixture))
    rounds = [
        {"round": round_number, "matches": sorted(matches, key=lambda item: item["match_id"])}
        for round_number, matches in sorted(by_round.items())
    ]
    return {
        "kind": "league",
        "id": competition,
        "label": rules.display_name(competition),
        "season": world["season_number"],
        "table": team_rows,
        "rounds": rounds,
        "champion": team_short(world, rows[0]["team_id"]) if world["season"].get("finalized") and rows else None,
        "awards": _awards_for(world, competition),
        "description": "20 队双循环，38 轮；积分→净胜球→进球→相互战绩→上季排名。" if competition == rules.PREMIER else f"20 队双循环，38 轮；所有名次 0 荣誉分，冠军保留 {rules.competition_label('zenith')} 资格。",
    }


def swiss_team_status(team_id: str, state: dict) -> tuple[str, str, str]:
    """返回（状态文字、语义 kind、战绩标签）；资格与最终种子严格分开。"""
    records = state.get("records", {})
    wins, losses = records.get(team_id, [0, 0])
    seeds = state.get("knockout_seeds") or {}
    if team_id in seeds:
        return f"已晋级 · 种子 {seeds[team_id]}", "qualified", f"{wins}–{losses}"
    if team_id in state.get("qualified", []) or wins >= 3:
        return "已晋级 · 种子待定", "qualified", f"{wins}–{losses}"
    if team_id in state.get("eliminated", []) or losses >= 3:
        return "已淘汰", "eliminated", f"{wins}–{losses}"
    return "争夺资格中", "active", f"{wins}–{losses}"


def _cup_detail(world: dict, competition: str, *, live_standings: bool = False) -> dict:
    meta = rules.CUP_META[competition]
    state = world["season"].get("cups", {}).get(competition)
    base = {
        "kind": "cup",
        "id": competition,
        "label": rules.competition_label(competition),
        "format": meta["format"],
        "entrants": meta["entrants"],
        "match_count": meta["match_count"],
        "season": world["season_number"],
        "active": state is not None,
        "champion": None,
        "runner_up": None,
        "placement_rows": [],
        "current_stage": "未开放",
        "completed_matches": 0,
        "next_match": None,
    }
    if state is None:
        base["inactive_reason"] = f"{rules.display_name(competition)} 从第 {meta['start_season']} 赛季开始举办。"
        base["start_season"] = meta["start_season"]
        return base
    base.update(
        {
            "champion": team_short(world, state.get("champion")) if state.get("champion") else None,
            "champion_id": state.get("champion"),
            "runner_up": team_short(world, state.get("runner_up")) if state.get("runner_up") else None,
            "runner_up_id": state.get("runner_up"),
            "participants": [{"team_id": tid, "team": team_short(world, tid)} for tid in state.get("participants", [])],
            "placement_rows": _placement_rows(world, state, competition),
            "neutral_venue": None,
        }
    )
    cup_fixtures = [fixture for fixture in world["season"]["fixtures"] if fixture["competition"] == competition]
    pending_fixtures = sorted(
        [fixture for fixture in cup_fixtures if fixture["status"] != "completed"],
        key=lambda fixture: (int(fixture["week"]), fixture["match_id"]),
    )
    base["current_stage"] = _current_cup_stage(world, competition)
    base["completed_matches"] = len([fixture for fixture in cup_fixtures if fixture["status"] == "completed"])
    base["next_match"] = fixture_dto(world, pending_fixtures[0]) if pending_fixtures else None
    venue_id = world["season"].get("neutral_venues", {}).get(competition)
    if venue_id:
        base["neutral_venue"] = stadium_name(venue_id)
    if competition == "aureole":
        base["groups"] = []
        for letter in state.get("groups", {}):
            table = _standings_dto(world, _cup_group_rows(world, competition, state, letter, live_standings))
            for row in table:
                row["advance"] = "前二晋级区" if row["rank"] <= 2 else "—"
                if live_standings:
                    finished = state.get("stage_done", {}).get("groups")
                    row["advance"] = ("已晋级八强" if finished else "暂列前二") if row["rank"] <= 2 else ("已淘汰" if finished else "—")
            base["groups"].append({"name": letter, "table": table})
        base["knockout"] = _tie_rounds(
            world,
            [("八强", state.get("quarterfinals", [])), ("半决赛", state.get("semifinals", [])), ("决赛", [state["final"]] if state.get("final") else [])],
        )
    elif competition == "confluence":
        base["knockout"] = _tie_rounds(
            world,
            [(stage_text(competition, item["stage"]), item["ties"]) for item in state.get("rounds", [])],
        )
    elif competition == "starpath":
        fixture_index = {fixture["match_id"]: fixture for fixture in world["season"]["fixtures"]}
        rows = []
        for team_id in state.get("participants", state.get("records", {})):
            wins, losses = state.get("records", {}).get(team_id, [0, 0])
            status, status_kind, record = swiss_team_status(team_id, state)
            rows.append(
                {
                    "team_id": team_id,
                    "team": team_short(world, team_id),
                    "wins": wins,
                    "losses": losses,
                    "record": record,
                    "bu": state.get("bu", {}).get(team_id, 0),
                    "seed": (state.get("knockout_seeds") or {}).get(team_id),
                    "status": status,
                    "status_kind": status_kind,
                }
            )
        if live_standings:
            from football_simulator.game.competitions import previous_ranks
            rank = previous_ranks(world)
            rows.sort(key=lambda row: (-row["wins"], row["losses"], -(row["bu"] or 0), rank.get(row["team_id"], 999), row["team_id"]))
        else:
            rows.sort(key=lambda row: (-row["wins"], row["losses"], -(row["bu"] or 0), row["team"]))
        base["swiss_rows"] = rows
        base["swiss_rounds"] = []
        for item in state.get("rounds", []):
            pairings = []
            for index, pair in enumerate(item.get("pairings", [])):
                fixture = fixture_index.get(item["matches"][index] if index < len(item["matches"]) else None)
                pairings.append(
                    {
                        "home": team_short(world, pair[0]),
                        "away": team_short(world, pair[1]),
                        "match_id": fixture["match_id"] if fixture else None,
                        "score": score_text(fixture) if fixture and fixture.get("status") == "completed" else "未赛",
                        "status": fixture_status(fixture) if fixture else "未赛",
                        "winner": team_short(world, fixture.get("winner_team_id")) if fixture and fixture.get("status") == "completed" else "",
                    }
                )
            base["swiss_rounds"].append(
                {
                    "round": item["round"],
                    "week": item["week"],
                    "pairings": pairings,
                    "rematch_reason": item.get("rematch_reason"),
                }
            )
        base["knockout"] = _tie_rounds(
            world,
            [("八强", state.get("quarterfinals", [])), ("半决赛", state.get("semifinals", [])), ("决赛", [state["final"]] if state.get("final") else [])],
        )
    elif competition == "ember":
        sections = [
            ("胜者组首轮", state.get("wb_quarterfinal", [])),
            ("胜者组次轮", state.get("wb_semifinal", [])),
            ("败者组首轮", state.get("lb_round_1", [])),
            ("败者组次轮", state.get("lb_round_2", [])),
            ("胜者组决赛", [state["wb_final"]] if state.get("wb_final") else []),
            ("败者组第三轮", state.get("lb_round_3", [])),
            ("败者组决赛", [state["lb_final"]] if state.get("lb_final") else []),
            ("总决赛", [state["grand_final"]] if state.get("grand_final") else []),
        ]
        rounds = []
        for label, ties in sections:
            if not ties:
                continue
            rounds.append({"label": label, "ties": [_tie_dto(world, tie) for tie in ties]})
        base["bracket_rounds"] = rounds
        base["loss_paths"] = [
            {"team_id": team_id, "team": team_short(world, team_id), "loss_stage": stage}
            for team_id, stage in state.get("loss_stage", {}).items()
        ]
    elif competition == "ascendant":
        base["groups"] = []
        for group_name, group_key, direct_count, playin_count in (
            ("登峰组", "D", 4, 6),
            ("涅槃组", "N", 1, 5),
        ):
            table = _standings_dto(world, _cup_group_rows(world, competition, state, group_key, live_standings))
            for row in table:
                if row["rank"] <= direct_count:
                    row["advance"] = "直通八强"
                elif row["rank"] <= playin_count:
                    row["advance"] = "进入突围赛"
                else:
                    row["advance"] = "淘汰"
                if live_standings and not state.get("stage_done", {}).get("groups"):
                    row["advance"] = "暂列直通区" if row["rank"] <= direct_count else "暂列突围区" if row["rank"] <= playin_count else "暂列淘汰区"
            base["groups"].append({"name": group_name, "table": table})
        base["playin"] = [_tie_dto(world, tie) for tie in state.get("playin", [])]
        base["direct"] = [{"team_id": tid, "team": team_short(world, tid)} for tid in state.get("direct", [])]
        base["knockout"] = _tie_rounds(
            world,
            [("八强", state.get("quarterfinals", [])), ("半决赛", state.get("semifinals", [])), ("决赛", [state["final"]] if state.get("final") else [])],
        )
    elif competition == "zenith":
        sources = state.get("sources", {})
        base["seed_rows"] = [
            {
                "seed": seed,
                "team_id": team_id,
                "team": team_short(world, team_id),
                "sources": sources.get(team_id, []),
            }
            for team_id, seed in sorted(state.get("seeds", {}).items(), key=lambda item: item[1])
        ]
        base["knockout"] = _tie_rounds(
            world,
            [("八强", state.get("quarterfinals", [])), ("半决赛", state.get("semifinals", [])), ("决赛", [state["final"]] if state.get("final") else [])],
        )
    base["awards"] = _awards_for(world, competition)
    base["fixtures"] = [
        fixture_dto(world, fixture)
        for fixture in world["season"]["fixtures"]
        if fixture["competition"] == competition
    ]
    base["fixtures"].sort(key=lambda row: (row["week"], row["match_id"]))
    base["bracket_note"] = _bracket_note(competition)
    return base


def _cup_group_rows(world: dict, competition: str, state: dict, group: str, live: bool) -> List[dict]:
    """Read-only live table; frozen qualification tables remain authoritative."""
    cache_key = "group_standings" if competition == "aureole" else "standings"
    frozen = state.get(cache_key, {}).get(group, [])
    if not live or (state.get("stage_done", {}).get("groups") and frozen):
        return frozen
    from football_simulator.game.competitions import previous_ranks

    prefix = "group_" if competition == "aureole" else "cross_group_"
    matches = [match for match in completed_matches(world, competition) if match.get("stage", "").startswith(prefix)]
    return standings_from_matches(state["groups"][group], matches, previous_rank=previous_ranks(world), partial=competition == "ascendant")


def _bracket_note(competition: str) -> str:
    return {
        "aureole": "四档抽签分四组；小组前二进入两回合八强；八强抽签回避同组并固定半区；决赛两回合。",
        "confluence": "32 队单场淘汰，每轮无种子公开重抽；非决赛上季联赛排名较高者主场；决赛中立场。",
        "starpath": "三胜晋级/三负淘汰；BU=对手胜场减负场之和；八强按 3–0/3–1/3–2 分档，固定 1–8/4–5/2–7/3–6。",
        "ember": "8 队完整双败签表；胜者组冠军进入总决赛且无重置；主场按无败→更晚落入败者组→上季排名。",
        "ascendant": "登峰组 1–6、涅槃组 7–12；组外循环 3 主 3 客；5 队直通＋3 队突围；八强保护登峰前二半区。",
        "zenith": "五杯冠军＋两级冠军＋卫冕去重，按上季一级排名补足 8；按上季总荣誉分排种子，固定高低位签表。",
    }.get(competition, "")


def _standings_dto(world: dict, rows: Sequence[dict]) -> List[dict]:
    return [
        {
            "rank": index + 1,
            "team_id": row["team_id"],
            "team": team_short(world, row["team_id"]),
            "played": row["played"],
            "wins": row["wins"],
            "draws": row["draws"],
            "losses": row["losses"],
            "goals_for": row["goals_for"],
            "goals_against": row["goals_against"],
            "goal_diff": row["goal_diff"],
            "points": row["points"],
        }
        for index, row in enumerate(rows)
    ]


def _tie_dto(world: dict, tie: dict) -> dict:
    legs = tie.get("legs")
    if legs:
        fixtures = [fixture_dto(world, next(item for item in world["season"]["fixtures"] if item["match_id"] == match_id)) for match_id in legs]
        return {
            "tie_id": tie["tie_id"],
            "teams": [team_short(world, team_id) for team_id in tie["teams"]],
            "team_ids": tie["teams"],
            "legs": fixtures,
            "aggregate": tie.get("aggregate"),
            "aggregate_text": (
                f"{team_short(world, tie['teams'][0])} {tie['aggregate'][0]}–{tie['aggregate'][1]} {team_short(world, tie['teams'][1])}"
                if tie.get("aggregate")
                else "待定"
            ),
            "winner": team_short(world, tie.get("winner")),
            "winner_id": tie.get("winner"),
            "two_leg": True,
            "reason": None,
        }
    fixture = next((item for item in world["season"]["fixtures"] if item["match_id"] == tie.get("match_id")), None)
    return {
        "tie_id": tie.get("tie_id"),
        "teams": [team_short(world, team_id) for team_id in tie.get("teams", [])],
        "team_ids": tie.get("teams", []),
        "match": fixture_dto(world, fixture) if fixture else None,
        "winner": team_short(world, tie.get("winner")),
        "winner_id": tie.get("winner"),
        "two_leg": False,
        "reason": tie.get("reason") or (fixture.get("venue_reason") if fixture else None),
    }


def _tie_rounds(world: dict, rounds: Sequence[Tuple[str, Sequence[dict]]]) -> List[dict]:
    return [
        {"label": label, "ties": [_tie_dto(world, tie) for tie in ties]}
        for label, ties in rounds
        if ties
    ]


TIER_LABELS = {
    "champion": "冠军",
    "runner_up": "亚军",
    "semifinal": "四强",
    "quarterfinal": "八强",
    "group_third": "小组第3",
    "group_fourth": "小组第4",
    "round_16": "16强",
    "round_32": "32强",
    "swiss_2_3": "瑞士轮2胜3负",
    "swiss_1_3": "瑞士轮1胜3负",
    "swiss_0_3": "瑞士轮0胜3负",
    "third": "季军",
    "fourth": "第4名",
    "fifth_sixth": "第5–6名",
    "seventh_eighth": "第7–8名",
    "play_in_exit": "突围赛出局",
    "nirvana_sixth": "涅槃组第6",
}


def tier_label(tier: str) -> str:
    return TIER_LABELS.get(tier, "未分类")


def _placement_rows(world: dict, state: dict, competition: str) -> List[dict]:
    rows = []
    for team_id, rank in sorted(state.get("placements", {}).items(), key=lambda item: item[1]):
        tier = rules.honor_key_for_rank(competition, rank)
        rows.append(
            {
                "rank": rank,
                "team_id": team_id,
                "team": team_short(world, team_id),
                "tier": tier,
                "tier_label": tier_label(tier),
                "points": rules.honor_points_for(competition, rank, world.get("honor_scoring_profile")),
            }
        )
    return rows


def _awards_for(world: dict, competition: str) -> dict:
    awards = (world["season"].get("awards") or {}).get("competitions", {}).get(competition, {})
    return {
        "awarded": bool(awards),
        "rule_version": awards.get("rule_version"),
        "evp_slots": awards.get("evp_slots"),
        "top_scorer": [_award_people(item) for item in awards.get("top_scorer", [])],
        "assist_leader": [_award_people(item) for item in awards.get("assist_leader", [])],
        "mvp": [_award_people(item) for item in awards.get("mvp", [])],
        "evp": [_award_people(item) for item in awards.get("evp", [])],
        "mvp_minimum": awards.get("mvp_minimum"),
        "max_team_matches": awards.get("max_team_matches"),
    }


def _award_people(item: dict) -> dict:
    """单条已存储奖项记录 → DTO；空/共享奖项由调用方列表决定。"""
    return {
        "player_id": item.get("player_id"),
        "label": item.get("label"),
        "team_id": item.get("team_id"),
        "team": item.get("team_name"),
        "teams": dict(item.get("teams") or {}),
        "value": item.get("value"),
        "appearances": item.get("appearances"),
    }


# ---------------------------------------------------------------------------
# 赛季页
# ---------------------------------------------------------------------------


def season_overview(world: dict) -> dict:
    season = world["season"]
    current_week = int(season["week"])
    weeks = []
    for slot in rules.calendar_weeks():
        week = int(slot["week"])
        cup_slots = [
            cup for cup in rules.week_cup_slots(slot)
            if rules.cup_active(cup["competition"], world["season_number"])
        ]
        label_parts = []
        if slot.get("league_round"):
            label_parts.append(f"联赛第 {slot['league_round']} 轮")
        for cup in cup_slots:
            label_parts.append(f"{rules.display_name(cup['competition'])} · {stage_text(cup['competition'], cup['stage'])}")
        if slot.get("break_type") == "winter":
            label_parts = ["冬窗休赛"]
        elif slot.get("break_type") == "summer":
            label_parts = ["夏窗休赛"]
        if slot.get("promotion_stage"):
            label_parts.append(stage_text(rules.PLAYOFF, slot["promotion_stage"]))
        status = "已完赛" if week < current_week else "当前" if week == current_week else "未开始"
        if season.get("season_complete"):
            status = "已完赛"
        weeks.append(
            {
                "week": week,
                "league_round": slot.get("league_round"),
                "cup_slots": cup_slots,
                "cup_label": (
                    " / ".join(
                        f"{rules.competition_label(cup['competition'])} · {stage_text(cup['competition'], cup['stage'])}"
                        for cup in cup_slots
                    ) or None
                ),
                "promotion_stage": slot.get("promotion_stage"),
                "break_type": slot.get("break_type"),
                "label": "；".join(label_parts) or "无比赛",
                "status": status,
            }
        )
    fixtures = world["season"]["fixtures"]
    return {
        "season": world["season_number"],
        "week": current_week,
        "weeks": weeks,
        "counts": {
            "league": len([fixture for fixture in fixtures if fixture["competition"] in (rules.PREMIER, rules.SECOND)]),
            "cups": len([fixture for fixture in fixtures if fixture["competition"] in rules.CUP_IDS]),
            "playoff": len([fixture for fixture in fixtures if fixture["competition"] == rules.PLAYOFF]),
            "scheduled": len([fixture for fixture in fixtures if fixture["status"] != "completed"]),
            "completed": len([fixture for fixture in fixtures if fixture["status"] == "completed"]),
        },
        "pending": season["pending"],
        "transition": season.get("transition") or {},
        "champions": _champion_rows(world),
        "story_events": [event for event in world.get("story_events", []) if int(event.get("season", 0)) == int(world["season_number"])][-8:][::-1],
    }


def _week_slots(week: int, season_number: Optional[int] = None) -> dict:
    try:
        slot = dict(rules.week_slot(week))
    except KeyError:
        return {}
    cups = rules.week_cup_slots(slot)
    if season_number is not None:
        cups = [cup for cup in cups if rules.cup_active(cup["competition"], season_number)]
    for cup in cups:
        cup.setdefault("label", f"{rules.competition_label(cup['competition'])} · {stage_text(cup['competition'], cup['stage'])}")
    slot["cup_slots"] = cups
    slot.pop("cup", None)
    return slot


def _champion_rows(world: dict) -> List[dict]:
    rows = []
    captured = {
        row["competition_id"]: row
        for row in (world["season"].get("championships") or {}).values()
    }
    for competition in (rules.PREMIER, rules.SECOND, *rules.CUP_IDS):
        record = captured.get(competition)
        if competition in (rules.PREMIER, rules.SECOND) and not record:
            continue
        champion = record.get("champion_team_id") if record else None
        if competition in rules.CUP_IDS and not champion:
            state = world["season"].get("cups", {}).get(competition) or {}
            champion = state.get("champion")
        rows.append(
            {
                "competition": competition,
                "label": rules.competition_label(competition),
                "champion": team_short(world, champion) if champion else "待定",
                "champion_id": champion,
                "decided_week": record.get("decided_week") if record else None,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 球队
# ---------------------------------------------------------------------------


def teams_page(world: dict, save_name: str) -> dict:
    tables = game_world.league_tables(world)
    ranks = {}
    for rows in tables.values():
        for index, row in enumerate(rows, start=1):
            ranks[row["team_id"]] = index
    honor_totals = dict(world["season"].get("honor_totals") or {})
    rows = []
    for team_id, team in world["teams"].items():
        table = next((row for row in tables["premier"] + tables["second"] if row["team_id"] == team_id), None)
        rows.append(
            {
                "team_id": team_id,
                "team": team_short(world, team_id),
                "club": team["club_en"],
                "search_text": f"{team['club_en']} {team.get('name_zh', '')}",
                "division": team["division"],
                "rank": ranks.get(team_id),
                "played": table["played"] if table else 0,
                "points": table["points"] if table else 0,
                "goal_diff": table["goal_diff"] if table else 0,
                "honor_points": honor_totals.get(team_id, 0),
                "stadium": stadium_name(team["stadium_id"], with_club=False),
                "real_count": sum(1 for entry in team["roster"] if world["players"][entry["player_id"]].get("is_real")),
            }
        )
    rows.sort(key=lambda row: (0 if row["division"] == "一级联赛" else 1, row["rank"] or 99, row["team"]))
    return {"rows": rows, "season": world["season_number"]}


def team_profile(world: dict, save_name: str, team_id: str) -> Optional[dict]:
    team = world["teams"].get(team_id)
    if not team:
        return None
    tables = world["season"].get("final_tables") or game_world.league_tables(world)
    table_rows = tables["premier" if team["division"] == "一级联赛" else "second"]
    table = next((row for row in table_rows if row["team_id"] == team_id), None)
    rank = table_rows.index(table) + 1 if table else None
    totals = _season_totals(world)
    roster = []
    for entry in team["roster"]:
        player_id = entry["player_id"]
        data = world["players"][player_id]
        stats = totals.get(player_id, {})
        roster.append(
            {
                "player_id": player_id,
                "name": player_name(world, player_id),
                "position": data["position"],
                "ability": data["ability"],
                "is_real": bool(data.get("is_real")),
                "appearances": stats.get("appearances", 0),
                "goals": stats.get("goals", 0),
                "assists": stats.get("assists", 0),
                "rating": _player_rating(world, player_id, stats),
                "market_value": data.get("market_value") or data.get("initial_market_value"),
            }
        )
    fixtures = [fixture for fixture in world["season"]["fixtures"] if team_id in (fixture["home_team_id"], fixture["away_team_id"])]
    recent = [fixture_dto(world, fixture) for fixture in fixtures if fixture["status"] == "completed"][-8:][::-1]
    upcoming = [fixture_dto(world, fixture) for fixture in fixtures if fixture["status"] != "completed"][:8]
    honors = [row for row in world["season"].get("honor_rows", []) if row["team_id"] == team_id]
    archived_honors = []
    for archive in world.get("archives", []):
        for row in archive.get("honor_rows", []):
            if row["team_id"] == team_id:
                archived_honors.append({**row, "season": archive["season_number"]})
    transfers = [
        row
        for row in world.get("transfer_history", [])
        if team_id in (row["team_a"], row["team_b"])
    ][-8:][::-1]
    return {
        "team_id": team_id,
        "team": team_short(world, team_id),
        "club": team["club_en"],
        "division": team["division"],
        "rank": rank,
        "table": table,
        "stadium": stadium_name(team["stadium_id"]),
        "roster": roster,
        "recent": recent,
        "upcoming": upcoming,
        "honors": honors,
        "archived_honors": archived_honors,
        "honor_total": int((world["season"].get("honor_totals") or {}).get(team_id, 0)),
        "transfers": transfers,
        "watch": team_id in world.get("settings", {}).get("watchlist", []),
    }


# ---------------------------------------------------------------------------
# 球员
# ---------------------------------------------------------------------------


def players_page(world: dict, save_name: str, *, position: Optional[str] = None, team_id: Optional[str] = None, search: str = "", include_default: bool = True) -> dict:
    totals = _season_totals(world)
    settlements = (world["season"].get("settlements", {}).get("final") or {}).get("players", {})
    rows = []
    for player_id, data in world["players"].items():
        current_team = data.get("team_id")
        if current_team is None:
            continue
        if not include_default and not data.get("is_real"):
            continue
        if position and data["position"] != position:
            continue
        if team_id and current_team != team_id:
            continue
        stats = totals.get(player_id, {})
        label = player_name(world, player_id)
        if search and search.strip().lower() not in label.lower():
            continue
        settlement = settlements.get(player_id, {})
        rows.append(
            {
                "player_id": player_id,
                "name": label,
                "team_id": current_team,
                "team": team_short(world, current_team),
                "club": (world["teams"].get(current_team) or {}).get("club_en", ""),
                "position": data["position"],
                "ability": data["ability"],
                "is_real": bool(data.get("is_real")),
                "appearances": stats.get("appearances", 0),
                "goals": stats.get("goals", 0),
                "assists": stats.get("assists", 0),
                "rating": settlement.get("rating") or _player_rating(world, player_id, stats),
                "market_value": data.get("market_value") or data.get("initial_market_value"),
                "frozen": bool(settlement),
            }
        )
    rows.sort(key=lambda row: (-row["goals"], -row["assists"], -row["ability"], row["name"]))
    return {
        "rows": rows,
        "teams": sorted(
            [{"id": team_id_value, "label": team_label(world, team_id_value)} for team_id_value in world["teams"]],
            key=lambda item: item["label"],
        ),
        "filters": {"position": position, "team_id": team_id, "search": search, "include_default": include_default},
    }


def player_profile(world: dict, save_name: str, player_id: str) -> Optional[dict]:
    data = world["players"].get(player_id)
    if not data:
        return None
    team_id = data.get("team_id")
    totals = _season_totals(world)
    stats = totals.get(player_id, {})
    settlements = world["season"].get("settlements", {})
    final_settlement = (settlements.get("final") or {}).get("players", {}).get(player_id)
    winter_settlement = (settlements.get("winter") or {}).get("players", {}).get(player_id)
    competitions = _player_competition_splits(world, player_id)
    team_splits = _player_team_splits(world, player_id)
    match_log = _player_match_log(world, player_id, limit=20)
    career = _player_career(save_name, player_id)
    awards = _player_awards(world, player_id)
    transfers = [
        row for row in world.get("transfer_history", []) if player_id in (row.get("players_a", []) + row.get("players_b", []))
    ][-8:][::-1]
    return {
        "player_id": player_id,
        "name": player_name(world, player_id),
        "position": data["position"],
        "ability": data["ability"],
        "is_real": bool(data.get("is_real")),
        "team_id": team_id,
        "team": team_short(world, team_id) if team_id else "已离队（历史身份保留）",
        "slot_number": data.get("slot_number"),
        "initial_market_value": data.get("initial_market_value"),
        "market_value": data.get("market_value") or data.get("initial_market_value"),
        "season": world["season_number"],
        "stats": stats,
        "rating": (final_settlement or {}).get("rating") or _player_rating(world, player_id, stats),
        "winter_settlement": winter_settlement,
        "final_settlement": final_settlement,
        "competition_splits": competitions,
        "team_splits": team_splits,
        "match_log": match_log,
        "career": career,
        "awards": awards,
        "transfers": transfers,
        "watch": player_id in world.get("settings", {}).get("watchlist", []),
        "freeze_note": "冬季/年度结算评分与身价一经产生即冻结；之后能力调整不重算旧值。",
    }


def _player_rating(world: dict, player_id: str, stats: dict) -> Optional[float]:
    if not stats or stats.get("appearances", 0) <= 0:
        return None
    player = game_world._player_object(world, player_id)
    formulas = __import__("football_simulator.domain.formulas", fromlist=["calculate_player_rating"])
    rating = formulas.calculate_player_rating_v031 if world.get("awards_rule_version") == "0.3.1" else formulas.calculate_player_rating
    return rating(
        game_world._stats_namespace(player, stats), stats["appearances"]
    )


def _player_competition_splits(world: dict, player_id: str) -> List[dict]:
    rows = []
    for competition in (rules.PREMIER, rules.SECOND, *rules.CUP_IDS, rules.PLAYOFF):
        matches = [match for match in completed_matches(world) if match["competition"] == competition]
        totals = game_world._aggregate_player_facts(world, matches).get(player_id)
        if not totals:
            continue
        rows.append(
            {
                "competition": competition,
                "label": rules.competition_label(competition) if competition in rules.CUP_IDS else rules.display_name(competition),
                "appearances": totals["appearances"],
                "goals": totals["goals"],
                "assists": totals["assists"],
                "rating": _player_rating(world, player_id, totals),
            }
        )
    return rows


def _player_team_splits(world: dict, player_id: str) -> List[dict]:
    totals = _season_totals(world).get(player_id)
    if not totals:
        return []
    return [
        {"team_id": team_id, "team": team_short(world, team_id), "appearances": count}
        for team_id, count in totals.get("teams", {}).items()
    ]


def _player_match_log(world: dict, player_id: str, limit: int = 20) -> List[dict]:
    rows = []
    for fixture in completed_matches(world):
        fact = (fixture.get("player_facts") or {}).get(player_id)
        if not fact or not fact.get("appeared"):
            continue
        home = team_short(world, fixture["home_team_id"])
        away = team_short(world, fixture["away_team_id"])
        rows.append(
            {
                "match_id": fixture["match_id"],
                "week": fixture["week"],
                "competition": fixture["competition"],
                "label": rules.competition_label(fixture["competition"]) if fixture["competition"] in rules.CUP_IDS else rules.display_name(fixture["competition"]),
                "stage": stage_text(fixture["competition"], fixture.get("stage", "")),
                "team_id": fact["team_id"],
                "team": team_short(world, fact["team_id"]),
                "opponent": team_short(world, fixture["away_team_id"] if fact["team_id"] == fixture["home_team_id"] else fixture["home_team_id"]),
                "score": f"{home} {score_text(fixture)} {away}",
                "goals": fact.get("goals", 0),
                "assists": fact.get("assists", 0),
                "chances_created": fact.get("chances_created", 0),
                "successful_defenses": fact.get("successful_defenses", 0),
                "successful_saves": fact.get("successful_saves", 0),
                "clean_sheets": fact.get("clean_sheets", 0),
            }
        )
    rows.sort(key=lambda row: (int(row["week"]), row["match_id"]), reverse=True)
    return rows[:limit]


def _player_career(save_name: str, player_id: str) -> List[dict]:
    rows = store.fetch_player_stats(save_name, player_id)
    if not rows:
        return []
    grouped: Dict[int, dict] = {}
    for row in rows:
        season = int(row["season_number"])
        entry = grouped.setdefault(
            season,
            {"season": season, "appearances": 0, "goals": 0, "assists": 0, "teams": {}, "competitions": {}},
        )
        entry["appearances"] += 1 if row.get("appeared") else 0
        for field in STAT_FIELDS:
            entry[field] = entry.get(field, 0) + int(row.get(field, 0))
        entry["teams"][row["team_id"]] = entry["teams"].get(row["team_id"], 0) + 1
        entry["competitions"][row["competition"]] = entry["competitions"].get(row["competition"], 0) + 1
    return [grouped[season] for season in sorted(grouped, reverse=True)]


def _player_awards(world: dict, player_id: str) -> List[dict]:
    rows = []
    for archive in [*world.get("archives", []), {"season_number": world["season_number"], "awards": world["season"].get("awards", {})}]:
        awards = archive.get("awards") or {}
        for competition, values in (awards.get("competitions") or {}).items():
            for award_type, winners in values.items():
                if not isinstance(winners, list):
                    continue
                for winner in winners:
                    if winner.get("player_id") == player_id:
                        rows.append(
                            {
                                "season": archive["season_number"],
                                "competition": competition,
                                "award": {"top_scorer": "射手王", "assist_leader": "助攻王", "mvp": "赛事 MVP", "evp": "赛事 EVP"}.get(award_type, award_type),
                                "value": winner.get("value"),
                            }
                        )
        for winner in awards.get("top20", []):
            if winner.get("player_id") == player_id:
                rows.append({"season": archive["season_number"], "competition": "top20", "award": f"年度 Top20 第 {winner.get('rank')} 名", "rank": winner.get("rank"), "value": winner.get("score")})
    return rows


# ---------------------------------------------------------------------------
# 历史
# ---------------------------------------------------------------------------


def history_page(world: dict, save_name: str, season: Optional[int] = None) -> dict:
    archives = list(world.get("archives", []))
    options = [
        {"season": archive["season_number"], "label": f"第 {archive['season_number']} 赛季"}
        for archive in sorted(archives, key=lambda item: item["season_number"], reverse=True)
    ]
    if not archives:
        return {"options": options, "archive": None, "selected": None, "empty": True}
    if season is None:
        selected = max(archives, key=lambda item: item["season_number"])
    else:
        selected = next((archive for archive in archives if int(archive["season_number"]) == int(season)), archives[-1])
    premier_order = selected.get("premier_order", [])
    second_order = selected.get("second_order", [])
    premier_table = selected.get("premier_table") or []
    second_table = selected.get("second_table") or []
    awards = selected.get("awards") or {}
    matches = store.fetch_matches(save_name, season=int(selected["season_number"]), status="completed")
    representative = []
    for match in matches[:8]:
        row = {
            "match_id": match["match_id"],
            "competition": match["competition"],
            "label": rules.competition_label(match["competition"]) if match["competition"] in rules.CUP_IDS else rules.display_name(match["competition"]),
            "home": team_short(world, match["home_team_id"]),
            "away": team_short(world, match["away_team_id"]),
            "score": f"{int(match.get('home_goals') or 0) + int(match.get('home_goals_et') or 0)}–{int(match.get('away_goals') or 0) + int(match.get('away_goals_et') or 0)}",
            "week": match["week"],
        }
        representative.append(row)
    return {
        "options": options,
        "selected": selected["season_number"],
        "archive": {
            "season": selected["season_number"],
            "premier_table": _archive_table(world, premier_order, premier_table, "premier"),
            "second_table": _archive_table(world, second_order, second_table, "second"),
            "honor_rows": [
                {**row, "team": team_short(world, row["team_id"]), "club": (world["teams"].get(row["team_id"]) or {}).get("club_en", "")}
                for row in selected.get("honor_rows", [])
            ],
            "honor_totals": [
                {"team_id": team_id, "team": team_short(world, team_id), "club": (world["teams"].get(team_id) or {}).get("club_en", ""), "points": points}
                for team_id, points in sorted((selected.get("honor_totals") or {}).items(), key=lambda item: -item[1])
            ],
            "cup_champions": [
                {
                    "competition": cup_id,
                    "label": rules.competition_label(cup_id),
                    "team_id": team_id,
                    "team": team_short(world, team_id),
                }
                for cup_id, team_id in (selected.get("cup_champions") or {}).items()
                if team_id
            ],
            "awards": _history_awards(world, awards),
            "top20": [
                {**row, "team": team_short(world, row.get("team_id"))}
                for row in (awards.get("top20") or [])
            ],
            "transition": selected.get("transition") or {},
            "transfers": selected.get("transfers") or [],
            "draft": selected.get("draft") or [],
            "representative_matches": representative,
        },
        "empty": False,
    }


def _archive_table(world: dict, order: List[str], table: List[dict], division: str) -> List[dict]:
    rows = []
    if table:
        for index, row in enumerate(table, start=1):
            rows.append(
                {
                    "rank": index,
                    "team_id": row["team_id"],
                    "team": team_short(world, row["team_id"]),
                    "club": (world["teams"].get(row["team_id"]) or {}).get("club_en", ""),
                    "played": row.get("played", 0),
                    "wins": row.get("wins", 0),
                    "draws": row.get("draws", 0),
                    "losses": row.get("losses", 0),
                    "goals_for": row.get("goals_for", 0),
                    "goals_against": row.get("goals_against", 0),
                    "goal_diff": row.get("goal_diff", 0),
                    "points": row.get("points", 0),
                }
            )
        return rows
    for index, team_id in enumerate(order, start=1):
        rows.append({"rank": index, "team_id": team_id, "team": team_short(world, team_id), "club": (world["teams"].get(team_id) or {}).get("club_en", ""), "played": 0, "wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0, "goal_diff": 0, "points": 0})
    return rows


def _history_awards(world: dict, awards: dict) -> dict:
    competitions = {}
    for competition, values in (awards.get("competitions") or {}).items():
        competitions[competition] = {
            "label": rules.competition_label(competition) if competition in rules.CUP_IDS else rules.display_name(competition),
            "top_scorer": "、".join(item.get("label", "") for item in values.get("top_scorer", [])),
            "assist_leader": "、".join(item.get("label", "") for item in values.get("assist_leader", [])),
            "mvp": "、".join(item.get("label", "") for item in values.get("mvp", [])),
            "evp": "、".join(item.get("label", "") for item in values.get("evp", [])),
            "mvp_minimum": values.get("mvp_minimum"),
        }
    return competitions


# ---------------------------------------------------------------------------
# 转会 / 选秀
# ---------------------------------------------------------------------------


def transfers_page(world: dict) -> dict:
    season = world["season"]
    pending = []
    for item in season["pending"].get("transfer_review", []):
        pending.append(
            {
                **item,
                "team_a_name": team_label(world, item["team_a"]),
                "team_b_name": team_label(world, item["team_b"]),
                "players_a": [{"player_id": pid, "name": player_name(world, pid), "position": world["players"][pid]["position"], "ability": world["players"][pid]["ability"], "value": _player_value(world, pid)} for pid in item["players_a"]],
                "players_b": [{"player_id": pid, "name": player_name(world, pid), "position": world["players"][pid]["position"], "ability": world["players"][pid]["ability"], "value": _player_value(world, pid)} for pid in item["players_b"]],
            }
        )
    history = []
    for row in reversed(world.get("transfer_history", [])):
        history.append(
            {
                **row,
                "team_a_name": team_label(world, row["team_a"]),
                "team_b_name": team_label(world, row["team_b"]),
                "players_a_names": [player_name(world, pid) for pid in row.get("players_a", [])],
                "players_b_names": [player_name(world, pid) for pid in row.get("players_b", [])],
            }
        )
    return {
        "pending": pending,
        "history": history[:60],
        "window": "冬窗" if 25 <= int(season["week"]) <= 27 else "夏窗" if int(season["week"]) >= 50 else "休赛期外",
        "season": world["season_number"],
        "week": season["week"],
    }


def _player_value(world: dict, player_id: str) -> float:
    return game_world._player_value(world, player_id)


def draft_page(world: dict, *, history: bool = False, selected_season: Optional[int] = None) -> dict:
    season = world["season"]
    pending = season["pending"].get("draft") or {}
    candidates = [
        {**item, "label": f"{item['name']} · {item['position']} · 能力 {item['ability']}"}
        for item in pending.get("candidates", [])
    ]
    order = [
        {"team_id": team_id, "team": team_label(world, team_id)}
        for team_id in pending.get("draft_order", [])
    ]
    logs = list(season.get("draft_log", []))
    options = []
    if history:
        from football_simulator.app.profile_history import draft_history
        logs = draft_history(world)
        options = list(range(int(season["number"]), 0, -1))
        selected_season = max(1, min(int(season["number"]), int(selected_season or season["number"])))
        logs = [log for log in logs if int(log["season"]) == selected_season]
        if selected_season != int(season["number"]):
            pending = {}
            candidates = []
            order = []
    return {
        "options": options,
        "selected_season": selected_season or int(season["number"]),
        "status": pending.get("status"),
        "season": season["number"],
        "stage": pending.get("stage"),
        "candidates": candidates,
        "order": order,
        "logs": [
            {
                "season": log["season"],
                "results": [
                    {**row, "team": team_short(world, row["team_id"]), "player": player_name(world, row["player_id"])}
                    for row in log.get("results", [])
                ],
                "undrafted": log.get("undrafted", []),
                "skipped": log.get("skipped", []),
                "no_slot": log.get("no_slot", []),
                "selected_count": log.get("selected_count", len(log.get("results", []))),
                "candidate_count": log.get("candidate_count", len(log.get("results", [])) + len(log.get("undrafted", []))),
            }
            for log in logs
        ],
        "empty": not pending and not logs,
    }


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _season_totals(world: dict) -> Dict[str, dict]:
    return game_world._aggregate_player_facts(world, completed_matches(world))


def _standings_before(world: dict, week: int) -> Dict[str, List[dict]]:
    if week <= 0:
        return {key: [] for key in ("premier", "second")}
    rank = game_world.competitions.previous_ranks(world)
    tables = {}
    for key, competition in (("premier", rules.PREMIER), ("second", rules.SECOND)):
        team_ids = world["season"]["league"][key]
        matches = [match for match in completed_matches(world, competition) if int(match["week"]) < week]
        tables[key] = standings_from_matches(team_ids, matches, previous_rank=rank)
    return tables


# ---------------------------------------------------------------------------
# 本轮赛事 UI：只读聚合（默认语义不变，旧调用不受影响）
# ---------------------------------------------------------------------------


def week_competitions(world: dict, week: int, *, completed_only: bool = False) -> List[dict]:
    """Every competition with fixtures in that week, aggregated from ALL fixtures.

    Not inferred from the dashboard's 16-row slices: a mixed week must not hide a
    cup. Stage labels come from the saved stage, not from a fixed calendar.
    """
    week = int(week or 0)
    buckets: Dict[str, dict] = {}
    for fixture in world["season"].get("fixtures", []):
        if int(fixture.get("week") or 0) != week:
            continue
        if completed_only and fixture.get("status") != "completed":
            continue
        competition = fixture["competition"]
        entry = buckets.setdefault(
            competition,
            {
                "competition_id": competition,
                "label": rules.competition_label(competition) if competition in rules.CUP_IDS else rules.display_name(competition),
                "stages": [],
                "total": 0,
                "completed": 0,
                "week": week,
            },
        )
        entry["total"] += 1
        if fixture.get("status") == "completed":
            entry["completed"] += 1
        stage = fixture.get("stage") or ""
        title = stage_text(competition, stage) if stage else "联赛轮次"
        if title and title not in entry["stages"]:
            entry["stages"].append(title)
    rows = list(buckets.values())
    for row in rows:
        row["scheduled"] = row["total"] - row["completed"]
        row["stage_text"] = " · ".join(row["stages"][:2]) + (f" 等 {len(row['stages'])} 个阶段" if len(row["stages"]) > 2 else "")
        row["route"] = ("competition", {"competition_id": row["competition_id"]})
        row["matches_route"] = ("matches", {"competition": row["competition_id"], "week": week})
    rows.sort(key=lambda row: (0 if row["competition_id"] in (rules.PREMIER, rules.SECOND) else 1, row["competition_id"]))
    return rows


def competition_leaders(world: dict, competition: str, *, limit: int = 10) -> dict:
    """Goals/assists top lists, filtered before truncation, merged across clubs.

    Sorting follows the shared award order (goals then assists then fewer
    appearances then stable id; assists then goals). Appearances stay in the
    data and only disappear from the visible columns.
    """
    players = competition_players(world, competition)
    goals = [row for row in players if int(row.get("goals") or 0) > 0]
    goals.sort(key=lambda row: (-int(row["goals"]), -int(row["assists"]), int(row["appearances"]), row["player_id"]))
    assists = [row for row in players if int(row.get("assists") or 0) > 0]
    assists.sort(key=lambda row: (-int(row["assists"]), -int(row["goals"]), int(row["appearances"]), row["player_id"]))
    def decorate(rows):
        out = []
        for index, row in enumerate(rows[:limit], start=1):
            team_id = row.get("team_id")
            out.append({
                **row,
                "rank": index,
                "club": (world["teams"].get(team_id) or {}).get("club_en", ""),
                "team_label": row.get("team") or "无所属球队",
                "has_team": bool(team_id and team_id in world["teams"]),
            })
        return out
    return {
        "goals": decorate(goals),
        "assists": decorate(assists),
        "goals_total": len(goals),
        "assists_total": len(assists),
    }


def archived_league_table(world: dict, competition: str, season: Optional[int] = None) -> dict:
    """Full archived league table for one season, read from the archive only.

    Never recomputes with the current season's teams or points. Missing season or
    incomplete archive returns an explicit empty state.
    """
    key = "premier" if competition == rules.PREMIER else "second"
    archives = sorted(world.get("archives", []), key=lambda item: int(item.get("season_number") or 0), reverse=True)
    seasons = [int(archive["season_number"]) for archive in archives]
    if not archives:
        return {"seasons": [], "season": None, "table_key": key, "rows": [], "champion": None, "empty": True, "reason": "首季尚无归档赛季"}
    selected = None
    if season is not None:
        selected = next((archive for archive in archives if int(archive.get("season_number") or 0) == int(season)), None)
    selected = selected or archives[0]
    order = selected.get(f"{key}_order") or []
    table = selected.get(f"{key}_table") or []
    rows = _archive_table(world, order, table, key)
    complete = bool(rows) and all(int(row.get("played") or 0) > 0 for row in rows)
    if not rows:
        return {"seasons": seasons, "season": int(selected["season_number"]), "table_key": key, "rows": [], "champion": None, "empty": True, "reason": "该届档案缺少联赛表"}
    champion = rows[0] if complete else None
    for row in rows:
        row["club"] = (world["teams"].get(row["team_id"]) or {}).get("club_en", "")
    return {
        "seasons": seasons,
        "season": int(selected["season_number"]),
        "table_key": key,
        "rows": rows,
        "champion": {"team_id": champion["team_id"], "team": champion["team"], "club": champion.get("club", "")} if champion else None,
        "complete": complete,
        "empty": False,
    }
