"""新存档配置、球队/球员身份与初始阵容。

只支持新存档：这里不读取任何旧存档或旧 schema。总配置只提供球队与真实
球员模板；每份新存档用注入的随机源重新分配联赛与球员，但球队稳定 ID 与
球场绑定只由俱乐部身份决定，不受随机分区影响。
"""

from __future__ import annotations

import json
import random
from typing import Dict, List, Optional, Tuple

from football_simulator import runtime
from football_simulator.game.models import (
    FORMATION_RULES,
    POSITION_DEFENDER,
    POSITION_FORWARD,
    POSITION_GOALKEEPER,
    POSITION_MIDFIELDER,
    POSITIONS,
    default_player_id,
    real_player_id_for,
    team_id_for,
)
from football_simulator.game.rules import stadiums_by_club

DEFAULT_ABILITY = 50
REAL_ABILITY_MIN = 60
REAL_ABILITY_MAX = 88
STAR_PLAYER_COUNT = 50


class ConfigError(ValueError):
    pass


def load_master_config() -> dict:
    path = runtime.shared_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"未找到总配置文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"总配置 JSON 无效：{path}") from exc
    _validate_master_config(data, path)
    return data


def _validate_master_config(data: object, path) -> None:
    if not isinstance(data, dict):
        raise ConfigError(f"总配置必须是 JSON 对象：{path}")
    premier = data.get("premier_teams")
    second = data.get("second_division_teams")
    if not isinstance(premier, list) or len(premier) != 20:
        raise ConfigError("总配置必须提供 20 支一级联赛球队。")
    if not isinstance(second, list) or len(second) != 20:
        raise ConfigError("总配置必须提供 20 支次级联赛球队。")
    all_teams = [*premier, *second]
    if len(set(all_teams)) != 40:
        raise ConfigError("40 支球队名必须唯一。")
    stadiums = stadiums_by_club()
    missing = [team for team in all_teams if team not in stadiums]
    if missing:
        raise ConfigError(f"以下球队缺少已确认主场：{missing}")
    players = data.get("real_players")
    if not isinstance(players, list) or len(players) < STAR_PLAYER_COUNT:
        raise ConfigError("总配置必须至少提供 50 名真实球员。")
    for index, player in enumerate(players):
        if not isinstance(player, dict) or player.get("position") not in POSITIONS:
            raise ConfigError(f"real_players[{index}] 位置无效。")
        if not isinstance(player.get("name"), str) or not player["name"].strip():
            raise ConfigError(f"real_players[{index}] 姓名无效。")


def default_ability() -> int:
    return DEFAULT_ABILITY


def build_teams_and_players(
    config: dict,
    rng: random.Random,
) -> Tuple[Dict[str, dict], Dict[str, dict], List[dict], Dict[str, str]]:
    """返回 (teams, players, draft_pool, club_names)。

    - 40 队各 11 个稳定槽位，默认球员 ID 含球队/位置/槽位，FW1 与 GK1
      永不共享身份；
    - 50 名真实球员随机分配到一级联赛球队的空缺位置；
    - 其余真实球员模板进入选秀池（200 人），选中时才生成能力与初始身价。
    """
    club_names = [*config["premier_teams"], *config["second_division_teams"]]
    shuffled = list(club_names)
    rng.shuffle(shuffled)
    premier_names = shuffled[:20]
    second_names = shuffled[20:]
    name_zh = dict(config.get("team_chinese_names", {}))
    stadiums = stadiums_by_club()

    teams: Dict[str, dict] = {}
    players: Dict[str, dict] = {}
    used_ids = set()
    for club in [*premier_names, *second_names]:
        team_id = team_id_for(club)
        if team_id in used_ids:
            raise ConfigError(f"球队稳定 ID 冲突：{team_id}")
        used_ids.add(team_id)
        stadium = stadiums[club]
        division = "一级联赛" if club in premier_names else "次级联赛"
        roster: List[dict] = []
        for position in POSITIONS:
            for slot in range(1, FORMATION_RULES[position] + 1):
                player_id = default_player_id(team_id, position, slot)
                players[player_id] = {
                    "player_id": player_id,
                    "name": None,
                    "position": position,
                    "ability": DEFAULT_ABILITY,
                    "is_real": False,
                    "slot_number": slot,
                    "team_id": team_id,
                    "initial_market_value": None,
                    "market_value": None,
                }
                roster.append({"player_id": player_id, "position": position, "slot": slot})
        teams[team_id] = {
            "team_id": team_id,
            "club_en": club,
            "name_zh": name_zh.get(club, club),
            "division": division,
            "stadium_id": stadium["stadium_id"],
            "roster": roster,
        }

    # 真实球员模板：前 50 名进入一级联赛，其余进入选秀池。
    templates = [dict(item) for item in config["real_players"]]
    rng.shuffle(templates)
    star_templates = templates[:STAR_PLAYER_COUNT]
    draft_templates = templates[STAR_PLAYER_COUNT:]

    vacancies = _premier_vacancies(teams, premier_names)
    for template in star_templates:
        position = template["position"]
        candidates = [team_id for team_id in vacancies if vacancies[team_id].get(position)]
        if not candidates:
            continue
        team_id = rng.choice(candidates)
        slot_index = vacancies[team_id][position].pop()
        roster_entry = teams[team_id]["roster"][slot_index]
        old_id = roster_entry["player_id"]
        new_id = real_player_id_for(template["name"])
        players.pop(old_id, None)
        players[new_id] = {
            "player_id": new_id,
            "name": template["name"],
            "position": position,
            "ability": rng.randint(REAL_ABILITY_MIN, REAL_ABILITY_MAX),
            "is_real": True,
            "slot_number": roster_entry["slot"],
            "team_id": team_id,
            "initial_market_value": None,
            "market_value": None,
        }
        roster_entry["player_id"] = new_id

    draft_pool = [
        {
            "name": template["name"],
            "position": template["position"],
        }
        for template in draft_templates
    ]
    return teams, players, draft_pool, {"premier": premier_names, "second": second_names}


def _premier_vacancies(teams: Dict[str, dict], premier_names: List[str]) -> Dict[str, Dict[str, List[int]]]:
    vacancies: Dict[str, Dict[str, List[int]]] = {}
    for club in premier_names:
        team_id = team_id_for(club)
        by_position: Dict[str, List[int]] = {position: [] for position in POSITIONS}
        for index, entry in enumerate(teams[team_id]["roster"]):
            by_position[entry["position"]].append(index)
        vacancies[team_id] = by_position
    return vacancies


def roster_player_ids(team: dict) -> List[str]:
    return [entry["player_id"] for entry in team["roster"]]


def team_objects(teams: Dict[str, dict], players: Dict[str, dict]):
    """构造引擎需要的不可变 Team/Player 对象。"""
    from football_simulator.game.models import Player, Team

    result = {}
    for team_id, team in teams.items():
        roster = tuple(
            Player(
                player_id=entry["player_id"],
                name=players[entry["player_id"]]["name"],
                position=players[entry["player_id"]]["position"],
                ability=int(players[entry["player_id"]]["ability"]),
                is_real=bool(players[entry["player_id"]]["is_real"]),
                slot_number=int(players[entry["player_id"]]["slot_number"]),
                initial_market_value=players[entry["player_id"]].get("initial_market_value"),
                team_id=team_id,
            )
            for entry in team["roster"]
        )
        result[team_id] = Team(
            team_id=team_id,
            name=team["club_en"],
            name_zh=team.get("name_zh", team["club_en"]),
            division=team["division"],
            stadium_id=team["stadium_id"],
            roster=roster,
        )
    return result


def team_by_player(players: Dict[str, dict]) -> Dict[str, str]:
    return {pid: data["team_id"] for pid, data in players.items()}
