import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from football_simulator.models import (
    FORMATION_RULES,
    POSITION_DEFENDER,
    POSITION_FORWARD,
    POSITION_GOALKEEPER,
    POSITION_MIDFIELDER,
    Player,
    Team,
)
from football_simulator.runtime import shared_config_path
from football_simulator.runtime import save_config_path


DEFAULT_PLAYER_ABILITY = 50
REAL_PLAYER_ABILITY_MIN = 60
REAL_PLAYER_ABILITY_MAX = 88


@dataclass(frozen=True)
class RealPlayerTemplate:
    name: str
    position: str


@dataclass(frozen=True)
class RealPlayerProfile:
    name: str
    position: str
    ability: int
    initial_market_value: Optional[float] = None


@dataclass(frozen=True)
class SaveConfig:
    save_name: str
    default_player_ability: int
    real_player_ability_min: int
    real_player_ability_max: int
    premier_teams: List[str]
    second_division_teams: List[str]
    team_chinese_names: Dict[str, str]
    real_players: List[RealPlayerTemplate]
    draft_players: List[RealPlayerTemplate]


def load_save_config(save_name: str) -> SaveConfig:
    config_path = ensure_save_config(save_name)
    return load_save_config_from_path(config_path, save_name)


def ensure_save_config(save_name: str, *, force: bool = False) -> Path:
    config_path = save_config_path(save_name)
    if config_path.exists() and not force:
        return config_path
    source_data = json.loads(shared_config_path().read_text(encoding="utf-8"))
    save_data = _build_randomized_save_config(source_data, random.SystemRandom())
    config_path.write_text(
        json.dumps(save_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return config_path


def load_save_config_from_path(config_path: Path, save_name: str = "default") -> SaveConfig:
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"未找到总配置文件：{config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"总配置 JSON 无效：{config_path}") from exc

    return _validate_save_config(data, config_path, save_name)


def _build_randomized_save_config(raw_data: object, rng: random.Random) -> dict:
    if not isinstance(raw_data, dict):
        raise ValueError(f"总配置必须是 JSON 对象：{shared_config_path()}")
    premier_teams = raw_data.get("premier_teams", raw_data.get("teams"))
    second_teams = raw_data.get("second_division_teams")
    if not isinstance(premier_teams, list) or not isinstance(second_teams, list):
        raise ValueError("总配置必须提供 premier_teams 和 second_division_teams。")
    all_teams = [str(team_name).strip() for team_name in [*premier_teams, *second_teams] if str(team_name).strip()]
    if len(all_teams) != 40 or len(set(all_teams)) != 40:
        raise ValueError("总配置必须提供 40 个唯一球队。")

    real_players = raw_data.get("real_players")
    if not isinstance(real_players, list) or len(real_players) < 50:
        raise ValueError("总配置必须至少提供 50 名真实球员。")

    shuffled_teams = list(all_teams)
    player_items = [dict(player) for player in real_players if isinstance(player, dict)]
    rng.shuffle(shuffled_teams)
    shuffled_players = _shuffle_players_with_initial_capacity(player_items, rng)

    save_data = {
        key: value
        for key, value in raw_data.items()
        if key not in {"teams", "premier_teams", "second_division_teams", "real_players"}
    }
    save_data["premier_teams"] = shuffled_teams[:20]
    save_data["second_division_teams"] = shuffled_teams[20:]
    save_data["real_players"] = shuffled_players
    return save_data


def _shuffle_players_with_initial_capacity(players: List[dict], rng: random.Random) -> List[dict]:
    shuffled = list(players)
    rng.shuffle(shuffled)
    capacities = {
        position: 20 * required_count
        for position, required_count in FORMATION_RULES.items()
    }
    first_batch: List[dict] = []
    reserve: List[dict] = []
    for player in shuffled:
        position = player.get("position")
        if len(first_batch) < 50 and position in capacities and capacities[position] > 0:
            first_batch.append(player)
            capacities[position] -= 1
        else:
            reserve.append(player)
    if len(first_batch) < 50:
        shortage = 50 - len(first_batch)
        first_batch.extend(reserve[:shortage])
        reserve = reserve[shortage:]
    return [*first_batch, *reserve]


def create_league_teams_from_save(
    config: SaveConfig,
    rng: random.Random,
    premier_team_names: Optional[List[str]] = None,
    second_team_names: Optional[List[str]] = None,
    real_player_pool: Optional[List[RealPlayerProfile]] = None,
    assigned_real_players: Optional[Dict[str, List[Player]]] = None,
) -> Tuple[List[Team], List[Team]]:
    premier_names = premier_team_names or config.premier_teams
    second_names = second_team_names or config.second_division_teams

    team_slots = {
        team_name: _build_default_roster(team_name, config.default_player_ability)
        for team_name in [*premier_names, *second_names]
    }
    all_team_names = [*premier_names, *second_names]
    vacancies = {
        team_name: _build_position_vacancies(team_slots[team_name])
        for team_name in (all_team_names if assigned_real_players else premier_names)
    }

    real_players = list(real_player_pool or build_real_player_pool(config, rng))
    assigned_names = set()
    if assigned_real_players:
        for team_name, assigned_players in assigned_real_players.items():
            if team_name not in team_slots:
                continue
            for player in assigned_players:
                if team_name not in vacancies or not vacancies[team_name][player.position]:
                    continue
                slot_index = vacancies[team_name][player.position].pop()
                team_slots[team_name][slot_index] = Player(
                    player_id=player.player_id,
                    name=player.name,
                    position=player.position,
                    ability=player.ability,
                    is_real=True,
                    slot_number=team_slots[team_name][slot_index].slot_number,
                    initial_market_value=player.initial_market_value,
                )
                if player.name:
                    assigned_names.add(player.name)
        real_players = [profile for profile in real_players if profile.name not in assigned_names]
    rng.shuffle(real_players)

    for player_profile in real_players:
        candidate_teams = [
            team_name
            for team_name, position_vacancies in vacancies.items()
            if position_vacancies[player_profile.position]
        ]
        if not candidate_teams:
            continue

        team_name = rng.choice(candidate_teams)
        slot_index = vacancies[team_name][player_profile.position].pop()
        slot_template = team_slots[team_name][slot_index]
        team_slots[team_name][slot_index] = Player(
            player_id=slot_template.player_id,
            name=player_profile.name,
            position=player_profile.position,
            ability=player_profile.ability,
            is_real=True,
            slot_number=slot_template.slot_number,
            initial_market_value=player_profile.initial_market_value,
        )

    premier_teams = [
        Team(name=team_name, roster=tuple(team_slots[team_name]), division="一级联赛")
        for team_name in premier_names
    ]
    second_teams = [
        Team(name=team_name, roster=tuple(team_slots[team_name]), division="次级联赛")
        for team_name in second_names
    ]
    return premier_teams, second_teams


def create_teams_from_save(config: SaveConfig, rng: random.Random) -> List[Team]:
    premier_teams, _ = create_league_teams_from_save(config, rng)
    return premier_teams


def build_real_player_pool(config: SaveConfig, rng: random.Random) -> List[RealPlayerProfile]:
    real_ability_min = max(config.default_player_ability + 1, config.real_player_ability_min)
    return [
        RealPlayerProfile(
            name=player_template.name,
            position=player_template.position,
            ability=rng.randint(real_ability_min, config.real_player_ability_max),
        )
        for player_template in config.real_players
    ]


def _validate_save_config(raw_data: object, config_path: Path, save_name: str) -> SaveConfig:
    if not isinstance(raw_data, dict):
        raise ValueError(f"总配置必须是 JSON 对象：{config_path}")

    default_player_ability = _optional_int_in_range(
        raw_data.get("default_player_ability"), "default_player_ability", config_path, DEFAULT_PLAYER_ABILITY
    )
    real_player_ability_min = _optional_int_in_range(
        raw_data.get("real_player_ability_min"), "real_player_ability_min", config_path, REAL_PLAYER_ABILITY_MIN
    )
    real_player_ability_max = _optional_int_in_range(
        raw_data.get("real_player_ability_max"), "real_player_ability_max", config_path, REAL_PLAYER_ABILITY_MAX
    )
    if real_player_ability_min > real_player_ability_max:
        raise ValueError(
            f"总配置无效：{config_path}，真实球员最低能力不能大于最高能力。"
        )
    if real_player_ability_max <= default_player_ability:
        raise ValueError(
            f"总配置无效：{config_path}，真实球员必须强于默认球员。"
        )

    premier_teams = raw_data.get("premier_teams", raw_data.get("teams"))
    second_division_teams = raw_data.get("second_division_teams")
    if not isinstance(premier_teams, list):
        raise ValueError(f"总配置无效：{config_path}，premier_teams 必须是列表。")
    if not isinstance(second_division_teams, list):
        raise ValueError(f"总配置无效：{config_path}，second_division_teams 必须是列表。")
    cleaned_premier_names = [
        _require_non_empty_string(team_name, "premier_teams[]", config_path).strip()
        for team_name in premier_teams
    ]
    cleaned_second_names = [
        _require_non_empty_string(team_name, "second_division_teams[]", config_path).strip()
        for team_name in second_division_teams
    ]
    if len(cleaned_premier_names) != 20:
        raise ValueError(f"总配置无效：{config_path}，一级联赛必须正好提供 20 个球队名。")
    if len(cleaned_second_names) != 20:
        raise ValueError(f"总配置无效：{config_path}，次级联赛必须正好提供 20 个球队名。")
    all_team_names = cleaned_premier_names + cleaned_second_names
    if len(set(all_team_names)) != len(all_team_names):
        raise ValueError(f"总配置无效：{config_path}，40 个球队名必须全部唯一。")

    raw_real_players = raw_data.get("real_players")
    if not isinstance(raw_real_players, list):
        raise ValueError(f"总配置无效：{config_path}，real_players 必须是列表。")

    if len(raw_real_players) < 50:
        raise ValueError(f"总配置无效：{config_path}，必须至少提供 50 名真实球员。")

    real_players: List[RealPlayerTemplate] = []
    draft_players: List[RealPlayerTemplate] = []
    position_counts: Counter[str] = Counter()
    position_capacity = {
        position: 20 * required_count
        for position, required_count in FORMATION_RULES.items()
    }

    for index, raw_player in enumerate(raw_real_players):
        if not isinstance(raw_player, dict):
            raise ValueError(
                f"总配置无效：{config_path}，real_players[{index}] 必须是对象。"
            )
        name = _require_non_empty_string(raw_player.get("name"), f"real_players[{index}].name", config_path)
        position = _require_non_empty_string(
            raw_player.get("position"), f"real_players[{index}].position", config_path
        )
        if position not in FORMATION_RULES:
            raise ValueError(
                f"总配置无效：{config_path}，real_players[{index}] 使用了不支持的位置 '{position}'。"
            )
        if index < 50:
            position_counts[position] += 1
        if index < 50 and position_counts[position] > position_capacity[position]:
            raise ValueError(
                f"总配置无效：{config_path}，{position} 位置的真实球员数量过多。"
            )
        target = real_players if index < 50 else draft_players
        target.append(RealPlayerTemplate(name=name, position=position))

    team_chinese_names = _load_team_chinese_names(raw_data, all_team_names, config_path)

    return SaveConfig(
        save_name=save_name,
        default_player_ability=default_player_ability,
        real_player_ability_min=real_player_ability_min,
        real_player_ability_max=real_player_ability_max,
        premier_teams=cleaned_premier_names,
        second_division_teams=cleaned_second_names,
        team_chinese_names=team_chinese_names,
        real_players=real_players,
        draft_players=draft_players,
    )


def load_team_chinese_names(save_name: Optional[str] = None) -> Dict[str, str]:
    config_path = save_config_path(save_name) if save_name else shared_config_path()
    if save_name and not config_path.exists():
        config_path = ensure_save_config(save_name)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    team_names = [
        name
        for key in ("premier_teams", "second_division_teams")
        for name in data.get(key, [])
        if isinstance(name, str)
    ]
    try:
        return _load_team_chinese_names(data, team_names, config_path)
    except ValueError:
        return {}


def _load_team_chinese_names(raw_data: dict, team_names: List[str], config_path: Path) -> Dict[str, str]:
    raw_names = raw_data.get("team_chinese_names", {})
    if raw_names is None:
        return {}
    if not isinstance(raw_names, dict):
        raise ValueError(f"总配置无效：{config_path}，team_chinese_names 必须是对象。")
    team_name_set = set(team_names)
    cleaned: Dict[str, str] = {}
    for english_name, chinese_name in raw_names.items():
        if not isinstance(english_name, str) or english_name not in team_name_set:
            continue
        if isinstance(chinese_name, str) and chinese_name.strip():
            cleaned[english_name] = chinese_name.strip()
    return cleaned


def _require_non_empty_string(value: object, field_name: str, config_path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"总配置无效：{config_path}，{field_name} 必须是非空字符串。")
    return value


def _optional_int_in_range(
    value: object,
    field_name: str,
    config_path: Path,
    default_value: int,
) -> int:
    if value is None:
        return default_value
    if not isinstance(value, int):
        raise ValueError(f"总配置无效：{config_path}，{field_name} 必须是整数。")
    if value < 1 or value > 99:
        raise ValueError(f"总配置无效：{config_path}，{field_name} 必须在 1 到 99 之间。")
    return value


def _build_default_roster(team_name: str, default_player_ability: int) -> List[Player]:
    roster: List[Player] = []
    team_slug = _slugify(team_name)
    roster.extend(
        Player(
            player_id=f"{team_slug}-{POSITION_GOALKEEPER.lower()}-{slot_number}",
            name=None,
            position=POSITION_GOALKEEPER,
            ability=default_player_ability,
            is_real=False,
            slot_number=slot_number,
        )
        for slot_number in range(1, FORMATION_RULES[POSITION_GOALKEEPER] + 1)
    )
    roster.extend(
        Player(
            player_id=f"{team_slug}-{POSITION_DEFENDER.lower()}-{slot_number}",
            name=None,
            position=POSITION_DEFENDER,
            ability=default_player_ability,
            is_real=False,
            slot_number=slot_number,
        )
        for slot_number in range(1, FORMATION_RULES[POSITION_DEFENDER] + 1)
    )
    roster.extend(
        Player(
            player_id=f"{team_slug}-{POSITION_MIDFIELDER.lower()}-{slot_number}",
            name=None,
            position=POSITION_MIDFIELDER,
            ability=default_player_ability,
            is_real=False,
            slot_number=slot_number,
        )
        for slot_number in range(1, FORMATION_RULES[POSITION_MIDFIELDER] + 1)
    )
    roster.extend(
        Player(
            player_id=f"{team_slug}-{POSITION_FORWARD.lower()}-{slot_number}",
            name=None,
            position=POSITION_FORWARD,
            ability=default_player_ability,
            is_real=False,
            slot_number=slot_number,
        )
        for slot_number in range(1, FORMATION_RULES[POSITION_FORWARD] + 1)
    )
    return roster


def _build_position_vacancies(roster: List[Player]) -> Dict[str, List[int]]:
    vacancies: Dict[str, List[int]] = {position: [] for position in FORMATION_RULES}
    for index, player in enumerate(roster):
        vacancies[player.position].append(index)
    return vacancies


def _slugify(value: str) -> str:
    slug = "".join(character.lower() if character.isalnum() else "-" for character in value)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")
