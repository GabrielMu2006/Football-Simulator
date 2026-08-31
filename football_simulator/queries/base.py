"""查询层基础设施：只读连接、稳定 ID 原语与通用辅助。"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from football_simulator.data import real_player_id
from football_simulator.runtime import normalize_save_name, save_root

COMPETITION_PREMIER = "一级联赛"
COMPETITION_SECOND = "次级联赛"
COMPETITION_WINNERS_CUP = "优胜者杯"
COMPETITION_CHALLENGE_CUP = "挑战杯"
COMPETITION_SUPER_CUP = "超级杯"
COMPETITION_PLAYOFF = "升级附加赛"
LEAGUE_COMPETITIONS = (COMPETITION_PREMIER, COMPETITION_SECOND)
CUP_COMPETITIONS = (COMPETITION_WINNERS_CUP, COMPETITION_CHALLENGE_CUP, COMPETITION_SUPER_CUP)
ALL_COMPETITIONS = (*LEAGUE_COMPETITIONS, *CUP_COMPETITIONS, COMPETITION_PLAYOFF)

CATEGORY_BY_COMPETITION = {
    COMPETITION_PREMIER: "premier",
    COMPETITION_SECOND: "second",
    COMPETITION_PLAYOFF: "playoff",
    COMPETITION_WINNERS_CUP: "cup",
    COMPETITION_CHALLENGE_CUP: "cup",
    COMPETITION_SUPER_CUP: "cup",
}

MATCH_STATUS_SCHEDULED = "scheduled"
MATCH_STATUS_COMPLETED = "completed"

SETTLEMENT_STAGE_WINTER = "winter"
SETTLEMENT_STAGE_FINAL = "final"


class MissingSaveError(FileNotFoundError):
    """存档不存在或尚未初始化。"""


def database_path(save_name: str) -> Path:
    return save_root() / normalize_save_name(save_name) / "save.sqlite3"


@contextmanager
def open_read_connection(save_name: str) -> Iterator[sqlite3.Connection]:
    """打开存档的只读连接（PRAGMA query_only=ON）。"""
    path = database_path(save_name)
    if not path.exists():
        raise MissingSaveError(f"未找到存档 '{save_name}' 的存档数据库：{path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    try:
        yield conn
    finally:
        conn.close()


def default_player_id(team_id: int, slot_number: int) -> str:
    """默认球员没有跨赛季稳定身份，按 (球队, 槽位) 合成查询层 ID。"""
    return f"default:{team_id}:{slot_number}"


@dataclass(frozen=True)
class SeasonRef:
    season_number: int
    status: str  # active / completed

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"


@dataclass(frozen=True)
class PlayerRef:
    """球员稳定引用。真实球员 ID 与注册表/比赛统计一致。"""

    player_id: str
    display_name: str
    position: str
    is_real: bool


@dataclass(frozen=True)
class TeamRef:
    team_id: int
    display_name: str
    division: str


@dataclass(frozen=True)
class CompetitionRef:
    competition_id: str  # 规范中文常量
    display_name: str

    @property
    def category(self) -> str:
        return CATEGORY_BY_COMPETITION[self.competition_id]


@dataclass(frozen=True)
class MatchRef:
    match_id: str
    season_number: int
    competition: str
    week_number: int
    round_number: int
    status: str  # scheduled / completed
    home: TeamRef
    away: TeamRef
    home_goals: Optional[int] = None
    away_goals: Optional[int] = None

    @property
    def is_completed(self) -> bool:
        return self.status == MATCH_STATUS_COMPLETED

    @property
    def score_display(self) -> Optional[str]:
        if not self.is_completed:
            return None
        return f"{self.home_goals}-{self.away_goals}"


# -- 通用读取辅助 -------------------------------------------------------


def load_seasons(conn: sqlite3.Connection) -> List[SeasonRef]:
    return [
        SeasonRef(season_number=int(row["season_number"]), status=row["status"])
        for row in conn.execute("SELECT season_number, status FROM seasons ORDER BY season_number")
    ]


def resolve_current_season(conn: sqlite3.Connection) -> SeasonRef:
    """当前赛季 = status='active' 的赛季；没有 active 时取最新一个。"""
    seasons = load_seasons(conn)
    if not seasons:
        raise MissingSaveError("存档还没有任何赛季数据。")
    for season in seasons:
        if season.status == "active":
            return season
    return seasons[-1]


def load_week_labels(conn: sqlite3.Connection) -> Tuple[dict, ...]:
    """读取存档 save_meta 中当前（进行中）赛季的赛历条目（已按杯赛激活修饰）。"""
    row = conn.execute("SELECT value_json FROM save_meta WHERE key = 'weeks'").fetchone()
    if row is None:
        return ()
    return tuple(json.loads(row["value_json"]))


def season_id_for(conn: sqlite3.Connection, season_number: int) -> int:
    row = conn.execute(
        "SELECT season_id FROM seasons WHERE season_number = ?",
        (int(season_number),),
    ).fetchone()
    if row is None:
        raise KeyError(f"存档中不存在第 {season_number} 赛季。")
    return int(row["season_id"])


def load_team_refs(conn: sqlite3.Connection) -> List[TeamRef]:
    return [
        TeamRef(team_id=int(row["team_id"]), display_name=row["name"], division=row["division"])
        for row in conn.execute("SELECT team_id, name, division FROM teams ORDER BY ordinal")
    ]


def team_ref_by_id(conn: sqlite3.Connection) -> Dict[int, TeamRef]:
    return {ref.team_id: ref for ref in load_team_refs(conn)}


def load_real_player_identity(
    conn: sqlite3.Connection,
) -> Dict[str, Tuple[str, str, int]]:
    """player_id -> (display_name, position, team_id)，来自当前注册表。"""
    identity: Dict[str, Tuple[str, str, int]] = {}
    for row in conn.execute(
        """
        SELECT p.player_id, p.name, p.position, p.team_id
        FROM players AS p WHERE p.is_real = 1
        """
    ):
        identity[row["player_id"]] = (row["name"], row["position"], int(row["team_id"]))
    return identity


def load_default_player_identity(
    conn: sqlite3.Connection,
) -> Dict[Tuple[int, int], Tuple[str, str]]:
    """(team_id, slot_number) -> (position, roster_index)；用于默认球员身份合成。"""
    identity: Dict[Tuple[int, int], Tuple[str, str]] = {}
    for row in conn.execute(
        "SELECT team_id, slot_number, position, roster_index FROM players WHERE is_real = 0"
    ):
        identity[(int(row["team_id"]), int(row["slot_number"]))] = (
            row["position"],
            int(row["roster_index"]),
        )
    return identity


def canonical_player_id_for_name(name: str) -> str:
    """历史键（real::<显示名>）到稳定 ID（real::<slug>）的收敛入口。"""
    return real_player_id(name)
