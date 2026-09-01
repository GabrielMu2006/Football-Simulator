"""球队维度的只读查询（阶段 2）。

数据口径：
- 联赛积分榜只累计 ``matches`` 中 ``category='premier'/'second'`` 的比赛；
  杯赛（category='cup'）与升级附加赛（category='playoff'）绝不计入。
- 排名链使用 ``football_simulator.domain.standings``（积分 → 净胜球 → 进球 →
  相互战绩 → 归档裁决 → 球队名称回退），与引擎 ``build_snapshot_from_state``
  一致；归档存在时使用归档中的 ``ranking_playoffs`` 同分裁决。
- 球队荣誉（team_honors）与球员个人奖项（player_awards）分开展示，互不混入。
- 历史键（归档/awards 表中的 ``real::<显示名>``）统一经
  ``base.canonical_player_id_for_name`` 收敛为稳定 ID；默认球员使用
  ``base.default_player_id``。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from football_simulator.domain import standings
from football_simulator.models import Fixture, MatchResult, Player, TableRow, Team
from football_simulator.queries import base

_DIVISION_BY_CATEGORY = {"premier": base.COMPETITION_PREMIER, "second": base.COMPETITION_SECOND}
_CATEGORY_BY_DIVISION = {division: category for category, division in _DIVISION_BY_CATEGORY.items()}
_LEAGUE_CATEGORIES = ("premier", "second")

# 归档荣誉标签中的占位值：不代表实际荣誉，展示时剔除。
_NON_HONOR_LABELS = {"", "未参赛", "未知", "未定"}
_HONOR_FIELD_LABELS = {
    "league_result": "联赛",
    "winners_cup_result": "优胜者杯",
    "challenge_cup_result": "挑战杯",
    "super_cup_result": "超级杯",
}

# 排名链占位阵容：Team 构造要求 1GK/4DF/3MF/3FW，排名计算只使用队名。
_PLACEHOLDER_SLOTS = (("GK", (1,)), ("DF", (1, 2, 3, 4)), ("MF", (1, 2, 3)), ("FW", (1, 2, 3)))
_PLACEHOLDER_TEAMS: Dict[str, Team] = {}


# -- DTO ----------------------------------------------------------------


@dataclass(frozen=True)
class TeamStandingsLine:
    """球队在某个赛季所属联赛中的积分榜行。"""

    played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    points: int
    rank: Optional[int]  # 分区内名次；该队尚未赛过（played=0）时为 None


@dataclass(frozen=True)
class TeamDirectoryRow:
    """球队目录行：联赛积分榜快照 + 当前注册阵容摘要。"""

    team: base.TeamRef
    season_division: str  # 该赛季实际参赛分区（由该赛季比赛 category 推得）
    played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    points: int
    rank: Optional[int]
    real_player_count: int  # 按当前注册阵容统计
    roster_total_ability: int  # 按当前注册阵容统计


@dataclass(frozen=True)
class TeamRosterPlayer:
    player: base.PlayerRef
    ability: int
    market_value: Optional[float]  # 结算身价（final 优先、winter 次之）；默认球员恒为 None


@dataclass(frozen=True)
class TeamTransferRow:
    player: base.PlayerRef
    market_value: Optional[float]
    week_number: int
    window: str
    trade_id: Optional[str]
    status: str
    counterpart: base.TeamRef  # 交易对手球队


@dataclass(frozen=True)
class PlayerAwardLine:
    """球员个人奖项行（来自 awards 表；与球队荣誉分开展示）。"""

    player: base.PlayerRef
    award_type: str  # top20 / top_scorer / assist_leader / mvp
    competition: Optional[str]  # top20 无所属赛事为 None
    rank: Optional[int]
    score: Optional[float]


@dataclass(frozen=True)
class TeamSeasonProfile:
    identity: base.TeamRef
    season_division: str
    standings_row: TeamStandingsLine
    roster: Tuple[TeamRosterPlayer, ...]
    fixtures: Tuple[base.MatchRef, ...]
    transfers_in: Tuple[TeamTransferRow, ...]
    transfers_out: Tuple[TeamTransferRow, ...]
    team_honors: Tuple[str, ...]
    player_awards: Tuple[PlayerAwardLine, ...]


# -- 内部辅助 -----------------------------------------------------------


def _placeholder_team(name: str) -> Team:
    team = _PLACEHOLDER_TEAMS.get(name)
    if team is None:
        roster = tuple(
            Player(
                player_id=f"standings::{name}::{position}{slot}",
                name=None,
                position=position,
                ability=0,
                is_real=False,
                slot_number=slot,
            )
            for position, slots in _PLACEHOLDER_SLOTS
            for slot in slots
        )
        team = Team(name=name, roster=roster)
        _PLACEHOLDER_TEAMS[name] = team
    return team


def load_archive(conn: sqlite3.Connection, season_number: int) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT sa.archive_json FROM season_archives AS sa
        JOIN seasons AS s ON s.season_id = sa.season_id
        WHERE s.season_number = ?
        """,
        (int(season_number),),
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["archive_json"])


def _team_refs_by_id(conn: sqlite3.Connection) -> Dict[int, base.TeamRef]:
    return base.team_ref_by_id(conn)


def _team_refs_by_name(conn: sqlite3.Connection) -> Dict[str, base.TeamRef]:
    return {ref.display_name: ref for ref in base.load_team_refs(conn)}


def _season_division_by_team(conn: sqlite3.Connection, season_id: int) -> Dict[int, str]:
    """球队在该赛季的参赛分区（由联赛比赛 category 推得，与升降级无关）。"""
    mapping: Dict[int, str] = {}
    for category in _LEAGUE_CATEGORIES:
        for row in conn.execute(
            """
            SELECT home_team_id AS team_id FROM matches WHERE season_id = ? AND category = ?
            UNION
            SELECT away_team_id FROM matches WHERE season_id = ? AND category = ?
            """,
            (season_id, category, season_id, category),
        ):
            mapping[int(row["team_id"])] = _DIVISION_BY_CATEGORY[category]
    return mapping


def compute_division_standings(
    conn: sqlite3.Connection,
    season_id: int,
    category: str,
    resolutions: List[dict],
) -> List[TableRow]:
    """按排名链从 matches 重放某分区全部已完成联赛比赛，返回有序 TableRow。"""
    names_by_id = {int(row["team_id"]): row["name"] for row in conn.execute("SELECT team_id, name FROM teams")}
    table_map: Dict[str, TableRow] = {}
    results: List[MatchResult] = []
    for row in conn.execute(
        """
        SELECT round_number, home_team_id, away_team_id, status, home_goals, away_goals, competition
        FROM matches
        WHERE season_id = ? AND category = ?
        ORDER BY week_number, round_number, ordinal
        """,
        (season_id, category),
    ):
        home_team = _placeholder_team(names_by_id[int(row["home_team_id"])])
        away_team = _placeholder_team(names_by_id[int(row["away_team_id"])])
        table_map.setdefault(home_team.name, TableRow(team=home_team))
        table_map.setdefault(away_team.name, TableRow(team=away_team))
        if row["status"] != base.MATCH_STATUS_COMPLETED:
            continue
        result = MatchResult(
            fixture=Fixture(int(row["round_number"]), home_team, away_team, row["competition"]),
            home_goals=int(row["home_goals"]),
            away_goals=int(row["away_goals"]),
            key_events=[],
        )
        standings.apply_table_result(table_map, result)
        results.append(result)
    return standings.rank_table_rows(list(table_map.values()), results, list(resolutions))


def _resolutions_for(conn: sqlite3.Connection, season_number: int, category: str) -> List[dict]:
    """归档中的同分裁决（仅当该赛季归档存在时可用）。"""
    archive = load_archive(conn, season_number)
    if not archive:
        return []
    division = _DIVISION_BY_CATEGORY[category]
    return list(archive.get("ranking_playoffs", {}).get(division, []))


def _standings_rows(
    conn: sqlite3.Connection,
    season_id: int,
    season_number: int,
    category: str,
) -> Tuple[List[TableRow], Dict[str, int]]:
    ordered = compute_division_standings(conn, season_id, category, _resolutions_for(conn, season_number, category))
    ids_by_name = {row["name"]: int(row["team_id"]) for row in conn.execute("SELECT team_id, name FROM teams")}
    return ordered, ids_by_name


def _standings_line(ordered: List[TableRow], team_name: str) -> TeamStandingsLine:
    for index, row in enumerate(ordered, start=1):
        if row.team.name == team_name:
            rank = index if row.played > 0 else None
            return TeamStandingsLine(
                played=row.played,
                wins=row.wins,
                draws=row.draws,
                losses=row.losses,
                goals_for=row.goals_for,
                goals_against=row.goals_against,
                points=row.points,
                rank=rank,
            )
    return TeamStandingsLine(0, 0, 0, 0, 0, 0, 0, None)


def _match_ref(row: sqlite3.Row, season_number: int, refs_by_id: Dict[int, base.TeamRef]) -> base.MatchRef:
    completed = row["status"] == base.MATCH_STATUS_COMPLETED
    return base.MatchRef(
        match_id=row["match_id"],
        season_number=season_number,
        competition=row["competition"],
        week_number=int(row["week_number"]),
        round_number=int(row["round_number"]),
        status=row["status"],
        home=refs_by_id[int(row["home_team_id"])],
        away=refs_by_id[int(row["away_team_id"])],
        home_goals=int(row["home_goals"]) if completed else None,
        away_goals=int(row["away_goals"]) if completed else None,
    )


def _settlement_market_values(conn: sqlite3.Connection, season_id: int) -> Dict[str, float]:
    """player_key -> 最终身价（final 优先于 winter）。"""
    values: Dict[str, float] = {}
    priority = {base.SETTLEMENT_STAGE_FINAL: 0, base.SETTLEMENT_STAGE_WINTER: 1}
    for row in conn.execute(
        "SELECT stage, player_key, market_value FROM player_settlements WHERE season_id = ?",
        (season_id,),
    ):
        if row["market_value"] is None:
            continue
        key = row["player_key"]
        rank = priority.get(row["stage"], 2)
        if key not in values or rank < values[key][0]:
            values[key] = (rank, float(row["market_value"]))
    return {key: value for key, (_, value) in values.items()}


def _player_ref_for_roster(row: sqlite3.Row, team_id: int) -> base.PlayerRef:
    is_real = bool(row["is_real"])
    display_name = row["name"] or f"默认 {row['position']} {int(row['slot_number'])}"
    if is_real:
        player_id = row["player_id"]
    else:
        player_id = base.default_player_id(team_id, int(row["slot_number"]))
    return base.PlayerRef(player_id=player_id, display_name=display_name, position=row["position"], is_real=is_real)


# -- 公开查询 -----------------------------------------------------------


def list_teams(
    conn: sqlite3.Connection,
    season_number: int,
    *,
    division: Optional[str] = None,
    search: Optional[str] = None,
) -> List[TeamDirectoryRow]:
    """球队目录：40 支球队各一行，附该赛季联赛积分榜行与阵容摘要。

    - ``division`` 按该赛季实际参赛分区过滤（由比赛 category 推得，而非
      teams 表当前分区，以便正确查询升降级前的历史赛季）。
    - ``search`` 按队名子串过滤。
    - 真实球员数 / 阵容总能力按当前注册阵容（players 表）统计。
    """
    season_id = base.season_id_for(conn, season_number)
    division_map = _season_division_by_team(conn, season_id)
    ordered_by_category = {
        category: compute_division_standings(conn, season_id, category, _resolutions_for(conn, season_number, category))
        for category in _LEAGUE_CATEGORIES
    }
    roster_stats = {
        int(row["team_id"]): (int(row["real_count"] or 0), int(row["total_ability"] or 0))
        for row in conn.execute(
            """
            SELECT team_id, SUM(is_real) AS real_count, SUM(ability) AS total_ability
            FROM players GROUP BY team_id
            """
        )
    }

    rows: List[TeamDirectoryRow] = []
    for ref in base.load_team_refs(conn):
        season_division = division_map.get(ref.team_id, ref.division)
        if division is not None and season_division != division:
            continue
        if search is not None and search not in ref.display_name:
            continue
        category = _CATEGORY_BY_DIVISION.get(season_division)
        line = _standings_line(ordered_by_category[category], ref.display_name) if category else _standings_line([], ref.display_name)
        real_count, total_ability = roster_stats.get(ref.team_id, (0, 0))
        rows.append(
            TeamDirectoryRow(
                team=ref,
                season_division=season_division,
                played=line.played,
                wins=line.wins,
                draws=line.draws,
                losses=line.losses,
                goals_for=line.goals_for,
                goals_against=line.goals_against,
                points=line.points,
                rank=line.rank,
                real_player_count=real_count,
                roster_total_ability=total_ability,
            )
        )

    division_order = {base.COMPETITION_PREMIER: 0, base.COMPETITION_SECOND: 1}

    def _order_key(row: TeamDirectoryRow) -> tuple:
        return (
            division_order.get(row.season_division, 9),
            row.rank if row.rank is not None else 10_000,
            row.team.display_name,
        )

    rows.sort(key=_order_key)
    return rows


def get_team_season_profile(conn: sqlite3.Connection, team_id: int, season_number: int) -> TeamSeasonProfile:
    """球队某赛季完整档案：积分榜行、阵容、赛程、转会、球队荣誉与球员奖项。"""
    team_row = conn.execute(
        "SELECT team_id, name, division FROM teams WHERE team_id = ?",
        (int(team_id),),
    ).fetchone()
    if team_row is None:
        raise KeyError(f"存档中不存在 team_id={team_id} 的球队。")
    season_id = base.season_id_for(conn, season_number)
    team_name = team_row["name"]
    identity = base.TeamRef(team_id=int(team_row["team_id"]), display_name=team_name, division=team_row["division"])
    refs_by_id = _team_refs_by_id(conn)
    refs_by_name = {ref.display_name: ref for ref in refs_by_id.values()}

    division_map = _season_division_by_team(conn, season_id)
    season_division = division_map.get(int(team_id), identity.division)
    category = _CATEGORY_BY_DIVISION.get(season_division)
    if category is not None:
        ordered, _ = _standings_rows(conn, season_id, season_number, category)
        standings_row = _standings_line(ordered, team_name)
    else:
        standings_row = _standings_line([], team_name)

    roster = tuple(_roster_lines(conn, season_id, int(team_id)))
    fixtures = tuple(_fixture_lines(conn, season_id, season_number, int(team_id), refs_by_id))
    transfers_in, transfers_out = _transfer_lines(conn, season_number, team_name, refs_by_name)
    team_honors = tuple(_team_honors(conn, season_number, team_name))
    player_awards = tuple(_player_awards(conn, season_id, team_name))

    return TeamSeasonProfile(
        identity=identity,
        season_division=season_division,
        standings_row=standings_row,
        roster=roster,
        fixtures=fixtures,
        transfers_in=transfers_in,
        transfers_out=transfers_out,
        team_honors=team_honors,
        player_awards=player_awards,
    )


def _roster_lines(conn: sqlite3.Connection, season_id: int, team_id: int) -> List[TeamRosterPlayer]:
    market_values = _settlement_market_values(conn, season_id)
    lines: List[TeamRosterPlayer] = []
    for row in conn.execute(
        """
        SELECT player_id, name, position, ability, is_real, slot_number, initial_market_value
        FROM players WHERE team_id = ? ORDER BY roster_index
        """,
        (team_id,),
    ):
        ref = _player_ref_for_roster(row, team_id)
        if ref.is_real:
            value = market_values.get(row["name"])
            if value is None:
                initial = row["initial_market_value"]
                value = float(initial) if initial is not None else None
        else:
            value = None
        lines.append(TeamRosterPlayer(player=ref, ability=int(row["ability"]), market_value=value))
    return lines


def _fixture_lines(
    conn: sqlite3.Connection,
    season_id: int,
    season_number: int,
    team_id: int,
    refs_by_id: Dict[int, base.TeamRef],
) -> List[base.MatchRef]:
    rows = conn.execute(
        """
        SELECT match_id, competition, week_number, round_number, ordinal,
               home_team_id, away_team_id, status, home_goals, away_goals
        FROM matches
        WHERE season_id = ? AND (home_team_id = ? OR away_team_id = ?)
        ORDER BY week_number, round_number, category, ordinal
        """,
        (season_id, team_id, team_id),
    ).fetchall()
    return [_match_ref(row, season_number, refs_by_id) for row in rows]


def _transfer_lines(
    conn: sqlite3.Connection,
    season_number: int,
    team_name: str,
    refs_by_name: Dict[str, base.TeamRef],
) -> Tuple[Tuple[TeamTransferRow, ...], Tuple[TeamTransferRow, ...]]:
    incoming: List[TeamTransferRow] = []
    outgoing: List[TeamTransferRow] = []
    for row in conn.execute(
        """
        SELECT week_number, window, trade_id, team_a, team_b,
               team_a_players_json, team_b_players_json, status
        FROM transfers WHERE season_number = ?
        ORDER BY transfer_row_id
        """,
        (int(season_number),),
    ):
        team_a = refs_by_name.get(row["team_a"])
        team_b = refs_by_name.get(row["team_b"])
        if team_a is None or team_b is None:
            raise KeyError(f"转会记录涉及未知球队：{row['team_a']} / {row['team_b']}")
        players_a = json.loads(row["team_a_players_json"])
        players_b = json.loads(row["team_b_players_json"])
        if row["team_a"] == team_name:
            outgoing.extend(_transfer_rows(players_a, row, team_b))
            incoming.extend(_transfer_rows(players_b, row, team_b))
        elif row["team_b"] == team_name:
            incoming.extend(_transfer_rows(players_a, row, team_a))
            outgoing.extend(_transfer_rows(players_b, row, team_a))

    def _order_key(line: TeamTransferRow) -> tuple:
        return (line.week_number, line.trade_id or "", line.player.display_name)

    incoming.sort(key=_order_key)
    outgoing.sort(key=_order_key)
    return tuple(incoming), tuple(outgoing)


def _transfer_rows(players: list, row: sqlite3.Row, counterpart: base.TeamRef) -> List[TeamTransferRow]:
    lines: List[TeamTransferRow] = []
    for item in players:
        display_name = item.get("name") or item.get("player_id") or ""
        lines.append(
            TeamTransferRow(
                player=base.PlayerRef(
                    player_id=base.canonical_player_id_for_name(display_name),
                    display_name=display_name,
                    position=item.get("position") or "",
                    is_real=True,
                ),
                market_value=float(item["market_value"]) if item.get("market_value") is not None else None,
                week_number=int(row["week_number"]),
                window=row["window"],
                trade_id=row["trade_id"],
                status=row["status"],
                counterpart=counterpart,
            )
        )
    return lines


def _team_honors(conn: sqlite3.Connection, season_number: int, team_name: str) -> List[str]:
    """该赛季归档中该队的荣誉标签（league_result + 三项杯赛结果）。"""
    archive = load_archive(conn, season_number)
    if not archive:
        return []
    for row in archive.get("team_stats", []):
        if row.get("team_name") == team_name:
            fields = (
                ("league_result", row.get("league_result")),
                ("winners_cup_result", row.get("winners_cup_result")),
                ("challenge_cup_result", row.get("challenge_cup_result")),
                ("super_cup_result", row.get("super_cup_result")),
            )
            labels = []
            for field, value in fields:
                if value and value not in _NON_HONOR_LABELS:
                    prefix = _HONOR_FIELD_LABELS.get(field, "")
                    labels.append(f"{prefix} {value}".strip())
            return labels
    return []


def _player_awards(
    conn: sqlite3.Connection,
    season_id: int,
    team_name: str,
) -> List[PlayerAwardLine]:
    """该队球员在该赛季获得的个人奖项（awards 表按 team_name 匹配）。"""
    positions_by_name = {
        row["name"]: row["position"]
        for row in conn.execute("SELECT name, position FROM players WHERE is_real = 1 AND name IS NOT NULL")
    }
    lines: List[PlayerAwardLine] = []
    for row in conn.execute(
        """
        SELECT award_type, competition, rank, player_key, player_label, score
        FROM awards WHERE season_id = ? AND team_name = ?
        ORDER BY award_row_id
        """,
        (season_id, team_name),
    ):
        label = row["player_label"] or row["player_key"] or ""
        position = positions_by_name.get(label, "")
        lines.append(
            PlayerAwardLine(
                player=base.PlayerRef(
                    player_id=base.canonical_player_id_for_name(label),
                    display_name=label,
                    position=position,
                    is_real=True,
                ),
                award_type=row["award_type"],
                competition=row["competition"],
                rank=int(row["rank"]) if row["rank"] is not None else None,
                score=float(row["score"]) if row["score"] is not None else None,
            )
        )
    return lines
