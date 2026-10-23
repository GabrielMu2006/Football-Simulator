"""联赛赛程、周次映射与比赛事实创建。

联赛沿用 20 队双循环圆桌法（38 轮）。所有比赛只在写命令中创建并保存；
读取页面不会重建赛程、抽签或消耗随机数。
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Sequence, Tuple

from football_simulator.game import rules
from football_simulator.game.models import slugify


def build_league_rounds(team_ids: Sequence[str]) -> List[List[Tuple[str, str]]]:
    """返回 38 轮，每轮 10 场 (home, away)，双重循环。"""
    if len(team_ids) != 20:
        raise ValueError("一级/次级联赛都必须正好 20 队。")
    rotation = list(team_ids)
    first_half: List[List[Tuple[str, str]]] = []
    for round_index in range(19):
        pairs: List[Tuple[str, str]] = []
        for index in range(10):
            home = rotation[index]
            away = rotation[19 - index]
            if round_index % 2 == 1:
                home, away = away, home
            pairs.append((home, away))
        first_half.append(pairs)
        rotation = [rotation[0], rotation[-1], *rotation[1:-1]]
    second_half = [[(away, home) for home, away in pairs] for pairs in first_half]
    return first_half + second_half


def league_week_for_round(round_number: int) -> int:
    for week in rules.calendar_weeks():
        if int(week.get("league_round") or 0) == round_number:
            return int(week["week"])
    raise KeyError(f"赛历中没有联赛第 {round_number} 轮。")


def cup_stage_week(competition: str, stage: str) -> int:
    weeks = rules.stage_weeks(competition).get(stage)
    if not weeks:
        raise KeyError(f"{competition} 没有阶段 {stage}。")
    return weeks[0]


def add_match(
    world: dict,
    *,
    competition: str,
    stage: str,
    week: int,
    home_team_id: str,
    away_team_id: str,
    leg: int = 1,
    tie_id: Optional[str] = None,
    venue_mode: str = "home",
    stadium_id: Optional[str] = None,
    neutral_reason: Optional[str] = None,
    round_number: int = 1,
    ordinal: int = 0,
    need_winner: bool = False,
    aggregate_from_tie: bool = False,
    group: Optional[str] = None,
) -> str:
    if home_team_id == away_team_id:
        raise ValueError("主客队不能相同。")
    season = world["season"]
    if venue_mode == "home":
        stadium_id = world["teams"][home_team_id]["stadium_id"]
    elif not stadium_id:
        raise ValueError("中立场比赛必须指定球场。")
    seq = int(season.get("match_seq", 0)) + 1
    season["match_seq"] = seq
    match_id = f"m{int(world['season_number']):02d}-{seq:05d}"
    fixture = {
        "match_id": match_id,
        "season_number": int(world["season_number"]),
        "competition": competition,
        "stage": stage,
        "week": int(week),
        "round": int(round_number),
        "ordinal": int(ordinal),
        "leg": int(leg),
        "tie_id": tie_id,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "stadium_id": stadium_id,
        "venue_mode": venue_mode,
        "neutral_reason": neutral_reason,
        "home_advantage_team_id": home_team_id if venue_mode == "home" else None,
        "need_winner": bool(need_winner),
        "aggregate_from_tie": bool(aggregate_from_tie),
        "group": group,
        "status": "scheduled",
        "home_goals": 0,
        "away_goals": 0,
        "home_goals_et": 0,
        "away_goals_et": 0,
        "home_pk": None,
        "away_pk": None,
        "decided_by": None,
        "winner_team_id": None,
        "events": [],
        "player_facts": {},
    }
    season["fixtures"].append(fixture)
    world.setdefault("_fixture_index", {})[match_id] = fixture
    return match_id


def fixture_index(world: dict) -> Dict[str, dict]:
    index = world.get("_fixture_index")
    if index is None:
        index = {fixture["match_id"]: fixture for fixture in world["season"]["fixtures"]}
        world["_fixture_index"] = index
    return index


def get_fixture(world: dict, match_id: str) -> dict:
    return fixture_index(world)[match_id]


def week_fixtures(world: dict, week: int) -> List[dict]:
    return [fixture for fixture in world["season"]["fixtures"] if int(fixture["week"]) == week]


def completed_matches(world: dict, competition: Optional[str] = None) -> List[dict]:
    return [
        fixture
        for fixture in world["season"]["fixtures"]
        if fixture["status"] == "completed" and (competition is None or fixture["competition"] == competition)
    ]


def standings_from_matches(
    team_ids: Sequence[str],
    matches: Sequence[dict],
    *,
    previous_rank: Optional[Dict[str, int]] = None,
    partial: bool = False,
) -> List[dict]:
    """积分榜：积分→净胜球→进球→相互战绩→上季排名→team_id。

    ``partial=True`` 用于组外循环：只统计列表内球队的表现，允许对手不在
    本组表中（跨组比赛仍计入本组球队的积分与净胜球），按积分→净胜球→
    进球→胜场→上季排名排序；组内无交手，不使用相互战绩。
    """
    rows = {
        team_id: {
            "team_id": team_id,
            "played": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goals_for": 0,
            "goals_against": 0,
            "points": 0,
        }
        for team_id in team_ids
    }
    head_to_head: Dict[Tuple[str, str], List[Tuple[int, int]]] = {}
    for match in matches:
        if match["status"] != "completed":
            continue
        home = match["home_team_id"]
        away = match["away_team_id"]
        if not partial and (home not in rows or away not in rows):
            continue
        if home not in rows and away not in rows:
            continue
        home_goals = int(match["home_goals"]) + int(match.get("home_goals_et") or 0)
        away_goals = int(match["away_goals"]) + int(match.get("away_goals_et") or 0)
        for team_id, goals_for, goals_against in ((home, home_goals, away_goals), (away, away_goals, home_goals)):
            if team_id not in rows:
                continue
            row = rows[team_id]
            row["played"] += 1
            row["goals_for"] += goals_for
            row["goals_against"] += goals_against
            if goals_for > goals_against:
                row["wins"] += 1
                row["points"] += 3
            elif goals_for < goals_against:
                row["losses"] += 1
            else:
                row["draws"] += 1
                row["points"] += 1
        if home in rows and away in rows:
            head_to_head.setdefault((home, away), []).append((home_goals, away_goals))
            head_to_head.setdefault((away, home), []).append((away_goals, home_goals))

    for row in rows.values():
        row["goal_diff"] = row["goals_for"] - row["goals_against"]

    previous_rank = previous_rank or {}

    def mini_table(team_ids: List[str], matches: List[dict]) -> Dict[str, Tuple[int, int, int]]:
        table = {team_id: [0, 0, 0] for team_id in team_ids}  # points, gd, gf
        team_set = set(team_ids)
        for match in matches:
            if match["status"] != "completed":
                continue
            home = match["home_team_id"]
            away = match["away_team_id"]
            if home not in team_set or away not in team_set:
                continue
            home_goals = int(match["home_goals"]) + int(match.get("home_goals_et") or 0)
            away_goals = int(match["away_goals"]) + int(match.get("away_goals_et") or 0)
            table[home][1] += home_goals - away_goals
            table[home][2] += home_goals
            table[away][1] += away_goals - home_goals
            table[away][2] += away_goals
            if home_goals > away_goals:
                table[home][0] += 3
            elif home_goals < away_goals:
                table[away][0] += 3
            else:
                table[home][0] += 1
                table[away][0] += 1
        return {team_id: (values[0], values[1], values[2]) for team_id, values in table.items()}

    def order_tied(group: List[dict], depth: int) -> List[dict]:
        """递归相互战绩：先按小积分榜分开，再对剩余同分队重算。"""
        if len(group) <= 1:
            return list(group)
        team_ids = [row["team_id"] for row in group]
        mini = mini_table(team_ids, matches)
        ordered = sorted(
            group,
            key=lambda row: (
                tuple(-value for value in mini[row["team_id"]]),
                previous_rank.get(row["team_id"], 999),
                row["team_id"],
            ),
        )
        result: List[dict] = []
        start = 0
        separated = False
        while start < len(ordered):
            end = start + 1
            key = mini[ordered[start]["team_id"]]
            while end < len(ordered) and mini[ordered[end]["team_id"]] == key:
                end += 1
            tied = ordered[start:end]
            if len(tied) > 1:
                if depth >= len(group) or len({row["team_id"] for row in tied}) == len(group):
                    # 无法再分出层次：按上季排名/稳定 ID 收尾，避免死循环。
                    tied = sorted(
                        tied,
                        key=lambda row: (previous_rank.get(row["team_id"], 999), row["team_id"]),
                    )
                else:
                    tied = order_tied(tied, depth + 1)
            else:
                separated = True
            result.extend(tied)
            start = end
        return result

    def sort_group(group: List[dict]) -> List[dict]:
        if len(group) <= 1:
            return list(group)
        ordered = sorted(
            group,
            key=lambda row: (
                -row["points"],
                -row["goal_diff"],
                -row["goals_for"],
                previous_rank.get(row["team_id"], 999),
                row["team_id"],
            ),
        )
        result: List[dict] = []
        start = 0
        while start < len(ordered):
            end = start + 1
            base = (ordered[start]["points"], ordered[start]["goal_diff"], ordered[start]["goals_for"])
            while end < len(ordered) and (
                ordered[end]["points"], ordered[end]["goal_diff"], ordered[end]["goals_for"]
            ) == base:
                end += 1
            tied = ordered[start:end]
            result.extend(order_tied(tied, 0) if len(tied) > 1 else tied)
            start = end
        return result

    if partial:
        return sorted(
            rows.values(),
            key=lambda row: (
                -row["points"],
                -row["goal_diff"],
                -row["goals_for"],
                -row["wins"],
                previous_rank.get(row["team_id"], 999),
                row["team_id"],
            ),
        )
    return sort_group(list(rows.values()))
