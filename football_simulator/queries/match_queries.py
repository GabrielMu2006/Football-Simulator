"""比赛查询服务（阶段 2）。

职责：赛历目录、单场详情（事件 + 全部 22 行 appeared 球员统计）、以及同一
(season, competition) 上下文内的上一场/下一场定位。

- 比赛身份复用 ``base.MatchRef`` 字段并外加 ``category``；
- 已完成比赛不可变；未赛比赛（scheduled）是赛前页语义：比分为 None、
  事件与球员行为空列表；
- 排序确定性，不调用任何随机源；match_id 不存在时抛 KeyError。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from football_simulator.queries import base

_DEFAULT_ID_SUFFIX = "-default"
_POSITION_TOKENS = {"gk": "GK", "df": "DF", "mf": "MF", "fw": "FW"}


# -- DTO ----------------------------------------------------------------


@dataclass(frozen=True)
class MatchRow(base.MatchRef):
    """base.MatchRef 全部字段 + 赛事类别。"""

    category: str = ""  # premier / second / cup / playoff


@dataclass(frozen=True)
class MatchPlayerLine:
    """单场球员统计行（比赛当时球队归属，appeared=1 的全部行）。"""

    player: base.PlayerRef
    team: base.TeamRef
    goals: int
    assists: int
    chances_created: int
    successful_defenses: int
    successful_saves: int
    clean_sheets: int


@dataclass(frozen=True)
class MatchDetail:
    """单场详情。未赛比赛 events/player_lines 为空列表。"""

    match: MatchRow
    key_events: List[str]  # 按 sequence_no 原始顺序，完整不截断
    player_lines: List[MatchPlayerLine]


# -- 内部：身份解析 -------------------------------------------------------


@dataclass(frozen=True)
class _IdentityInfo:
    display_name: str
    position: str
    is_real: bool
    slot_number: int
    roster_team_id: int


def display_label(name: Optional[str], position: str, slot_number: int) -> str:
    """与 models.Player.label 同规则的显示名。"""
    return name if name else f"默认 {position} {slot_number}"


def _load_identity_map(conn: sqlite3.Connection) -> Dict[str, _IdentityInfo]:
    """db player_id → 注册表身份（players 表）。"""
    result: Dict[str, _IdentityInfo] = {}
    for row in conn.execute(
        "SELECT player_id, name, position, is_real, slot_number, team_id FROM players"
    ):
        result[row["player_id"]] = _IdentityInfo(
            display_name=display_label(row["name"], row["position"], int(row["slot_number"])),
            position=row["position"],
            is_real=bool(row["is_real"]),
            slot_number=int(row["slot_number"]),
            roster_team_id=int(row["team_id"]),
        )
    return result


def _parse_default_db_id(db_player_id: str) -> Optional[Tuple[str, int]]:
    """解析默认球员 DB ID（<球队slug>-<位置>-<槽位>-default）→ (位置, 槽位)。"""
    if not db_player_id.endswith(_DEFAULT_ID_SUFFIX):
        return None
    parts = db_player_id[: -len(_DEFAULT_ID_SUFFIX)].rsplit("-", 2)
    if len(parts) != 3:
        return None
    position = _POSITION_TOKENS.get(parts[1])
    if position is None or not parts[2].isdigit():
        return None
    return position, int(parts[2])


def _player_ref_for(
    db_player_id: str,
    stat_team_id: int,
    identity_map: Dict[str, _IdentityInfo],
) -> base.PlayerRef:
    """比赛统计行的稳定球员引用。

    真实球员稳定 ID 与 DB ID 一致；默认球员按 base.default_player_id 合成
    （注册表缺失时（已被新秀替换的历史默认球员）回退解析 DB ID 本身）。
    """
    info = identity_map.get(db_player_id)
    if info is not None:
        if info.is_real:
            stable_id = db_player_id
        else:
            stable_id = base.default_player_id(info.roster_team_id, info.slot_number)
        return base.PlayerRef(
            player_id=stable_id,
            display_name=info.display_name,
            position=info.position,
            is_real=info.is_real,
        )
    parsed = _parse_default_db_id(db_player_id)
    if parsed is not None:
        position, slot_number = parsed
        return base.PlayerRef(
            player_id=base.default_player_id(stat_team_id, slot_number),
            display_name=f"默认 {position} {slot_number}",
            position=position,
            is_real=False,
        )
    return base.PlayerRef(player_id=db_player_id, display_name=db_player_id, position="", is_real=False)


# -- 内部：读取辅助 -------------------------------------------------------


def _fetch_match_row(conn: sqlite3.Connection, match_id: str):
    row = conn.execute(
        """
        SELECT m.match_id AS match_id,
               m.season_id AS season_id,
               s.season_number AS season_number,
               m.category AS category,
               m.competition AS competition,
               m.week_number AS week_number,
               m.round_number AS round_number,
               m.ordinal AS ordinal,
               m.status AS status,
               m.home_team_id AS home_team_id,
               m.away_team_id AS away_team_id,
               m.home_goals AS home_goals,
               m.away_goals AS away_goals
        FROM matches AS m
        JOIN seasons AS s ON s.season_id = m.season_id
        WHERE m.match_id = ?
        """,
        (match_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"比赛不存在：{match_id!r}")
    return row


def _build_match_row(row, teams: Dict[int, base.TeamRef]) -> MatchRow:
    home_team_id = int(row["home_team_id"])
    away_team_id = int(row["away_team_id"])
    for team_id in (home_team_id, away_team_id):
        if team_id not in teams:
            raise KeyError(f"比赛引用的球队不存在：{team_id}")
    return MatchRow(
        match_id=row["match_id"],
        season_number=int(row["season_number"]),
        competition=row["competition"],
        week_number=int(row["week_number"]),
        round_number=int(row["round_number"]),
        status=row["status"],
        home=teams[home_team_id],
        away=teams[away_team_id],
        home_goals=None if row["home_goals"] is None else int(row["home_goals"]),
        away_goals=None if row["away_goals"] is None else int(row["away_goals"]),
        category=row["category"],
    )


# -- 对外 API ------------------------------------------------------------


def list_matches(
    conn: sqlite3.Connection,
    season_number: int,
    *,
    competition: Optional[str] = None,
    week_number: Optional[int] = None,
    status: Optional[str] = None,
    team_id: Optional[int] = None,
    search: Optional[str] = None,
) -> List[MatchRow]:
    """赛季比赛目录，按 (week_number, category, round_number, ordinal) 排序。"""
    season_id = base.season_id_for(conn, season_number)
    clauses = ["m.season_id = ?"]
    params: List[object] = [season_id]
    if competition is not None:
        clauses.append("m.competition = ?")
        params.append(competition)
    if week_number is not None:
        clauses.append("m.week_number = ?")
        params.append(int(week_number))
    if status is not None:
        clauses.append("m.status = ?")
        params.append(status)
    if team_id is not None:
        clauses.append("(m.home_team_id = ? OR m.away_team_id = ?)")
        params.extend([int(team_id), int(team_id)])
    if search:
        like = f"%{search}%"
        clauses.append("(t1.name LIKE ? OR t2.name LIKE ? OR m.competition LIKE ?)")
        params.extend([like, like, like])
    rows = conn.execute(
        f"""
        SELECT m.match_id AS match_id,
               s.season_number AS season_number,
               m.category AS category,
               m.competition AS competition,
               m.week_number AS week_number,
               m.round_number AS round_number,
               m.status AS status,
               m.home_team_id AS home_team_id,
               m.away_team_id AS away_team_id,
               m.home_goals AS home_goals,
               m.away_goals AS away_goals
        FROM matches AS m
        JOIN seasons AS s ON s.season_id = m.season_id
        JOIN teams AS t1 ON t1.team_id = m.home_team_id
        JOIN teams AS t2 ON t2.team_id = m.away_team_id
        WHERE {' AND '.join(clauses)}
        ORDER BY m.week_number, m.category, m.round_number, m.ordinal, m.match_id
        """,
        tuple(params),
    ).fetchall()
    teams = base.team_ref_by_id(conn)
    return [_build_match_row(row, teams) for row in rows]


def get_match_detail(conn: sqlite3.Connection, match_id: str) -> MatchDetail:
    """单场详情。match_id 不存在时抛 KeyError。

    - 已赛：比分、按 sequence_no 原始顺序的完整事件、全部 appeared=1 行
      （含六项全 0 的行），status='completed'；
    - 未赛：status='scheduled'，比分为 None，events/player_lines 为空列表。
    """
    row = _fetch_match_row(conn, match_id)
    teams = base.team_ref_by_id(conn)
    match_row = _build_match_row(row, teams)

    key_events: List[str] = []
    player_lines: List[MatchPlayerLine] = []
    if row["status"] == base.MATCH_STATUS_COMPLETED:
        key_events = [
            event_row["event_text"]
            for event_row in conn.execute(
                "SELECT event_text FROM match_events WHERE match_id = ? ORDER BY sequence_no",
                (match_id,),
            )
        ]
        stat_rows = conn.execute(
            """
            SELECT player_id, team_id, goals, assists, chances_created,
                   successful_defenses, successful_saves, clean_sheets
            FROM player_match_stats
            WHERE match_id = ? AND appeared = 1
            """,
            (match_id,),
        ).fetchall()
        identity_map = _load_identity_map(conn)
        home_team_id = int(row["home_team_id"])
        entries = []
        for stat_row in stat_rows:
            stat_team_id = int(stat_row["team_id"])
            player_ref = _player_ref_for(stat_row["player_id"], stat_team_id, identity_map)
            entries.append(
                (
                    0 if stat_team_id == home_team_id else 1,
                    stat_team_id,
                    stat_row["player_id"],
                    MatchPlayerLine(
                        player=player_ref,
                        team=teams[stat_team_id],
                        goals=int(stat_row["goals"]),
                        assists=int(stat_row["assists"]),
                        chances_created=int(stat_row["chances_created"]),
                        successful_defenses=int(stat_row["successful_defenses"]),
                        successful_saves=int(stat_row["successful_saves"]),
                        clean_sheets=int(stat_row["clean_sheets"]),
                    ),
                )
            )
        entries.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
        player_lines = [entry[3] for entry in entries]

    return MatchDetail(match=match_row, key_events=key_events, player_lines=player_lines)


def get_match_neighbors(
    conn: sqlite3.Connection,
    match_id: str,
    *,
    competition: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """同一 (season, competition) 过滤上下文中的 (上一场, 下一场) match_id。

    - 上下文序：week_number + ordinal（平局时按 category/round/match_id
      确定性裁决）；competition=None 表示同赛季全部比赛序。
    - competition 过滤上下文必须包含该比赛本身，否则抛 KeyError。
    """
    row = _fetch_match_row(conn, match_id)
    clauses = ["season_id = ?"]
    params: List[object] = [row["season_id"]]
    if competition is not None:
        clauses.append("competition = ?")
        params.append(competition)
    ordered_ids = [
        neighbor_row["match_id"]
        for neighbor_row in conn.execute(
            f"""
            SELECT match_id FROM matches
            WHERE {' AND '.join(clauses)}
            ORDER BY week_number, ordinal, category, round_number, match_id
            """,
            tuple(params),
        )
    ]
    try:
        index = ordered_ids.index(match_id)
    except ValueError as exc:
        raise KeyError(f"比赛 {match_id!r} 不在该赛事上下文中：{competition!r}") from exc
    prev_id = ordered_ids[index - 1] if index > 0 else None
    next_id = ordered_ids[index + 1] if index + 1 < len(ordered_ids) else None
    return prev_id, next_id
