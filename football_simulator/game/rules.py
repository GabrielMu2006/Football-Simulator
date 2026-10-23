"""稳定赛事注册表、规则版本与权威参数。

规则数值直接读取 ``football_simulator/rules/competitions.json``（由
Reviews/game_design_2026-10-20 的已确认设计文件原样复制，SHA-256 见
``RULES_SOURCE_SHA256``）。显示名只在这里映射，任何改名都不改变赛事 ID、
参赛资格、签表、比赛 ID、统计或历史事实。
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

RULES_VERSION = "2026-10-20.2"
WORLD_FORMAT_VERSION = 2
RULES_DIR = Path(__file__).resolve().parent.parent / "rules"
COMPETITIONS_PATH = RULES_DIR / "competitions.json"
STADIUMS_PATH = RULES_DIR / "stadiums.json"

RULES_SOURCE_SHA256 = {
    "competitions": "21eff4e178c18397c5de3f5d3c6082ea8007a60e4996becb8ea1a35776c51919",
    "stadiums": "26df9bf982c0775910d587ee5d59ccd9f5f2111372d89255b66ab0d58c074b8a",
}

PREMIER = "premier"
SECOND = "second"
PLAYOFF = "playoff"

LEAGUE_COMPETITIONS = (PREMIER, SECOND)
CUP_IDS = ("aureole", "confluence", "starpath", "ember", "ascendant", "zenith")
REGULAR_CUP_IDS = CUP_IDS[:5]

CUP_META: Dict[str, dict] = {
    "aureole": {
        "cup_number": 1, "name_zh": "君临杯", "name_en": "Reign Cup",
        "entrants": 16, "start_season": 2, "match_count": 62,
        "format": "groups_then_two_leg_knockout",
    },
    "confluence": {
        "cup_number": 2, "name_zh": "命途杯", "name_en": "Destiny Cup",
        "entrants": 32, "start_season": 2, "match_count": 31,
        "format": "single_elimination_redraw",
    },
    "starpath": {
        "cup_number": 3, "name_zh": "极境杯", "name_en": "Apex Cup",
        "entrants": 16, "start_season": 2, "match_count": 46,
        "format": "swiss_then_two_leg_knockout",
    },
    "ember": {
        "cup_number": 4, "name_zh": "不灭杯", "name_en": "Resurgence Cup",
        "entrants": 8, "start_season": 2, "match_count": 14,
        "format": "double_elimination",
    },
    "ascendant": {
        "cup_number": 5, "name_zh": "凌越杯", "name_en": "Ascendance Cup",
        "entrants": 12, "start_season": 2, "match_count": 46,
        "format": "cross_group_playin_knockout",
    },
    "zenith": {
        "cup_number": 6, "name_zh": "永世杯", "name_en": "Aeon Cup",
        "entrants": 8, "start_season": 3, "match_count": 7,
        "format": "qualified_fixed_bracket",
    },
}

COMPETITION_DISPLAY: Dict[str, dict] = {
    PREMIER: {"zh": "一级联赛", "en": "Premier Division"},
    SECOND: {"zh": "次级联赛", "en": "Second Division"},
    PLAYOFF: {"zh": "升级附加赛", "en": "Promotion Playoffs"},
}
for _cup_id, _meta in CUP_META.items():
    COMPETITION_DISPLAY[_cup_id] = {"zh": _meta["name_zh"], "en": _meta["name_en"]}

ALL_COMPETITION_IDS = (PREMIER, SECOND, PLAYOFF, *CUP_IDS)

STAGE_LABELS: Dict[str, Dict[str, str]] = {
    "league": {"zh": "联赛", "en": "League"},
    "group": {"zh": "小组赛", "en": "Group Stage"},
    "round_32": {"zh": "32强", "en": "Round of 32"},
    "round_16": {"zh": "16强", "en": "Round of 16"},
    "quarterfinal": {"zh": "八强", "en": "Quarter-final"},
    "semifinal": {"zh": "半决赛", "en": "Semi-final"},
    "final": {"zh": "决赛", "en": "Final"},
    "swiss": {"zh": "瑞士轮", "en": "Swiss Stage"},
    "play_in": {"zh": "突围赛", "en": "Play-in"},
    "wb_quarterfinal": {"zh": "胜者组首轮", "en": "Winners' Round 1"},
    "wb_semifinal": {"zh": "胜者组次轮", "en": "Winners' Round 2"},
    "wb_final": {"zh": "胜者组决赛", "en": "Winners' Final"},
    "lb_round_1": {"zh": "败者组首轮", "en": "Losers' Round 1"},
    "lb_round_2": {"zh": "败者组次轮", "en": "Losers' Round 2"},
    "lb_round_3": {"zh": "败者组第三轮", "en": "Losers' Round 3"},
    "lb_final": {"zh": "败者组决赛", "en": "Losers' Final"},
    "grand_final": {"zh": "总决赛", "en": "Grand Final"},
    "cross_group": {"zh": "组外循环", "en": "Cross-group"},
    "promotion_semifinal": {"zh": "升级附加赛半决赛", "en": "Promotion Semi-final"},
    "promotion_final": {"zh": "升级附加赛决赛", "en": "Promotion Final"},
}


def display_name(competition_id: str, lang: str = "en") -> str:
    """按稳定 ID 取显示名；英文是默认主名，中文用于实体副标题。"""
    labels = COMPETITION_DISPLAY.get(competition_id)
    if labels is None:
        return competition_id
    if lang == "en":
        return labels["en"]
    return labels["zh"] or labels["en"]


def competition_label(competition_id: str) -> str:
    """历史兼容入口：赛事列表/比分卡/荣誉卡统一英文主名。"""
    return display_name(competition_id, "en")


def stage_label(stage: str, lang: str = "zh") -> str:
    labels = STAGE_LABELS.get(stage)
    if labels is None:
        return stage
    return labels["zh"] if lang == "zh" else labels["en"]


@lru_cache(maxsize=1)
def load_rules() -> dict:
    data = json.loads(COMPETITIONS_PATH.read_text(encoding="utf-8"))
    _verify_rule_file(data, COMPETITIONS_PATH, "competitions")
    return data


@lru_cache(maxsize=1)
def load_stadiums() -> List[dict]:
    data = json.loads(STADIUMS_PATH.read_text(encoding="utf-8"))
    _verify_rule_file(data, STADIUMS_PATH, "stadiums")
    return list(data["stadiums"])


def _verify_rule_file(data: dict, path: Path, key: str) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != RULES_SOURCE_SHA256[key]:
        raise RuntimeError(
            f"规则文件 {path.name} 与已确认版本不一致（sha256={digest}）。"
            "规则数值必须来自已确认设计，不允许运行时静默漂移。"
        )


@lru_cache(maxsize=1)
def calendar_weeks() -> List[dict]:
    return list(load_rules()["weeks"])


HONOR_PROFILE = "champion-first-2026-10-23"


@lru_cache(maxsize=2)
def honor_points(profile: Optional[str] = None) -> dict:
    if profile == HONOR_PROFILE:
        return json.loads((RULES_DIR / "honor_scoring.json").read_text(encoding="utf-8"))
    return dict(load_rules()["honor_points"])


@lru_cache(maxsize=1)
def swiss_priority_table() -> tuple:
    return tuple(tuple(tuple(pair) for pair in option) for option in load_rules()["swiss_six_team_pairing_priority"])


@lru_cache(maxsize=1)
def stadiums_by_club() -> Dict[str, dict]:
    return {item["club_en"]: item for item in load_stadiums()}


def stadium_by_id(stadium_id: str) -> Optional[dict]:
    return next((item for item in load_stadiums() if item["stadium_id"] == stadium_id), None)


def week_cup_slots(week: dict) -> List[dict]:
    """唯一杯赛日程真源：weeks[].cup_slots（兼容旧只读规则的 cup 仅用于迁移测试）。"""
    slots = week.get("cup_slots")
    if slots is None:
        legacy = week.get("cup")
        return [dict(legacy)] if legacy else []
    return [dict(slot) for slot in (slots or [])]


def stage_weeks(competition_id: str) -> Dict[str, List[int]]:
    """规则 JSON 中该杯赛的阶段 -> 周次（同一阶段可有多周）。"""
    result: Dict[str, List[int]] = {}
    for week in calendar_weeks():
        for cup in week_cup_slots(week):
            if cup.get("competition") == competition_id:
                result.setdefault(cup["stage"], []).append(int(week["week"]))
    return result


def week_slot(week_number: int) -> dict:
    for week in calendar_weeks():
        if int(week["week"]) == week_number:
            return week
    raise KeyError(f"赛历中没有第 {week_number} 周。")


def league_round_for_week(week_number: int) -> Optional[int]:
    return week_slot(week_number).get("league_round")


def neutral_final_competitions() -> tuple:
    return ("confluence", "starpath", "ascendant", "zenith")


def cup_active(cup_id: str, season_number: int) -> bool:
    return season_number >= CUP_META[cup_id]["start_season"]


def active_cups(season_number: int) -> tuple:
    return tuple(cup_id for cup_id in CUP_IDS if cup_active(cup_id, season_number))


def honor_key_for_rank(competition_id: str, rank: int) -> str:
    """把最终名次映射到荣誉表层级键（六杯）。"""
    if competition_id == "aureole":
        return "champion" if rank == 1 else "runner_up" if rank == 2 else "semifinal" if rank <= 4 else "quarterfinal" if rank <= 8 else "group_third" if rank <= 12 else "group_fourth"
    if competition_id == "confluence":
        return "champion" if rank == 1 else "runner_up" if rank == 2 else "semifinal" if rank <= 4 else "quarterfinal" if rank <= 8 else "round_16" if rank <= 16 else "round_32"
    if competition_id == "starpath":
        return "champion" if rank == 1 else "runner_up" if rank == 2 else "semifinal" if rank <= 4 else "quarterfinal" if rank <= 8 else "swiss_2_3" if rank <= 11 else "swiss_1_3" if rank <= 14 else "swiss_0_3"
    if competition_id == "ember":
        return "champion" if rank == 1 else "runner_up" if rank == 2 else "third" if rank == 3 else "fourth" if rank == 4 else "fifth_sixth" if rank <= 6 else "seventh_eighth"
    if competition_id == "ascendant":
        return "champion" if rank == 1 else "runner_up" if rank == 2 else "semifinal" if rank <= 4 else "quarterfinal" if rank <= 8 else "play_in_exit" if rank <= 11 else "nirvana_sixth"
    if competition_id == "zenith":
        return "champion" if rank == 1 else "runner_up" if rank == 2 else "semifinal" if rank <= 4 else "quarterfinal"
    raise KeyError(competition_id)


def honor_points_for(competition_id: str, rank: int, profile: Optional[str] = None) -> int:
    table = honor_points(profile)[competition_id]
    return int(table.get(honor_key_for_rank(competition_id, rank), 0))


def premier_honor_points(rank: int, profile: Optional[str] = None) -> int:
    return int(honor_points(profile)["premier"].get(str(rank), 0))
