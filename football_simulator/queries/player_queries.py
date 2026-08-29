"""球员查询服务（阶段 2）。

统计口径（实施方案 §8.5/§12.1，与旧快照有意不同）：
- 基于 ``player_match_stats`` 的全部比赛（含次级联赛、杯赛、升级附加赛）；
- 按**比赛当时**的球队归属（``pms.team_id``）统计，赛季内转会球员会出现
  多个 (赛事, 球队) 分段；
- 只累计 ``appeared = 1`` 的行（引擎为每场已完成比赛写入比赛当时两队
  注册阵容全部 22 行 appeared=1，六项可为 0）。

稳定 ID 收敛规则：
- 真实球员：``real::<姓名slug>``（与 ``players.player_id`` /
  ``player_match_stats.player_id`` 一致）；
- legacy 键（``real::<显示名>`` 或纯显示名，见于 ``awards.player_key`` 与
  ``player_settlements.player_key``）经 ``base.canonical_player_id_for_name``
  收敛到上述稳定 ID；
- 默认球员：``base.default_player_id(team_id, slot_number)`` 合成。
  注意：slot_number 是位置内编号（GK/DF/MF/FW 各自从 1 起），因此
  ``default:<team_id>:<slot>`` 在同队跨位置时可能对应多名默认球员；
  解析时按位置规范序 GK→DF→MF→FW 取第一个候选（确定性行为）。

所有返回值均为 frozen dataclass；所有排序确定性，不调用任何随机源。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from football_simulator.domain.formulas import calculate_player_rating
from football_simulator.models import Player as ModelPlayer
from football_simulator.models import PlayerSeasonStats
from football_simulator.queries import base

#: 结算阶段对应的周数（冬窗第 24 周 / 赛季末第 49 周）。
SETTLEMENT_WEEK_BY_STAGE = {
    base.SETTLEMENT_STAGE_WINTER: 24,
    base.SETTLEMENT_STAGE_FINAL: 49,
}

_STAGE_ORDER = {base.SETTLEMENT_STAGE_WINTER: 0, base.SETTLEMENT_STAGE_FINAL: 1}
_POSITION_ORDER = {"GK": 0, "DF": 1, "MF": 2, "FW": 3}
_AWARD_TYPE_ORDER = ("top_scorer", "assist_leader", "mvp")
_AWARD_TYPE_LABELS = {"top_scorer": "射手王", "assist_leader": "助攻王", "mvp": "MVP"}
_HONOR_FIELDS = ("league_result", "winners_cup_result", "challenge_cup_result", "super_cup_result")
_SKIPPED_HONOR_VALUES = {"未参赛", "未知"}
_LEGACY_REAL_PREFIX = "real::"

STAT_FIELDS = (
    "goals",
    "assists",
    "chances_created",
    "successful_defenses",
    "successful_saves",
    "clean_sheets",
)
_COUNT_FIELDS = ("appeared",) + STAT_FIELDS


# -- DTO ----------------------------------------------------------------


@dataclass(frozen=True)
class PlayerStatLine:
    """出场与六项统计（口径：player_match_stats 全部比赛）。"""

    appeared: int
    goals: int
    assists: int
    chances_created: int
    successful_defenses: int
    successful_saves: int
    clean_sheets: int


@dataclass(frozen=True)
class PlayerDirectoryRow:
    """球员目录行（按赛季口径统计）。"""

    player_id: str  # 稳定 ID
    display_name: str
    position: str
    is_real: bool
    team: base.TeamRef  # 该赛季出场最多的球队（无出场时为当前注册球队）
    additional_teams: List[base.TeamRef]  # 赛季内效力过的其他球队
    ability: int
    appeared: int
    goals: int
    assists: int
    chances_created: int
    successful_defenses: int
    successful_saves: int
    clean_sheets: int
    rating: float  # 按本口径推导的评分
    market_value: Optional[float]  # 该赛季最近一次结算身价；无结算为 None


@dataclass(frozen=True)
class PlayerCompetitionSplit:
    """(赛事, 比赛当时球队) 分段。赛季内转会同赛事会出现多行。"""

    competition: str
    team: base.TeamRef
    stats: PlayerStatLine
    rating: float


@dataclass(frozen=True)
class PlayerMatchRow:
    """比赛日志行（该球员 appeared=1 的每场比赛）。"""

    match_id: str
    week_number: int
    round_number: int
    category: str
    competition: str
    team: base.TeamRef  # 比赛当时所属球队
    opponent: base.TeamRef
    is_home: bool
    home_goals: Optional[int]
    away_goals: Optional[int]
    stats: PlayerStatLine


@dataclass(frozen=True)
class Top20Award:
    rank: int
    score: Optional[float]


@dataclass(frozen=True)
class CompetitionAward:
    competition: str
    award_type: str  # top_scorer / assist_leader / mvp
    score: Optional[float]


@dataclass(frozen=True)
class PlayerSeasonAwards:
    """个人奖项（与团队荣誉 team_honors 严格分开）。"""

    top20: Optional[Top20Award]
    competitions: List[CompetitionAward]


@dataclass(frozen=True)
class SettlementPoint:
    """跨赛季结算轨迹点（来自 player_settlements）。"""

    season_number: int
    stage: str  # winter / final
    week_number: int  # 24 / 49
    rating: Optional[float]
    market_value: Optional[float]


@dataclass(frozen=True)
class PlayerSeasonProfile:
    """球员赛季档案。"""

    identity: base.PlayerRef
    ability: int
    current_team: Optional[base.TeamRef]  # players 表当前注册归属
    season_teams: List[base.TeamRef]  # 该赛季比赛当时归属，按首次出场排序
    season_totals: PlayerStatLine
    competition_splits: List[PlayerCompetitionSplit]
    match_log: List[PlayerMatchRow]
    awards: PlayerSeasonAwards
    team_honors: List[str]  # 所属球队该赛季团队荣誉标签（与个人奖项分开）
    trend: List[SettlementPoint]


@dataclass(frozen=True)
class PlayerCareerSeason:
    season_number: int
    totals: PlayerStatLine
    season_rating: Optional[float]  # 赛季末（final）结算评分
    market_value: Optional[float]  # 赛季末（final）结算身价
    award_labels: List[str]  # 个人奖项摘要标签


@dataclass(frozen=True)
class PlayerCareer:
    identity: base.PlayerRef
    seasons: List[PlayerCareerSeason]
    career_totals: PlayerStatLine


# -- 内部：身份解析 -------------------------------------------------------


@dataclass(frozen=True)
class _ResolvedPlayer:
    stable_id: str
    db_player_id: str  # players / player_match_stats 中的 ID
    display_name: str
    position: str
    ability: int
    is_real: bool
    slot_number: int
    roster_team_id: int


def display_label(name: Optional[str], position: str, slot_number: int) -> str:
    """与 models.Player.label 同规则的显示名。"""
    return name if name else f"默认 {position} {slot_number}"


def _resolve_player(conn: sqlite3.Connection, player_id: str) -> _ResolvedPlayer:
    """把查询层稳定 ID 解析为注册表身份。找不到时抛 KeyError。"""
    row = conn.execute(
        """
        SELECT player_id, name, position, ability, is_real, slot_number, team_id
        FROM players WHERE player_id = ?
        """,
        (player_id,),
    ).fetchone()
    if row is not None and row["is_real"]:
        return _ResolvedPlayer(
            stable_id=row["player_id"],
            db_player_id=row["player_id"],
            display_name=display_label(row["name"], row["position"], int(row["slot_number"])),
            position=row["position"],
            ability=int(row["ability"]),
            is_real=True,
            slot_number=int(row["slot_number"]),
            roster_team_id=int(row["team_id"]),
        )
    if row is not None:
        return _ResolvedPlayer(
            stable_id=base.default_player_id(int(row["team_id"]), int(row["slot_number"])),
            db_player_id=row["player_id"],
            display_name=display_label(row["name"], row["position"], int(row["slot_number"])),
            position=row["position"],
            ability=int(row["ability"]),
            is_real=False,
            slot_number=int(row["slot_number"]),
            roster_team_id=int(row["team_id"]),
        )
    if isinstance(player_id, str) and player_id.startswith("default:"):
        parts = player_id.split(":")
        if len(parts) == 3:
            try:
                team_id, slot_number = int(parts[1]), int(parts[2])
            except ValueError:
                team_id, slot_number = -1, -1
            candidates = conn.execute(
                """
                SELECT player_id, name, position, ability, is_real, slot_number, team_id
                FROM players
                WHERE team_id = ? AND slot_number = ? AND is_real = 0
                ORDER BY CASE position WHEN 'GK' THEN 0 WHEN 'DF' THEN 1 WHEN 'MF' THEN 2 ELSE 3 END,
                         roster_index
                """,
                (team_id, slot_number),
            ).fetchall()
            if candidates:
                row = candidates[0]
                return _ResolvedPlayer(
                    stable_id=player_id,
                    db_player_id=row["player_id"],
                    display_name=display_label(row["name"], row["position"], int(row["slot_number"])),
                    position=row["position"],
                    ability=int(row["ability"]),
                    is_real=False,
                    slot_number=int(row["slot_number"]),
                    roster_team_id=int(row["team_id"]),
                )
    raise KeyError(f"球员不存在：{player_id!r}")


def _canonical_id_from_key(key: Optional[str]) -> Optional[str]:
    """legacy 键（real::<显示名> / 纯显示名）→ 稳定 ID；无法解析返回 None。"""
    if not key:
        return None
    name = key[len(_LEGACY_REAL_PREFIX):] if key.startswith(_LEGACY_REAL_PREFIX) else key
    return base.canonical_player_id_for_name(name)


# -- 内部：统计聚合 -------------------------------------------------------


def _empty_stat_line() -> PlayerStatLine:
    return PlayerStatLine(appeared=0, goals=0, assists=0, chances_created=0, successful_defenses=0, successful_saves=0, clean_sheets=0)


def _stat_line_from_row(row) -> PlayerStatLine:
    return PlayerStatLine(
        appeared=int(row["appeared"]),
        goals=int(row["goals"]),
        assists=int(row["assists"]),
        chances_created=int(row["chances_created"]),
        successful_defenses=int(row["successful_defenses"]),
        successful_saves=int(row["successful_saves"]),
        clean_sheets=int(row["clean_sheets"]),
    )


def _add_stat_line(total: PlayerStatLine, part: PlayerStatLine) -> PlayerStatLine:
    return PlayerStatLine(
        appeared=total.appeared + part.appeared,
        goals=total.goals + part.goals,
        assists=total.assists + part.assists,
        chances_created=total.chances_created + part.chances_created,
        successful_defenses=total.successful_defenses + part.successful_defenses,
        successful_saves=total.successful_saves + part.successful_saves,
        clean_sheets=total.clean_sheets + part.clean_sheets,
    )


def _load_season_segments(
    conn: sqlite3.Connection,
    season_id: int,
    db_player_id: Optional[str] = None,
) -> List[dict]:
    """(球员, 赛事, 球队) 分段聚合。行序确定（赛事规范序 → 球队 ID）。"""
    player_filter = ""
    params: Tuple = (season_id,)
    if db_player_id is not None:
        player_filter = " AND pms.player_id = ?"
        params = (season_id, db_player_id)
    rows = conn.execute(
        f"""
        SELECT pms.player_id AS player_id,
               m.competition AS competition,
               pms.team_id AS team_id,
               COUNT(*) AS appeared,
               SUM(pms.goals) AS goals,
               SUM(pms.assists) AS assists,
               SUM(pms.chances_created) AS chances_created,
               SUM(pms.successful_defenses) AS successful_defenses,
               SUM(pms.successful_saves) AS successful_saves,
               SUM(pms.clean_sheets) AS clean_sheets
        FROM player_match_stats AS pms
        JOIN matches AS m ON m.match_id = pms.match_id
        WHERE m.season_id = ? AND pms.appeared = 1{player_filter}
        GROUP BY pms.player_id, m.competition, pms.team_id
        """,
        params,
    ).fetchall()
    segments = [
        {
            "player_id": row["player_id"],
            "competition": row["competition"],
            "team_id": int(row["team_id"]),
            "stats": _stat_line_from_row(row),
        }
        for row in rows
    ]
    segments.sort(key=lambda item: (_competition_order(item["competition"]), item["team_id"]))
    return segments


def _competition_order(competition: str) -> int:
    try:
        return base.ALL_COMPETITIONS.index(competition)
    except ValueError:
        return len(base.ALL_COMPETITIONS)


def _load_player_match_rows(
    conn: sqlite3.Connection,
    season_id: int,
    db_player_id: str,
) -> List[PlayerMatchRow]:
    """球员该赛季全部 appeared 行，按 (week, ordinal) 排序（确定性平局裁决）。"""
    rows = conn.execute(
        """
        SELECT pms.match_id AS match_id,
               m.week_number AS week_number,
               m.round_number AS round_number,
               m.category AS category,
               m.competition AS competition,
               m.ordinal AS ordinal,
               m.home_team_id AS home_team_id,
               m.away_team_id AS away_team_id,
               m.status AS status,
               m.home_goals AS home_goals,
               m.away_goals AS away_goals,
               pms.team_id AS stat_team_id,
               pms.goals AS goals,
               pms.assists AS assists,
               pms.chances_created AS chances_created,
               pms.successful_defenses AS successful_defenses,
               pms.successful_saves AS successful_saves,
               pms.clean_sheets AS clean_sheets
        FROM player_match_stats AS pms
        JOIN matches AS m ON m.match_id = pms.match_id
        WHERE m.season_id = ? AND pms.player_id = ? AND pms.appeared = 1
        ORDER BY m.week_number, m.ordinal, m.category, m.round_number, m.match_id
        """,
        (season_id, db_player_id),
    ).fetchall()
    teams = base.team_ref_by_id(conn)
    result: List[PlayerMatchRow] = []
    for row in rows:
        stat_team_id = int(row["stat_team_id"])
        home_team_id = int(row["home_team_id"])
        away_team_id = int(row["away_team_id"])
        is_home = stat_team_id == home_team_id
        opponent_id = away_team_id if is_home else home_team_id
        result.append(
            PlayerMatchRow(
                match_id=row["match_id"],
                week_number=int(row["week_number"]),
                round_number=int(row["round_number"]),
                category=row["category"],
                competition=row["competition"],
                team=teams[stat_team_id],
                opponent=teams[opponent_id],
                is_home=is_home,
                home_goals=None if row["home_goals"] is None else int(row["home_goals"]),
                away_goals=None if row["away_goals"] is None else int(row["away_goals"]),
                stats=PlayerStatLine(
                    appeared=1,
                    goals=int(row["goals"]),
                    assists=int(row["assists"]),
                    chances_created=int(row["chances_created"]),
                    successful_defenses=int(row["successful_defenses"]),
                    successful_saves=int(row["successful_saves"]),
                    clean_sheets=int(row["clean_sheets"]),
                ),
            )
        )
    return result


def _first_appearance_order(match_log: List[PlayerMatchRow]) -> Dict[Tuple[str, int], int]:
    """(competition, team_id) → 首次出场序号。match_log 已按 (week, ordinal) 排序，
    因此首次出现的行序即首次出场先后。"""
    order: Dict[Tuple[str, int], int] = {}
    for index, row in enumerate(match_log):
        key = (row.competition, row.team.team_id)
        if key not in order:
            order[key] = index
    return order


def _derived_rating(
    *,
    position: str,
    ability: int,
    is_real: bool,
    stable_id: str,
    display_name: str,
    slot_number: int,
    stats: PlayerStatLine,
) -> float:
    """用冻结公式按查询层口径推导评分（matches_played=该口径出场数）。"""
    model_player = ModelPlayer(
        player_id=stable_id,
        name=display_name,
        position=position,
        ability=int(ability),
        is_real=is_real,
        slot_number=int(slot_number),
    )
    season_stats = PlayerSeasonStats(
        player=model_player,
        team_name="",
        season_number=0,
        appearances=stats.appeared,
        goals=stats.goals,
        assists=stats.assists,
        chances_created=stats.chances_created,
        successful_defenses=stats.successful_defenses,
        successful_saves=stats.successful_saves,
        clean_sheets=stats.clean_sheets,
    )
    return calculate_player_rating(season_stats, stats.appeared)


# -- 内部：结算 / 奖项 / 荣誉 ---------------------------------------------


def _load_settlement_index(conn: sqlite3.Connection) -> Dict[str, Dict[int, Dict[str, Tuple[Optional[float], Optional[float]]]]]:
    """稳定 ID → season_number → stage → (rating, market_value)。"""
    rows = conn.execute(
        """
        SELECT s.season_number AS season_number,
               ps.stage AS stage,
               ps.player_key AS player_key,
               ps.season_rating AS season_rating,
               ps.market_value AS market_value
        FROM player_settlements AS ps
        JOIN seasons AS s ON s.season_id = ps.season_id
        """
    ).fetchall()
    index: Dict[str, Dict[int, Dict[str, Tuple[Optional[float], Optional[float]]]]] = {}
    for row in rows:
        stable_id = _canonical_id_from_key(row["player_key"])
        if stable_id is None:
            continue
        per_season = index.setdefault(stable_id, {})
        per_stage = per_season.setdefault(int(row["season_number"]), {})
        per_stage[row["stage"]] = (
            None if row["season_rating"] is None else float(row["season_rating"]),
            None if row["market_value"] is None else float(row["market_value"]),
        )
    return index


def _latest_season_market_value(index_entry: Dict[str, Tuple[Optional[float], Optional[float]]]) -> Optional[float]:
    """该赛季最近一次结算身价：final 优先，其次 winter；无结算为 None。"""
    if base.SETTLEMENT_STAGE_FINAL in index_entry:
        return index_entry[base.SETTLEMENT_STAGE_FINAL][1]
    if base.SETTLEMENT_STAGE_WINTER in index_entry:
        return index_entry[base.SETTLEMENT_STAGE_WINTER][1]
    return None


def _load_award_rows(
    conn: sqlite3.Connection,
    season_id: Optional[int] = None,
) -> List[dict]:
    """awards 表行 → {canonical_id, competition, award_type, rank, score}。"""
    if season_id is None:
        rows = conn.execute(
            """
            SELECT season_id, competition, award_type, rank, player_key, score
            FROM awards
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT season_id, competition, award_type, rank, player_key, score
            FROM awards WHERE season_id = ?
            """,
            (season_id,),
        ).fetchall()
    result = []
    for row in rows:
        result.append(
            {
                "season_id": int(row["season_id"]),
                "canonical_id": _canonical_id_from_key(row["player_key"]),
                "competition": row["competition"],
                "award_type": row["award_type"],
                "rank": None if row["rank"] is None else int(row["rank"]),
                "score": None if row["score"] is None else float(row["score"]),
            }
        )
    return result


def _build_season_awards(award_rows: List[dict]) -> PlayerSeasonAwards:
    top20_rows = [row for row in award_rows if row["award_type"] == "top20" and row["rank"] is not None]
    top20 = None
    if top20_rows:
        best = min(top20_rows, key=lambda row: (row["rank"], row["award_type"]))
        top20 = Top20Award(rank=best["rank"], score=best["score"])
    competition_awards = [
        CompetitionAward(competition=row["competition"], award_type=row["award_type"], score=row["score"])
        for row in award_rows
        if row["award_type"] != "top20"
    ]
    competition_awards.sort(
        key=lambda award: (
            _competition_order(award.competition or ""),
            _AWARD_TYPE_ORDER.index(award.award_type) if award.award_type in _AWARD_TYPE_ORDER else len(_AWARD_TYPE_ORDER),
            award.competition or "",
        )
    )
    return PlayerSeasonAwards(top20=top20, competitions=competition_awards)


def _award_labels(award_rows: List[dict]) -> List[str]:
    """个人奖项摘要标签（Top20 优先，其后按赛事规范序）。"""
    labels: List[str] = []
    top20_rows = [row for row in award_rows if row["award_type"] == "top20" and row["rank"] is not None]
    if top20_rows:
        best = min(top20_rows, key=lambda row: (row["rank"], row["award_type"]))
        labels.append(f"Top20 第 {best['rank']} 名")
    for award in _build_season_awards(award_rows).competitions:
        type_label = _AWARD_TYPE_LABELS.get(award.award_type, award.award_type)
        labels.append(f"{award.competition}{type_label}")
    return labels


def _load_team_honors(conn: sqlite3.Connection, season_id: int, team_names: List[str]) -> List[str]:
    """从 season_archives 的 team_stats 提取所属球队团队荣誉（只取非"未参赛"/"未知"）。"""
    row = conn.execute(
        "SELECT archive_json FROM season_archives WHERE season_id = ?",
        (season_id,),
    ).fetchone()
    if row is None:
        return []
    archive = json.loads(row["archive_json"])
    stats_by_name = {entry.get("team_name"): entry for entry in archive.get("team_stats", [])}
    honors: List[str] = []
    for name in team_names:
        entry = stats_by_name.get(name)
        if not entry:
            continue
        for field in _HONOR_FIELDS:
            value = entry.get(field)
            if value and value not in _SKIPPED_HONOR_VALUES:
                honors.append(str(value))
    return honors


def _build_trend(
    settlement_index: Dict[str, Dict[int, Dict[str, Tuple[Optional[float], Optional[float]]]]],
    stable_id: str,
) -> List[SettlementPoint]:
    points: List[SettlementPoint] = []
    for season_number, per_stage in settlement_index.get(stable_id, {}).items():
        for stage, (rating, market_value) in per_stage.items():
            week_number = SETTLEMENT_WEEK_BY_STAGE.get(stage)
            if week_number is None:
                continue
            points.append(
                SettlementPoint(
                    season_number=season_number,
                    stage=stage,
                    week_number=week_number,
                    rating=rating,
                    market_value=market_value,
                )
            )
    points.sort(key=lambda point: (point.season_number, _STAGE_ORDER.get(point.stage, 99), point.week_number))
    return points


# -- 对外 API ------------------------------------------------------------


def list_players(
    conn: sqlite3.Connection,
    season_number: int,
    *,
    search: Optional[str] = None,
    position: Optional[str] = None,
    team_id: Optional[int] = None,
    competition: Optional[str] = None,
    is_real: Optional[bool] = None,
) -> List[PlayerDirectoryRow]:
    """球员目录（按赛季口径统计）。

    - 目录范围：当前注册阵容（players 表）；对已完结赛季只收录该赛季
      出场过的球员（active 赛季则收录完整阵容，供开季空数据期使用）。
    - 所属球队 = 该赛季（比赛当时归属）出场最多的队；无出场时回退当前
      注册球队；赛季内转会的其他球队进入 additional_teams。
    - competition 过滤：统计只计该赛事，且只返回在该赛事出场过的球员。
    - team_id 过滤：按目录行的所属球队（primary team）过滤。
    - 排序：所属球队名 → 位置规范序 → 显示名 → 稳定 ID（确定性）。
    """
    season_id = base.season_id_for(conn, season_number)
    season_row = conn.execute(
        "SELECT status FROM seasons WHERE season_id = ?",
        (season_id,),
    ).fetchone()
    season_is_active = season_row is not None and season_row["status"] == "active"
    teams = base.team_ref_by_id(conn)
    settlement_index = _load_settlement_index(conn)
    segments_by_player: Dict[str, List[dict]] = {}
    for segment in _load_season_segments(conn, season_id):
        segments_by_player.setdefault(segment["player_id"], []).append(segment)

    roster = conn.execute(
        """
        SELECT player_id, name, position, ability, is_real, slot_number, team_id
        FROM players
        ORDER BY team_id, roster_index
        """
    ).fetchall()

    search_lower = search.lower() if search else None
    rows: List[PlayerDirectoryRow] = []
    for prow in roster:
        db_player_id = prow["player_id"]
        row_is_real = bool(prow["is_real"])
        slot_number = int(prow["slot_number"])
        stable_id = prow["player_id"] if row_is_real else base.default_player_id(int(prow["team_id"]), slot_number)
        display_name = display_label(prow["name"], prow["position"], slot_number)

        segments = segments_by_player.get(db_player_id, [])
        season_appeared = sum(segment["stats"].appeared for segment in segments)
        if season_appeared == 0 and not season_is_active:
            # 已完结赛季的目录只收录该赛季出场过的球员。
            continue

        if competition is not None:
            scoped = [segment for segment in segments if segment["competition"] == competition]
            if sum(segment["stats"].appeared for segment in scoped) == 0:
                continue
        else:
            scoped = segments

        totals = _empty_stat_line()
        for segment in scoped:
            totals = _add_stat_line(totals, segment["stats"])

        if segments:
            appeared_by_team: Dict[int, int] = {}
            for segment in segments:
                appeared_by_team[segment["team_id"]] = appeared_by_team.get(segment["team_id"], 0) + segment["stats"].appeared
            ordered_teams = sorted(appeared_by_team.items(), key=lambda item: (-item[1], item[0]))
            primary_team_id = ordered_teams[0][0]
            additional_ids = [team_id_iter for team_id_iter, _ in ordered_teams[1:]]
        else:
            primary_team_id = int(prow["team_id"])
            additional_ids = []
        if team_id is not None and primary_team_id != int(team_id):
            continue
        if position is not None and prow["position"] != position:
            continue
        if is_real is not None and is_real != row_is_real:
            continue
        if search_lower is not None and search_lower not in display_name.lower():
            continue

        primary_team = teams.get(primary_team_id)
        if primary_team is None:
            raise KeyError(f"注册表中的球队不存在：{primary_team_id}")
        settlement_entry = settlement_index.get(stable_id, {}).get(int(season_number), {})
        rows.append(
            PlayerDirectoryRow(
                player_id=stable_id,
                display_name=display_name,
                position=prow["position"],
                is_real=row_is_real,
                team=primary_team,
                additional_teams=[teams[tid] for tid in additional_ids if tid in teams],
                ability=int(prow["ability"]),
                appeared=totals.appeared,
                goals=totals.goals,
                assists=totals.assists,
                chances_created=totals.chances_created,
                successful_defenses=totals.successful_defenses,
                successful_saves=totals.successful_saves,
                clean_sheets=totals.clean_sheets,
                rating=_derived_rating(
                    position=prow["position"],
                    ability=int(prow["ability"]),
                    is_real=row_is_real,
                    stable_id=stable_id,
                    display_name=display_name,
                    slot_number=slot_number,
                    stats=totals,
                ),
                market_value=_latest_season_market_value(settlement_entry),
            )
        )

    rows.sort(
        key=lambda row: (
            row.team.display_name,
            _POSITION_ORDER.get(row.position, len(_POSITION_ORDER)),
            row.display_name,
            row.player_id,
        )
    )
    return rows


def get_player_season_profile(
    conn: sqlite3.Connection,
    player_id: str,
    season_number: int,
) -> PlayerSeasonProfile:
    """球员赛季档案。球员或赛季不存在时抛 KeyError。"""
    identity = _resolve_player(conn, player_id)
    season_id = base.season_id_for(conn, season_number)
    teams = base.team_ref_by_id(conn)

    match_log = _load_player_match_rows(conn, season_id, identity.db_player_id)
    segments = _load_season_segments(conn, season_id, identity.db_player_id)

    season_totals = _empty_stat_line()
    for segment in segments:
        season_totals = _add_stat_line(season_totals, segment["stats"])

    first_order = _first_appearance_order(match_log)
    splits: List[PlayerCompetitionSplit] = []
    for segment in segments:
        team_ref = teams.get(segment["team_id"])
        if team_ref is None:
            raise KeyError(f"比赛统计引用的球队不存在：{segment['team_id']}")
        splits.append(
            PlayerCompetitionSplit(
                competition=segment["competition"],
                team=team_ref,
                stats=segment["stats"],
                rating=_derived_rating(
                    position=identity.position,
                    ability=identity.ability,
                    is_real=identity.is_real,
                    stable_id=identity.stable_id,
                    display_name=identity.display_name,
                    slot_number=identity.slot_number,
                    stats=segment["stats"],
                ),
            )
        )
    splits.sort(
        key=lambda split: (
            _competition_order(split.competition),
            first_order.get((split.competition, split.team.team_id), 10 ** 9),
            split.team.team_id,
        )
    )

    season_team_ids: List[int] = []
    for row in match_log:
        if row.team.team_id not in season_team_ids:
            season_team_ids.append(row.team.team_id)
    season_teams = [teams[tid] for tid in season_team_ids]

    award_rows = [row for row in _load_award_rows(conn, season_id) if row["canonical_id"] == identity.stable_id]

    current_team = teams.get(identity.roster_team_id)
    return PlayerSeasonProfile(
        identity=base.PlayerRef(
            player_id=identity.stable_id,
            display_name=identity.display_name,
            position=identity.position,
            is_real=identity.is_real,
        ),
        ability=identity.ability,
        current_team=current_team,
        season_teams=season_teams,
        season_totals=season_totals,
        competition_splits=splits,
        match_log=match_log,
        awards=_build_season_awards(award_rows),
        team_honors=_load_team_honors(conn, season_id, [team.display_name for team in season_teams]),
        trend=_build_trend(_load_settlement_index(conn), identity.stable_id),
    )


def get_player_career(conn: sqlite3.Connection, player_id: str) -> PlayerCareer:
    """球员生涯：各赛季汇总 + 生涯总计。球员不存在时抛 KeyError。"""
    identity = _resolve_player(conn, player_id)
    rows = conn.execute(
        """
        SELECT s.season_number AS season_number,
               COUNT(*) AS appeared,
               SUM(pms.goals) AS goals,
               SUM(pms.assists) AS assists,
               SUM(pms.chances_created) AS chances_created,
               SUM(pms.successful_defenses) AS successful_defenses,
               SUM(pms.successful_saves) AS successful_saves,
               SUM(pms.clean_sheets) AS clean_sheets
        FROM player_match_stats AS pms
        JOIN matches AS m ON m.match_id = pms.match_id
        JOIN seasons AS s ON s.season_id = m.season_id
        WHERE pms.player_id = ? AND pms.appeared = 1
        GROUP BY s.season_number
        ORDER BY s.season_number
        """,
        (identity.db_player_id,),
    ).fetchall()

    season_numbers = [int(row["season_number"]) for row in rows]
    season_ids = {
        int(row["season_number"]): int(row["season_id"])
        for row in conn.execute("SELECT season_id, season_number FROM seasons")
    }
    awards_by_season: Dict[int, List[dict]] = {}
    for award in _load_award_rows(conn):
        if award["canonical_id"] != identity.stable_id:
            continue
        for number in season_numbers:
            if season_ids.get(number) == award["season_id"]:
                awards_by_season.setdefault(number, []).append(award)

    settlement_index = _load_settlement_index(conn)
    per_season_settlement = settlement_index.get(identity.stable_id, {})

    seasons: List[PlayerCareerSeason] = []
    career_totals = _empty_stat_line()
    for row in rows:
        season_number = int(row["season_number"])
        totals = _stat_line_from_row(row)
        career_totals = _add_stat_line(career_totals, totals)
        final_entry = per_season_settlement.get(season_number, {}).get(base.SETTLEMENT_STAGE_FINAL)
        seasons.append(
            PlayerCareerSeason(
                season_number=season_number,
                totals=totals,
                season_rating=None if final_entry is None else final_entry[0],
                market_value=None if final_entry is None else final_entry[1],
                award_labels=_award_labels(awards_by_season.get(season_number, [])),
            )
        )

    return PlayerCareer(
        identity=base.PlayerRef(
            player_id=identity.stable_id,
            display_name=identity.display_name,
            position=identity.position,
            is_real=identity.is_real,
        ),
        seasons=seasons,
        career_totals=career_totals,
    )
