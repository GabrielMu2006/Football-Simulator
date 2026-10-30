"""世界推进：新档、周推进、结算、选秀、转会、归档与新赛季。

所有写入都在命令内完成；读取页面只调用查询层，不触发本模块。赛季日历严格
使用规则 JSON 的 52 周槽位，第 24/49 周结算，第 50–52 周保留夏窗操作。
"""

from __future__ import annotations

import math
import random
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from football_simulator.game import competitions, rules, transfers
from football_simulator.game.calendar import (
    add_match,
    build_league_rounds,
    completed_matches,
    get_fixture,
    league_week_for_round,
    standings_from_matches,
    week_fixtures,
)
from football_simulator.game.config import (
    build_teams_and_players,
    load_master_config,
    team_objects,
)
from football_simulator.game.engine import MatchEngine
from football_simulator.game.models import (
    FORMATION_RULES,
    POSITION_GOALKEEPER,
    Player,
    empty_player_fact,
    real_player_id_for,
)
from football_simulator.domain.formulas import calculate_market_value, calculate_player_rating
from football_simulator.persistence_v2 import store

WINTER_SETTLEMENT_WEEK = 24
FINAL_SETTLEMENT_WEEK = 49
TOTAL_WEEKS = 52
SECOND_LEAGUE_WEIGHT = 0.35
DEFAULT_MODE = "key_pause"


class WorldError(ValueError):
    pass


# ---------------------------------------------------------------------------
# 新世界
# ---------------------------------------------------------------------------


def create_new_world(save_name: str, *, rng: Optional[random.Random] = None) -> dict:
    rng = rng or random.SystemRandom()
    save_name = runtime_normalize(save_name)
    config = load_master_config()
    teams, players, draft_pool, divisions = build_teams_and_players(config, rng)
    from football_simulator.game.models import team_id_for

    club_to_id = {
        club: team_id_for(club)
        for club in [*divisions["premier"], *divisions["second"]]
    }
    division_ids = {
        "premier": [club_to_id[club] for club in divisions["premier"]],
        "second": [club_to_id[club] for club in divisions["second"]],
    }
    world = {
        "format": "football-simulator-world",
        "format_version": rules.WORLD_FORMAT_VERSION,
        "app_version": store.APP_VERSION,
        "rules_version": rules.RULES_VERSION,
        "awards_rule_version": "0.3.1",
        "world_id": str(uuid.uuid4()),
        "save_name": save_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "season_number": 1,
        "version": 0,
        "teams": teams,
        "players": players,
        "draft_pool": draft_pool,
        "draft_pool_index": 0,
        "transfer_history": [],
        "story_events": [],
        "archives": [],
        "settings": {"mode": DEFAULT_MODE, "watchlist": []},
        "season": _new_season_state(1, teams, players, division_ids, None),
    }
    world["season"]["fixtures"] = []
    _create_league_fixtures(world, rng)
    competitions.initialize_cups(world, 1, rng)
    store.create_database(save_name, world, _save_config_payload(config, divisions))
    return store.load_world(save_name)


def _new_season_state(
    season_number: int,
    teams: Dict[str, dict],
    players: Dict[str, dict],
    divisions: Dict[str, List[str]],
    previous: Optional[dict],
) -> dict:
    season = {
        "number": season_number,
        "week": 1,
        "fixtures": [],
        "cups": {},
        "neutral_venues": {},
        "match_seq": 0,
        "tie_seq": 0,
        "pending": {"ability_review": [], "transfer_review": [], "draft": {}},
        "mode": DEFAULT_MODE,
        "watchlist": [],
        "settlements": {},
        "awards": {},
        "honor_rows": [],
        "honor_totals": {},
        "previous": previous or {},
        "transition": {},
        "finalized": False,
        "season_complete": False,
        "transfers": [],
        "draft_log": [],
        "events": [],
        "promotion_playoff": {},
        "league": {
            "premier": list(divisions["premier"]),
            "second": list(divisions["second"]),
        },
    }
    return season


def _create_league_fixtures(world: dict, rng: random.Random) -> None:
    for division_key, competition, division_name in (
        ("premier", rules.PREMIER, "一级联赛"),
        ("second", rules.SECOND, "次级联赛"),
    ):
        team_ids = world["season"]["league"][division_key]
        rounds = build_league_rounds(team_ids)
        for round_index, pairs in enumerate(rounds, start=1):
            week = league_week_for_round(round_index)
            for ordinal, (home, away) in enumerate(pairs):
                add_match(
                    world,
                    competition=competition,
                    stage="league",
                    week=week,
                    home_team_id=home,
                    away_team_id=away,
                    round_number=round_index,
                    ordinal=ordinal,
                )


def _save_config_payload(config: dict, divisions: Dict[str, List[str]]) -> dict:
    # 导出包必须携带本存档实际分区与完整配置，而不是主配置的原始分区。
    by_club = {
        club: (config.get("team_chinese_names") or {}).get(club, club)
        for club in [*config["premier_teams"], *config["second_division_teams"]]
    }
    club_by_id = {}
    # divisions 里是稳定 team_id；调用方传入的 config 不含映射，因此由
    # create_new_world 在写入前补充真实分区（见 _save_config_payload 的调用）。
    return {
        "premier_teams": list(divisions.get("premier_names", [])),
        "second_division_teams": list(divisions.get("second_names", [])),
        "team_chinese_names": by_club,
        "real_players": list(config.get("real_players", [])),
        "default_player_ability": 50,
        "real_player_ability_min": 60,
        "real_player_ability_max": 88,
    }


def runtime_normalize(save_name: str) -> str:
    from football_simulator import runtime

    return runtime.normalize_save_name(save_name)


# ---------------------------------------------------------------------------
# 推进一周
# ---------------------------------------------------------------------------


def advance_week(save_name: str, *, rng: Optional[random.Random] = None) -> dict:
    world = store.load_world(save_name)
    season = world["season"]
    if season.get("season_complete"):
        raise WorldError("本赛季已结束，请先开始下一赛季。")
    pending = season["pending"]
    if pending.get("ability_review"):
        raise WorldError("还有未处理的能力审核，请先完成。")
    if pending.get("draft", {}).get("status") == "awaiting_input":
        raise WorldError("还有未处理的选秀，请先完成。")
    if pending.get("transfer_review"):
        raise WorldError("还有未处理的转会审核，请先完成。")

    week = int(season["week"])
    if week > TOTAL_WEEKS:
        raise WorldError("当前赛季已经没有剩余周次。")
    rng = rng or random.SystemRandom()
    engine = MatchEngine(rng)
    team_objects_map = team_objects(world["teams"], world["players"])

    completed_ids: List[str] = []
    for fixture in _week_order(week_fixtures(world, week)):
        if fixture["status"] == "completed":
            continue
        home = team_objects_map[fixture["home_team_id"]]
        away = team_objects_map[fixture["away_team_id"]]
        aggregate = _aggregate_for(world, fixture)
        facts = engine.play(
            {
                "match_id": fixture["match_id"],
                "season_number": world["season_number"],
                "competition": fixture["competition"],
                "stage": fixture["stage"],
                "week": fixture["week"],
                "round": fixture.get("round", 1),
                "ordinal": fixture.get("ordinal", 0),
                "leg": fixture.get("leg", 1),
                "tie_id": fixture.get("tie_id"),
                "stadium_id": fixture["stadium_id"],
                "venue_mode": fixture["venue_mode"],
                "neutral_reason": fixture.get("neutral_reason"),
                "home_advantage_team_id": fixture.get("home_advantage_team_id"),
            },
            home,
            away,
            need_winner=bool(fixture.get("need_winner")),
            aggregate=aggregate,
        )
        _apply_facts(fixture, facts)
        completed_ids.append(fixture["match_id"])

    competitions.progress_cups(world, week, rng)
    _capture_championships(world, week)
    if week == 42:
        _create_promotion_playoffs(world)
    if week == 47:
        _create_promotion_final(world)
    if week == WINTER_SETTLEMENT_WEEK:
        _compute_settlement(world, "winter", cutoff_week=WINTER_SETTLEMENT_WEEK)
        _prepare_transfer_review(world, rng, "winter")
    if week == FINAL_SETTLEMENT_WEEK:
        _finalize_sport(world, rng)
        _prepare_ability_review(world, rng)
        _prepare_draft(world, rng)
    if week in (25, 26, 27, 50, 51, 52):
        _prepare_transfer_review(world, rng, "summer" if week >= 50 else "winter")
    from football_simulator.game import editions
    editions.capture_finished(world)
    if week >= TOTAL_WEEKS:
        season["season_complete"] = True
        _archive_season(world)
    season["week"] = min(TOTAL_WEEKS, week + 1) if week < TOTAL_WEEKS else TOTAL_WEEKS
    world["story_events"] = _generate_week_events(world, completed_ids, week)
    season["events"] = [event for event in world["story_events"] if event["season"] == world["season_number"]]
    store.write_world(save_name, world, completed_week=week)
    return {"week": week, "matches": completed_ids, "world": world}


def _capture_championships(world: dict, decided_week: int) -> None:
    """冠军决定当刻冻结注册阵容；与本周事实同一 world 写入事务。

    幂等：title_id 已存在时不覆盖名单。冠军不得早于冬窗结束后产生。
    """
    from football_simulator.game.models import slugify  # noqa: F401

    season = world["season"]
    records = season.setdefault("championships", {})
    season_number = int(world["season_number"])
    world_id = str(world.get("world_id") or "world")

    def capture(competition_id: str, team_id: str, match_id: Optional[str]) -> None:
        if not team_id:
            return
        if int(decided_week) <= 27:
            raise WorldError(
                f"{competition_id} 冠军决定周 {decided_week} 不得早于冬窗结束（第27周）。"
            )
        title_id = f"{world_id}:{season_number}:{competition_id}"
        if title_id in records:
            return
        team = world["teams"].get(team_id) or {}
        roster = [entry.get("player_id") for entry in team.get("roster", [])]
        records[title_id] = {
            "title_id": title_id,
            "season_number": season_number,
            "competition_id": competition_id,
            "champion_team_id": team_id,
            "decided_week": int(decided_week),
            "decided_match_id": match_id,
            "roster_player_ids": [pid for pid in dict.fromkeys(roster) if pid],
            "roster_slots": [
                {"slot_number": entry.get("slot_number"), "position": entry.get("position"), "player_id": entry.get("player_id")}
                for entry in team.get("roster", [])
            ],
            "source": "championship_decision",
        }

    for cup_id in rules.CUP_IDS:
        state = season.get("cups", {}).get(cup_id) or {}
        champion = state.get("champion")
        if champion:
            capture(cup_id, champion, _final_match_id(world, cup_id))

    if int(decided_week) == 42:
        tables = league_tables(world)
        for competition_id, key in ((rules.PREMIER, "premier"), (rules.SECOND, "second")):
            rows = tables.get(key) or []
            if rows:
                capture(competition_id, rows[0]["team_id"], None)


def _week_order(fixtures: List[dict]) -> List[dict]:
    order = {rules.PREMIER: 0, rules.SECOND: 1, "playoff": 2}
    return sorted(fixtures, key=lambda fixture: (order.get(fixture["competition"], 3), fixture["match_id"]))


def _aggregate_for(world: dict, fixture: dict) -> Optional[Tuple[int, int]]:
    if not fixture.get("aggregate_from_tie"):
        return None
    tie_id = fixture.get("tie_id")
    first_leg = next(
        (
            item
            for item in world["season"]["fixtures"]
            if item.get("tie_id") == tie_id and int(item.get("leg", 1)) == 1
        ),
        None,
    )
    if first_leg is None:
        return (0, 0)
    home_id = fixture["home_team_id"]
    away_id = fixture["away_team_id"]
    home_goals = _goals_for_team(first_leg, home_id)
    away_goals = _goals_for_team(first_leg, away_id)
    return (home_goals, away_goals)


def _goals_for_team(fixture: dict, team_id: str) -> int:
    if fixture["home_team_id"] == team_id:
        return int(fixture.get("home_goals", 0)) + int(fixture.get("home_goals_et", 0))
    return int(fixture.get("away_goals", 0)) + int(fixture.get("away_goals_et", 0))


def _apply_facts(fixture: dict, facts) -> None:
    fixture.update(
        {
            "status": "completed",
            "home_goals": facts.home_goals,
            "away_goals": facts.away_goals,
            "home_goals_et": facts.home_goals_et,
            "away_goals_et": facts.away_goals_et,
            "home_pk": facts.home_pk,
            "away_pk": facts.away_pk,
            "pk_first": facts.pk_first,
            "shootout": facts.shootout,
            "events": facts.events,
            "player_facts": facts.player_facts,
            "winner_team_id": facts.winner_team_id,
            "decided_by": facts.decided_by,
            "extra_time_played": facts.extra_time_played,
            "home_advantage_team_id": facts.home_advantage_team_id,
            "total_home_goals": facts.total_home_goals,
            "total_away_goals": facts.total_away_goals,
        }
    )


# ---------------------------------------------------------------------------
# 结算、奖项、荣誉、归档
# ---------------------------------------------------------------------------


def _compute_settlement(world: dict, stage: str, cutoff_week: int) -> None:
    matches = [m for m in completed_matches(world) if int(m["week"]) <= cutoff_week]
    totals = _aggregate_player_facts(world, matches)
    players_settlement: Dict[str, dict] = {}
    for player_id, data in world["players"].items():
        if not data.get("is_real"):
            continue
        stats = totals.get(player_id)
        if stats and stats["appearances"] > 0:
            player = _player_object(world, player_id)
            old_rating = calculate_player_rating(_stats_namespace(player, stats), stats["appearances"])
            market_value = calculate_market_value(player, old_rating)
            if world.get("awards_rule_version") == "0.3.1":
                from football_simulator.domain.formulas import calculate_player_rating_v031
                rating = calculate_player_rating_v031(_stats_namespace(player, stats), stats["appearances"])
            else:
                rating = old_rating
        else:
            rating = None
            market_value = data.get("initial_market_value")
        players_settlement[player_id] = {
            "rating": rating,
            "market_value": market_value,
            "appearances": stats["appearances"] if stats else 0,
            "goals": stats["goals"] if stats else 0,
            "assists": stats["assists"] if stats else 0,
            "ability": data["ability"],
        }
    world["season"]["settlements"][stage] = {
        "stage": stage,
        "cutoff_week": cutoff_week,
        "players": players_settlement,
    }
    if stage == "final":
        for player_id, data in players_settlement.items():
            if data["market_value"] is not None and player_id in world["players"]:
                world["players"][player_id]["market_value"] = data["market_value"]


def _finalize_sport(world: dict, rng: random.Random) -> None:
    season = world["season"]
    if season.get("finalized"):
        return
    resolve_promotion_playoff(world)
    _compute_settlement(world, "final", cutoff_week=FINAL_SETTLEMENT_WEEK)
    tables = league_tables(world)
    season["final_tables"] = {key: rows for key, rows in tables.items()}
    season["honor_rows"], season["honor_totals"] = compute_honors(world)
    if world.get("awards_rule_version") == "0.3.1":
        from football_simulator.game import editions
        editions.capture_finished(world)
    season["awards"] = compute_season_awards(world)
    season["transition"] = compute_transition(world, tables)
    season["finalized"] = True


def league_tables(world: dict) -> Dict[str, List[dict]]:
    rank = competitions.previous_ranks(world)
    tables = {}
    for key, competition in (("premier", rules.PREMIER), ("second", rules.SECOND)):
        team_ids = world["season"]["league"][key]
        matches = completed_matches(world, competition)
        tables[key] = standings_from_matches(team_ids, matches, previous_rank=rank)
    return tables


def compute_honors(world: dict) -> Tuple[List[dict], Dict[str, int]]:
    season = world["season"]
    tables = season.get("final_tables") or league_tables(world)
    rows: List[dict] = []
    totals: Dict[str, int] = {team_id: 0 for team_id in world["teams"]}
    for rank, row in enumerate(tables["premier"], start=1):
        points = rules.premier_honor_points(rank, world.get("honor_scoring_profile"))
        rows.append(
            {
                "team_id": row["team_id"],
                "competition": rules.PREMIER,
                "rank": rank,
                "points": points,
                "tier": f"league_{rank}",
                "label": f"一级联赛第 {rank} 名",
            }
        )
        totals[row["team_id"]] += points
    for rank, row in enumerate(tables["second"], start=1):
        rows.append(
            {
                "team_id": row["team_id"],
                "competition": rules.SECOND,
                "rank": rank,
                "points": 0,
                "tier": f"league_{rank}",
                "label": f"次级联赛第 {rank} 名（0 荣誉分）",
            }
        )
    for cup_id, state in season.get("cups", {}).items():
        placements = state.get("placements") or {}
        if not placements:
            continue
        for team_id, rank in placements.items():
            tier = rules.honor_key_for_rank(cup_id, rank)
            points = rules.honor_points_for(cup_id, rank, world.get("honor_scoring_profile"))
            rows.append(
                {
                    "team_id": team_id,
                    "competition": cup_id,
                    "rank": rank,
                    "points": points,
                    "tier": tier,
                    "label": f"{rules.display_name(cup_id, 'en')} {tier}",
                }
            )
            totals[team_id] = totals.get(team_id, 0) + points
    rows.sort(key=lambda row: (row["competition"], row["rank"], row["team_id"]))
    return rows, totals


def compute_transition(world: dict, tables: Dict[str, List[dict]]) -> dict:
    premier = tables["premier"]
    second = tables["second"]
    relegated = [row["team_id"] for row in premier[-3:]]
    promoted_direct = [row["team_id"] for row in second[:2]]
    playoff_winner = world["season"].get("promotion_playoff", {}).get("winner")
    promoted = list(promoted_direct)
    if playoff_winner and playoff_winner not in promoted:
        promoted.append(playoff_winner)
    promoted = promoted[:3]
    premier_ids = [row["team_id"] for row in premier]
    second_ids = [row["team_id"] for row in second]
    next_premier = [team_id for team_id in premier_ids if team_id not in relegated] + promoted
    next_second = [team_id for team_id in second_ids if team_id not in promoted] + relegated
    return {
        "relegated": relegated,
        "promoted_direct": promoted_direct,
        "playoff_winner": playoff_winner,
        "promoted": promoted,
        "next_premier": next_premier,
        "next_second": next_second,
    }


def _aggregate_player_facts(world: dict, matches: Iterable[dict]) -> Dict[str, dict]:
    totals: Dict[str, dict] = {}
    for match in matches:
        if match["status"] != "completed":
            continue
        for player_id, fact in (match.get("player_facts") or {}).items():
            if not fact.get("appeared"):
                continue
            row = totals.setdefault(
                player_id,
                {
                    "appearances": 0,
                    "goals": 0,
                    "assists": 0,
                    "chances_created": 0,
                    "successful_defenses": 0,
                    "successful_saves": 0,
                    "clean_sheets": 0,
                    "teams": {},
                },
            )
            row["appearances"] += 1
            for field in ("goals", "assists", "chances_created", "successful_defenses", "successful_saves", "clean_sheets"):
                row[field] += int(fact.get(field, 0))
            team_id = fact.get("team_id")
            if team_id:
                row["teams"][team_id] = row["teams"].get(team_id, 0) + 1
    return totals


def _stats_namespace(player: Player, stats: dict):
    return SimpleNamespace(
        player=player,
        goals=stats["goals"],
        assists=stats["assists"],
        chances_created=stats["chances_created"],
        successful_defenses=stats["successful_defenses"],
        successful_saves=stats["successful_saves"],
        clean_sheets=stats["clean_sheets"],
    )


def _player_object(world: dict, player_id: str) -> Player:
    data = world["players"][player_id]
    return Player(
        player_id=player_id,
        name=data.get("name"),
        position=data["position"],
        ability=int(data["ability"]),
        is_real=bool(data.get("is_real")),
        slot_number=int(data.get("slot_number", 0)),
        initial_market_value=data.get("initial_market_value"),
        team_id=data.get("team_id"),
    )


def _award_winners(rows: List[dict], key, *, value_field: str, require_positive: bool = False) -> List[dict]:
    if not rows:
        return []
    best = min(key(row) for row in rows)
    winners = [row for row in rows if key(row) == best]
    if require_positive and all(int(row[value_field]) <= 0 for row in winners):
        return []
    winners.sort(key=lambda row: (row["label"], row["player_id"]))
    return winners


def compute_season_awards(world: dict) -> dict:
    if world.get("awards_rule_version") == "0.3.1":
        from football_simulator.game.awards_v031 import season_awards
        return season_awards(world)
    season = world["season"]
    totals = _aggregate_player_facts(world, completed_matches(world))
    tables = season.get("final_tables") or league_tables(world)
    # 球队荣誉分来自本赛季所有赛事。
    honor_totals = season.get("honor_totals") or compute_honors(world)[1]
    second_ids = {row["team_id"] for row in tables["second"]}
    top20 = []
    for player_id, stats in totals.items():
        data = world["players"].get(player_id)
        if not data or not data.get("is_real"):
            continue
        player = _player_object(world, player_id)
        rating = calculate_player_rating(_stats_namespace(player, stats), stats["appearances"])
        team_id = data.get("team_id") or next(iter(stats["teams"]), None)
        if not team_id:
            continue
        honor = int(honor_totals.get(team_id, 0))
        weight = SECOND_LEAGUE_WEIGHT if team_id in second_ids else 1.0
        if player.position == POSITION_GOALKEEPER:
            production = 0.08 * stats["successful_saves"] + 1.35 * stats["clean_sheets"]
        else:
            production = (
                2.8 * stats["goals"]
                + 2.2 * stats["assists"]
                + 0.24 * stats["chances_created"]
                + 0.36 * stats["successful_defenses"]
            )
        rating_part = 74.0 * rating
        ability_part = 1.15 * player.ability
        honor_part = 0.18 * honor
        score = round((rating_part + ability_part + production + honor_part) * weight, 2)
        top20.append(
            {
                "player_id": player_id,
                "label": player.display_name,
                "position": player.position,
                "team_id": team_id,
                "team_name": world["teams"][team_id]["club_en"],
                "ability": player.ability,
                "rating": rating,
                "market_value": world["players"][player_id].get("market_value"),
                "goals": stats["goals"],
                "assists": stats["assists"],
                "appearances": stats["appearances"],
                "score": score,
                "components": {
                    "rating": round(rating_part, 2),
                    "ability": round(ability_part, 2),
                    "production": round(production, 2),
                    "team_honor": round(honor_part, 2),
                    "league_weight": weight,
                },
            }
        )
    top20.sort(key=lambda item: (-item["score"], -item["rating"], -(item["goals"] + item["assists"]), -item["ability"], item["player_id"]))
    for rank, row in enumerate(top20[:20], start=1):
        row["rank"] = rank
    return {
        "top20": top20[:20],
        "competitions": compute_competition_awards(world),
    }


def compute_competition_awards(world: dict, competition_ids=None) -> Dict[str, dict]:
    if world.get("awards_rule_version") == "0.3.1":
        from football_simulator.game.awards_v031 import competition_awards
        return competition_awards(world, competition_ids)
    result: Dict[str, dict] = {}
    competition_ids = competition_ids if competition_ids is not None else [rules.PREMIER, rules.SECOND, *rules.CUP_IDS]
    for competition in competition_ids:
        from football_simulator.game import editions
        frozen = world.get("edition_archives", {}).get(editions.key(world["season_number"], competition))
        if frozen and frozen.get("awards"):
            from copy import deepcopy
            result[competition] = deepcopy(frozen["awards"])
            continue
        matches = [
            match
            for match in completed_matches(world, competition)
            if match["status"] == "completed"
        ]
        if not matches:
            continue
        team_match_counts: Dict[str, int] = {}
        for match in matches:
            team_match_counts[match["home_team_id"]] = team_match_counts.get(match["home_team_id"], 0) + 1
            team_match_counts[match["away_team_id"]] = team_match_counts.get(match["away_team_id"], 0) + 1
        max_team_matches = max(team_match_counts.values()) if team_match_counts else 0
        totals = _aggregate_player_facts(world, matches)
        rows = []
        for player_id, stats in totals.items():
            data = world["players"].get(player_id)
            if not data or not data.get("is_real") or stats["appearances"] <= 0:
                continue
            player = _player_object(world, player_id)
            rating = calculate_player_rating(_stats_namespace(player, stats), stats["appearances"])
            team_id = data.get("team_id") or next(iter(stats["teams"]), None)
            rows.append(
                {
                    "player_id": player_id,
                    "label": player.display_name,
                    "team_id": team_id,
                    "team_name": world["teams"][team_id]["club_en"] if team_id else "",
                    "position": player.position,
                    "appearances": stats["appearances"],
                    "goals": stats["goals"],
                    "assists": stats["assists"],
                    "rating": rating,
                }
            )
        threshold = max(2, math.ceil(max_team_matches / 2)) if max_team_matches else 2
        scorers = _award_winners(
            rows,
            lambda row: (-row["goals"], -row["assists"], row["appearances"]),
            value_field="goals",
            require_positive=True,
        )
        assists = _award_winners(
            rows,
            lambda row: (-row["assists"], -row["goals"], row["appearances"]),
            value_field="assists",
            require_positive=True,
        )
        eligible = [row for row in rows if row["appearances"] >= threshold]
        mvp = _award_winners(
            eligible,
            lambda row: (-row["rating"], -row["appearances"]),
            value_field="rating",
        )
        result[competition] = {
            "max_team_matches": max_team_matches,
            "mvp_minimum": threshold,
            "top_scorer": _award_payload(scorers, "goals"),
            "assist_leader": _award_payload(assists, "assists"),
            "mvp": _award_payload(mvp, "rating"),
        }
    return result


def _award_payload(rows: List[dict], value_field: str) -> List[dict]:
    return [
        {
            "player_id": row["player_id"],
            "label": row["label"],
            "team_id": row["team_id"],
            "team_name": row["team_name"],
            "appearances": row["appearances"],
            "value": row[value_field],
            "rating": row["rating"],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 升级附加赛
# ---------------------------------------------------------------------------


def _create_promotion_playoffs(world: dict) -> None:
    season = world["season"]
    if season["promotion_playoff"].get("semifinals"):
        return
    tables = league_tables(world)
    second = tables["second"]
    seeds = [row["team_id"] for row in second[2:6]]
    if len(seeds) < 4:
        return
    pairs = [(seeds[0], seeds[3]), (seeds[1], seeds[2])]
    season["promotion_playoff"] = {
        "seeds": seeds,
        "semifinals": [],
        "final": None,
        "winner": None,
    }
    for higher, lower in pairs:
        season["promotion_playoff"]["semifinals"].append(
            _two_leg_playoff_tie(world, "promotion_semifinal", higher, lower, (46, 47))
        )


def _two_leg_playoff_tie(world: dict, stage: str, higher: str, lower: str, weeks: Tuple[int, int]) -> dict:
    tie_id = f"tie-playoff-{stage}-{competitions._next_id(world, 'tie_seq'):03d}"
    first = add_match(
        world,
        competition=rules.PLAYOFF,
        stage=f"{stage}_leg_1",
        week=weeks[0],
        home_team_id=lower,
        away_team_id=higher,
        leg=1,
        tie_id=tie_id,
    )
    second = add_match(
        world,
        competition=rules.PLAYOFF,
        stage=f"{stage}_leg_2",
        week=weeks[1],
        home_team_id=higher,
        away_team_id=lower,
        leg=2,
        tie_id=tie_id,
        need_winner=True,
        aggregate_from_tie=True,
    )
    return {
        "tie_id": tie_id,
        "stage": stage,
        "teams": [higher, lower],
        "higher": higher,
        "lower": lower,
        "legs": [first, second],
        "winner": None,
        "aggregate": None,
    }


def _create_promotion_final(world: dict) -> None:
    playoff = world["season"]["promotion_playoff"]
    if not playoff.get("semifinals") or playoff.get("final"):
        return
    for tie in playoff["semifinals"]:
        competitions.resolve_two_leg_tie(world, tie)
    if not all(tie["winner"] for tie in playoff["semifinals"]):
        return
    finalists = [tie["winner"] for tie in playoff["semifinals"]]
    tables = league_tables(world)
    order = [row["team_id"] for row in tables["second"]]
    finalists.sort(key=lambda team_id: order.index(team_id) if team_id in order else 99)
    higher, lower = finalists[0], finalists[1]
    playoff["final"] = _two_leg_playoff_tie(world, "promotion_final", higher, lower, (48, 49))


def resolve_promotion_playoff(world: dict) -> Optional[str]:
    playoff = world["season"].get("promotion_playoff", {})
    final = playoff.get("final")
    if not final:
        return playoff.get("winner")
    competitions.resolve_two_leg_tie(world, final)
    if final.get("winner"):
        playoff["winner"] = final["winner"]
    return playoff.get("winner")


# ---------------------------------------------------------------------------
# 待办：转会、能力审核、选秀
# ---------------------------------------------------------------------------


def prepare_pending(save_name: str, rng: Optional[random.Random] = None) -> dict:
    """季末若尚未生成待办（例如旧流程或测试），补齐能力审核与选秀。"""
    world = store.load_world(save_name)
    rng = rng or random.SystemRandom()
    if not world["season"].get("finalized"):
        raise WorldError("赛季尚未完成正式结算，不能生成季末待办。")
    _prepare_ability_review(world, rng)
    _prepare_draft(world, rng)
    store.write_world(save_name, world)
    return world


def _player_value(world: dict, player_id: str) -> float:
    data = world["players"][player_id]
    value = data.get("market_value")
    if value is None:
        value = data.get("initial_market_value")
    if value is None:
        settlement = world["season"].get("settlements", {}).get("final", {}).get("players", {}).get(player_id)
        value = settlement.get("market_value") if settlement else None
    return float(value if value is not None else 30.0)


def _prepare_transfer_review(world: dict, rng: random.Random, window: str) -> None:
    season = world["season"]
    if season["pending"].get("transfer_review"):
        return
    if season["pending"].get("ability_review") or season["pending"].get("draft", {}).get("status") == "awaiting_input":
        return
    if not season.get("finalized") and int(season["week"]) < WINTER_SETTLEMENT_WEEK:
        return
    season["pending"]["transfer_review"] = transfers.generate_proposals(world, rng, window, _player_value)


def apply_transfer_reviews(save_name: str, decisions: Dict[str, bool]) -> dict:
    world = store.load_world(save_name)
    season = world["season"]
    pending = season["pending"].get("transfer_review") or []
    if not pending:
        raise WorldError("当前没有待处理的转会审核。")
    if season["pending"].get("ability_review") or season["pending"].get("draft", {}).get("status") == "awaiting_input":
        raise WorldError("请先完成能力审核与选秀，再处理转会。")
    applied = 0
    for item in pending:
        approved = bool(decisions.get(item["trade_id"], False))
        if not approved:
            item["status"] = "rejected"
            continue
        _apply_trade(world, item)
        item["status"] = "approved"
        applied += 1
        season["transfers"].append(
            {
                "season": season["number"],
                "week": item["week"],
                "window": item["window"],
                "trade_id": item["trade_id"],
                "team_a": item["team_a"],
                "team_b": item["team_b"],
                "players_a": item["players_a"],
                "players_b": item["players_b"],
                "labels_a": [world["players"][pid]["name"] for pid in item["players_a"]],
                "labels_b": [world["players"][pid]["name"] for pid in item["players_b"]],
                "status": "approved",
                "value_a": round(sum(_player_value(world, pid) for pid in item["players_a"]), 2),
                "value_b": round(sum(_player_value(world, pid) for pid in item["players_b"]), 2),
                "value_gap": round(abs(sum(_player_value(world, pid) for pid in item["players_a"]) - sum(_player_value(world, pid) for pid in item["players_b"])), 2),
            }
        )
        world["transfer_history"].append(season["transfers"][-1])
    season["pending"]["transfer_review"] = []
    store.write_world(save_name, world)
    return {"applied": applied, "world": world}


def _apply_trade(world: dict, item: dict) -> None:
    try:
        transfers.apply_trade(world, item, _player_value)
    except transfers.TradeError as exc:
        raise WorldError(str(exc)) from exc


def _prepare_ability_review(world: dict, rng: random.Random) -> None:
    season = world["season"]
    if season["pending"].get("ability_review"):
        return
    real_ids = [pid for pid, data in world["players"].items() if data.get("is_real")]
    if not real_ids:
        return
    count = max(1, int(len(real_ids) * 0.4))
    chosen = rng.sample(real_ids, min(count, len(real_ids)))
    proposals = []
    for player_id in chosen:
        data = world["players"][player_id]
        old = int(data["ability"])
        candidates = [value for value in range(max(60, old - 10), min(99, old + 10) + 1) if value != old]
        if not candidates:
            continue
        new = rng.choice(candidates)
        proposals.append(
            {
                "player_id": player_id,
                "label": data.get("name") or f"默认 {data['position']}",
                "position": data["position"],
                "team_id": data.get("team_id"),
                "old_ability": old,
                "new_ability": new,
                "delta": new - old,
            }
        )
    proposals.sort(key=lambda item: (item["position"], item["label"]))
    season["pending"]["ability_review"] = proposals


def apply_ability_review(save_name: str, decisions: Dict[str, bool]) -> dict:
    world = store.load_world(save_name)
    season = world["season"]
    pending = season["pending"].get("ability_review") or []
    if not pending:
        raise WorldError("当前没有待处理的能力审核。")
    applied = 0
    for item in pending:
        if decisions.get(item["player_id"], False):
            data = world["players"].get(item["player_id"])
            if data is not None:
                data["ability"] = int(item["new_ability"])
                item["accepted"] = True
                applied += 1
    season["pending"]["ability_review"] = []
    store.write_world(save_name, world)
    return {"applied": applied, "world": world}


def _prepare_draft(world: dict, rng: random.Random) -> None:
    season = world["season"]
    pending = season["pending"].get("draft") or {}
    if pending.get("status") == "awaiting_input":
        return
    if season["draft_log"] and int(season["draft_log"][-1]["season"]) == int(season["number"]):
        return
    candidate_count = rng.randint(6, 10)
    candidates = []
    index = int(world.get("draft_pool_index", 0))
    pool = world.get("draft_pool", [])
    for template in pool[index:index + candidate_count]:
        candidates.append(
            {
                "name": template["name"],
                "position": template["position"],
                "ability": rng.randint(60, 88),
            }
        )
    world["draft_pool_index"] = index + len(candidates)
    if not candidates:
        return
    tables = season.get("final_tables") or league_tables(world)
    draft_order = [row["team_id"] for row in reversed(tables["second"])] + [
        row["team_id"] for row in reversed(tables["premier"])
    ]
    season["pending"]["draft"] = {
        "status": "awaiting_input",
        "season": season["number"],
        "stage": "夏窗前选秀",
        "candidates": candidates,
        "draft_order": draft_order,
        "results": [],
    }


def apply_draft(save_name: str, *, selected_names: Optional[List[str]] = None) -> dict:
    world = store.load_world(save_name)
    season = world["season"]
    pending = season["pending"].get("draft") or {}
    if pending.get("status") != "awaiting_input":
        raise WorldError("当前没有待处理的选秀。")
    if season["pending"].get("ability_review") or season["pending"].get("transfer_review"):
        raise WorldError("请先完成能力审核与转会审核，再进行选秀。")
    all_candidates = list(pending["candidates"])
    if selected_names is None:
        candidates = list(all_candidates)
        skipped = []
    else:
        by_name = {item["name"]: item for item in all_candidates}
        missing = [name for name in selected_names if name not in by_name]
        if missing:
            raise WorldError(f"选秀候选不存在：{missing}")
        candidates = [by_name[name] for name in selected_names]
        selected_set = set(selected_names)
        skipped = [item for item in all_candidates if item["name"] not in selected_set]
    candidates.sort(key=lambda item: (-int(item["ability"]), item["name"]))
    teams = pending["draft_order"]
    results = []
    remaining = list(candidates)
    while remaining:
        picked_in_round = False
        for team_id in teams:
            if not remaining:
                break
            open_slots = _open_real_slots(world, team_id)
            eligible = [item for item in remaining if item["position"] in open_slots]
            if not eligible:
                continue
            profile = eligible[0]
            player_id = real_player_id_for(profile["name"])
            if player_id in world["players"]:
                raise WorldError(f"新秀身份重复：{profile['name']}")
            roster = world["teams"][team_id]["roster"]
            slot_index = open_slots[profile["position"]]
            old_id = roster[slot_index]["player_id"]
            # 被替换的默认球员保留注册身份：其既有逐场历史仍能定位原实体。
            if old_id in world["players"]:
                world["players"][old_id]["team_id"] = None
                world["players"][old_id]["retired_from_roster"] = True
            world["players"][player_id] = {
                "player_id": player_id,
                "name": profile["name"],
                "position": profile["position"],
                "ability": int(profile["ability"]),
                "is_real": True,
                "slot_number": roster[slot_index]["slot"],
                "team_id": team_id,
                "initial_market_value": 30.0,
                "market_value": 30.0,
            }
            if world.get("edition_archive_version"):
                world["players"][player_id]["draft_origin"] = {"season": int(season["number"]), "pick": len(results) + 1, "team_id": team_id}
            roster[slot_index]["player_id"] = player_id
            results.append(
                {
                    "team_id": team_id,
                    "player_id": player_id,
                    "pick": len(results) + 1,
                    "name": profile["name"],
                    "position": profile["position"],
                    "ability": int(profile["ability"]),
                    "market_value": 30.0,
                }
            )
            remaining.remove(profile)
            picked_in_round = True
        if not picked_in_round:
            break
    season["pending"]["draft"] = {}
    no_slot = [
        {**item, "reason": "no_open_slot"}
        for item in remaining
    ]
    skipped_rows = [
        {**item, "reason": "user_skipped"}
        for item in skipped
    ]
    season["draft_log"].append(
        {
            "season": season["number"],
            "results": results,
            "undrafted": skipped_rows + no_slot,
            "skipped": skipped_rows,
            "no_slot": no_slot,
            "selected_count": len(candidates),
            "candidate_count": len(all_candidates),
        }
    )
    store.write_world(save_name, world)
    return {
        "drafted": results,
        "undrafted": skipped_rows + no_slot,
        "skipped": skipped_rows,
        "no_slot": no_slot,
        "world": world,
    }


def _open_real_slots(world: dict, team_id: str) -> Dict[str, int]:
    """返回可升级为真实球员的默认球员槽位（每个位置取第一个）。"""
    open_slots: Dict[str, int] = {}
    for index, entry in enumerate(world["teams"][team_id]["roster"]):
        player = world["players"][entry["player_id"]]
        if player.get("is_real"):
            continue
        open_slots.setdefault(player["position"], index)
    return open_slots


# ---------------------------------------------------------------------------
# 新赛季
# ---------------------------------------------------------------------------


def start_next_season(save_name: str, *, rng: Optional[random.Random] = None) -> dict:
    world = store.load_world(save_name)
    season = world["season"]
    if not season.get("season_complete"):
        raise WorldError("当前赛季尚未结束，不能开始下一赛季。")
    pending = season["pending"]
    if pending.get("ability_review") or pending.get("transfer_review") or pending.get("draft", {}).get("status") == "awaiting_input":
        raise WorldError("还有未完成的季末待办，请先处理。")
    if not world.get("archives"):
        _archive_season(world)
    previous = world["archives"][-1]
    rng = rng or random.SystemRandom()
    transition = season.get("transition") or {}
    next_premier = transition.get("next_premier") or season["league"]["premier"]
    next_second = transition.get("next_second") or season["league"]["second"]
    for team_id, team in world["teams"].items():
        team["division"] = "一级联赛" if team_id in next_premier else "次级联赛"
    world["season_number"] = int(world["season_number"]) + 1
    divisions = {"premier": list(next_premier), "second": list(next_second)}
    world["season"] = _new_season_state(
        world["season_number"], world["teams"], world["players"], divisions, previous
    )
    _create_league_fixtures(world, rng)
    competitions.initialize_cups(world, world["season_number"], rng)
    store.write_world(save_name, world)
    return store.load_world(save_name)


def _archive_season(world: dict) -> None:
    season = world["season"]
    if any(int(archive["season_number"]) == int(season["number"]) for archive in world["archives"]):
        return
    if not season.get("finalized"):
        _finalize_sport(world, random.SystemRandom())
    resolve_promotion_playoff(world)
    tables = season.get("final_tables") or league_tables(world)
    transition = season.get("transition") or compute_transition(world, tables)
    season["transition"] = transition
    premier_order = [row["team_id"] for row in tables["premier"]]
    second_order = [row["team_id"] for row in tables["second"]]
    archive = {
        "season_number": season["number"],
        "premier_order": premier_order,
        "second_order": second_order,
        "premier_table": tables["premier"],
        "second_table": tables["second"],
        "honor_rows": season.get("honor_rows", []),
        "honor_totals": season.get("honor_totals", {}),
        "cup_champions": {
            cup_id: state.get("champion")
            for cup_id, state in season.get("cups", {}).items()
            if state.get("champion")
        },
        "cup_placements": {
            cup_id: state.get("placements", {})
            for cup_id, state in season.get("cups", {}).items()
        },
        "championships": dict(season.get("championships") or {}),
        "awards": season.get("awards", {}),
        "settlements": season.get("settlements", {}),
        "transition": transition,
        "transfers": season.get("transfers", []),
        "draft": season.get("draft_log", []),
        "top20": (season.get("awards", {}) or {}).get("top20", []),
    }
    if world.get("honor_scoring_profile"):
        archive["honor_scoring_profile"] = world["honor_scoring_profile"]
    world["archives"].append(archive)


# ---------------------------------------------------------------------------
# 故事事件（事实驱动，持久化去重）
# ---------------------------------------------------------------------------


def ranking_movement_text(new_rank: int, gap: int) -> str:
    if new_rank == 1:
        return "，升至榜首。"
    if gap == 0:
        return "，与榜首同分。"
    return f"，落后榜首 {gap} 分。"


def _generate_week_events(world: dict, completed_ids: List[str], completed_week: int) -> List[dict]:
    """把本周真实事实组织成可去重的故事事件。

    事件 ID 稳定；刷新/读档不会重复生成。只写可证明的结论：冠军来自已保存
    的最终结果；出线来自已完成阶段的签表；排名变化来自真实积分；逆转来自
    真实进球序列。没有数学依据时只描述事实，不宣称“已出线”。
    """
    events = list(world.get("story_events", []))
    known = {event["event_id"] for event in events}
    season = world["season"]
    week = int(completed_week)
    season_number = int(world["season_number"])

    def add(event: dict) -> None:
        if event["event_id"] in known:
            return
        known.add(event["event_id"])
        events.append(event)

    # 1) 冠军揭晓（只写已保存的冠军）
    for cup_id, state in season.get("cups", {}).items():
        champion = state.get("champion")
        if not champion:
            continue
        final_match_id = _final_match_id(world, cup_id)
        add(
            {
                "event_id": f"champion:{season_number}:{cup_id}",
                "type": "champion",
                "season": season_number,
                "week": week,
                "competition": cup_id,
                "team_id": champion,
                "match_id": final_match_id,
                "text": f"{_team_zh(world, champion)} 赢得{rules.display_name(cup_id, 'en')}冠军。",
                "payload": {"competition": cup_id, "route": ["competition", {"competition_id": cup_id}]},
                "rules_version": rules.RULES_VERSION,
            }
        )

    # 2) 已完成的出线结论（阶段完成后才写）
    starpath = season.get("cups", {}).get("starpath")
    if starpath and starpath.get("stage_done", {}).get("swiss"):
        qualified = starpath.get("qualified", [])
        if qualified:
            add(
                {
                    "event_id": f"qualification:{season_number}:starpath:swiss",
                    "type": "qualification",
                    "season": season_number,
                    "week": week,
                    "competition": "starpath",
                    "team_id": None,
                    "match_id": None,
                    "text": f"{rules.display_name('starpath', 'en')}瑞士轮结束：" + "、".join(_team_zh(world, tid) for tid in qualified) + " 晋级八强。",
                    "payload": {"competition": "starpath", "route": ["competition", {"competition_id": "starpath"}]},
                    "rules_version": rules.RULES_VERSION,
                }
            )
    aureole = season.get("cups", {}).get("aureole")
    if aureole and aureole.get("stage_done", {}).get("groups"):
        add(
            {
                "event_id": f"qualification:{season_number}:aureole:groups",
                "type": "qualification",
                "season": season_number,
                "week": week,
                "competition": "aureole",
                "team_id": None,
                "match_id": None,
                "text": f"{rules.display_name('aureole', 'zh')}小组赛结束：四个小组前二进入两回合八强。",
                "payload": {"competition": "aureole", "route": ["competition", {"competition_id": "aureole"}]},
                "rules_version": rules.RULES_VERSION,
            }
        )
    ascendant = season.get("cups", {}).get("ascendant")
    if ascendant and ascendant.get("stage_done", {}).get("playin"):
        add(
            {
                "event_id": f"qualification:{season_number}:ascendant:playin",
                "type": "qualification",
                "season": season_number,
                "week": week,
                "competition": "ascendant",
                "team_id": None,
                "match_id": None,
                "text": "凌越杯突围赛结束：三名突围者与五支直通队组成八强。",
                "payload": {"competition": "ascendant", "route": ["competition", {"competition_id": "ascendant"}]},
                "rules_version": rules.RULES_VERSION,
            }
        )

    # 3) 顶级联赛排名变化（真实积分，赛前/赛后对照）
    current_rows = league_tables(world)["premier"]
    previous_rows = _table_before_week(world, week - 1)
    if previous_rows and any(int(row.get("played") or 0) > 0 for row in previous_rows):
        previous_rank = {row["team_id"]: index + 1 for index, row in enumerate(previous_rows)}
        mover = None
        for index, row in enumerate(current_rows[:6], start=1):
            old_rank = previous_rank.get(row["team_id"])
            if old_rank is None or old_rank <= index:
                continue
            delta = old_rank - index
            if mover is None or delta > mover[0]:
                mover = (delta, row["team_id"], old_rank, index, row["points"])
        if mover:
            _, team_id, old_rank, new_rank, points = mover
            leader_points = current_rows[0]["points"] if current_rows else 0
            gap = leader_points - points
            movement = ranking_movement_text(new_rank, gap)
            add(
                {
                    "event_id": f"ranking:{season_number}:{week}:{team_id}",
                    "type": "ranking",
                    "season": season_number,
                    "week": week,
                    "competition": rules.PREMIER,
                    "team_id": team_id,
                    "match_id": None,
                    "text": f"{_team_zh(world, team_id)} 升至第 {new_rank}（原第 {old_rank}），积 {points} 分" + movement,
                    "payload": {"route": ["competition", {"competition_id": rules.PREMIER}]},
                    "rules_version": rules.RULES_VERSION,
                }
            )

    # 4) 真实进球序列支持的逆转
    comeback = _comeback_event(world, completed_ids, week, season_number)
    if comeback:
        add(comeback)

    # 5) 升降级（第 49 周正式结算后）
    transition = season.get("transition") or {}
    if season.get("finalized") and transition:
        for team_id in transition.get("promoted", []):
            add(
                {
                    "event_id": f"transition:{season_number}:promoted:{team_id}",
                    "type": "transition",
                    "season": season_number,
                    "week": week,
                    "competition": rules.PLAYOFF,
                    "team_id": team_id,
                    "match_id": None,
                    "text": f"{_team_zh(world, team_id)} 获得下赛季一级联赛资格。",
                    "payload": {"route": ["season", {}]},
                    "rules_version": rules.RULES_VERSION,
                }
            )
        for team_id in transition.get("relegated", []):
            add(
                {
                    "event_id": f"transition:{season_number}:relegated:{team_id}",
                    "type": "transition",
                    "season": season_number,
                    "week": week,
                    "competition": rules.PREMIER,
                    "team_id": team_id,
                    "match_id": None,
                    "text": f"{_team_zh(world, team_id)} 降入次级联赛。",
                    "payload": {"route": ["season", {}]},
                    "rules_version": rules.RULES_VERSION,
                }
            )

    # 6) 比赛结果事件（周报/比赛证据；首页只在不影响重点时补充）
    for match_id in completed_ids:
        fixture = get_fixture(world, match_id)
        if fixture["status"] != "completed":
            continue
        home = world["teams"][fixture["home_team_id"]]
        away = world["teams"][fixture["away_team_id"]]
        home_name = home.get("name_zh") or home["club_en"]
        away_name = away.get("name_zh") or away["club_en"]
        home_goals = int(fixture.get("home_goals", 0)) + int(fixture.get("home_goals_et", 0))
        away_goals = int(fixture.get("away_goals", 0)) + int(fixture.get("away_goals_et", 0))
        event_id = f"result:{season_number}:{match_id}"
        if event_id in known:
            continue
        if home_goals == 0 and away_goals == 0:
            text = f"{home_name} 0–0 {away_name}：双方均未破门。"
        elif home_goals == away_goals:
            text = f"{home_name} {home_goals}–{away_goals} {away_name}：双方战平。"
        else:
            winner_name = home_name if home_goals > away_goals else away_name
            loser_name = away_name if home_goals > away_goals else home_name
            text = f"{winner_name} {max(home_goals, away_goals)}–{min(home_goals, away_goals)} 击败 {loser_name}。"
        if fixture.get("decided_by") == "pk":
            text += f" 点球 {fixture.get('home_pk')}–{fixture.get('away_pk')} 决胜。"
        elif fixture.get("extra_time_played"):
            text += " 加时赛决出胜负。"
        add(
            {
                "event_id": event_id,
                "type": "match_result",
                "season": season_number,
                "week": week,
                "competition": fixture["competition"],
                "match_id": match_id,
                "team_id": fixture.get("winner_team_id"),
                "text": text,
                "payload": {
                    "home_team_id": fixture["home_team_id"],
                    "away_team_id": fixture["away_team_id"],
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    "venue_mode": fixture["venue_mode"],
                    "decided_by": fixture.get("decided_by"),
                },
                "rules_version": rules.RULES_VERSION,
            }
        )
    return events


def _team_zh(world: dict, team_id: str) -> str:
    team = world["teams"].get(team_id) or {}
    return team.get("club_en") or "未知球队"


def _final_match_id(world: dict, cup_id: str) -> Optional[str]:
    candidates = [
        fixture["match_id"]
        for fixture in world["season"]["fixtures"]
        if fixture["competition"] == cup_id
        and fixture["status"] == "completed"
        and fixture.get("stage") in ("final", "final_leg_2", "grand_final")
    ]
    return candidates[-1] if candidates else None


def _table_before_week(world: dict, week: int) -> List[dict]:
    if week <= 0:
        return []
    rank = competitions.previous_ranks(world)
    team_ids = world["season"]["league"]["premier"]
    matches = [match for match in completed_matches(world, rules.PREMIER) if int(match["week"]) < week]
    return standings_from_matches(team_ids, matches, previous_rank=rank)


def _comeback_event(world: dict, completed_ids: List[str], week: int, season_number: int) -> Optional[dict]:
    for match_id in completed_ids:
        fixture = get_fixture(world, match_id)
        if fixture["status"] != "completed" or not fixture.get("winner_team_id"):
            continue
        winner = fixture["winner_team_id"]
        home = fixture["home_team_id"]
        score = [0, 0]
        trailed = False
        for event in sorted(fixture.get("events", []), key=lambda item: int(item.get("minute") or 0)):
            if event.get("type") != "goal":
                continue
            if event.get("team_id") == home:
                score[0] += 1
            else:
                score[1] += 1
            winner_side = 0 if winner == home else 1
            if score[winner_side] < score[1 - winner_side]:
                trailed = True
        if not trailed:
            continue
        home_name = _team_zh(world, fixture["home_team_id"])
        away_name = _team_zh(world, fixture["away_team_id"])
        winner_name = _team_zh(world, winner)
        home_goals = int(fixture.get("home_goals", 0)) + int(fixture.get("home_goals_et", 0))
        away_goals = int(fixture.get("away_goals", 0)) + int(fixture.get("away_goals_et", 0))
        winner_goals = home_goals if winner == fixture["home_team_id"] else away_goals
        loser_goals = away_goals if winner == fixture["home_team_id"] else home_goals
        return {
            "event_id": f"comeback:{season_number}:{match_id}",
            "type": "comeback",
            "season": season_number,
            "week": week,
            "competition": fixture["competition"],
            "team_id": winner,
            "match_id": match_id,
            "text": f"{winner_name} 在比赛中一度落后，最终以 {winner_goals}–{loser_goals} 完成逆转（{home_name} vs {away_name}）。",
            "payload": {"route": ["match", {"match_id": match_id}]},
            "rules_version": rules.RULES_VERSION,
        }
    return None
