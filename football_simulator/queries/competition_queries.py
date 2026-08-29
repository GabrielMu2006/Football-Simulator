"""赛事维度的只读查询（阶段 2）。

覆盖六个规范赛事（一级联赛/次级联赛/优胜者杯/挑战杯/超级杯/升级附加赛）：
- 联赛积分榜与 team_queries 共用同一排名链（domain.standings）与数据源
  （matches 中 category='premier'/'second'），两侧结果必然一致。
- 杯赛 stage_rows 由 ``matches`` 表（round/ordinal 稳定主键）与
  ``season_runtime.cup_state`` 的签表/晋级记录共同重建；排序固定为
  ``(round_number, match_id)``，绝不依赖无序集合或显示文本推断。
- 榜单（射手/助攻/评分）基于 ``player_match_stats`` 按赛事聚合，评分使用
  ``domain.formulas.calculate_player_rating``；并列时按
  统计降序 → 评分降序 → 能力降序 → 名称升序 确定性排序。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from football_simulator.domain import formulas, standings
from football_simulator.models import Fixture, MatchResult, Player, PlayerSeasonStats, TableRow, Team
from football_simulator.queries import base

DIVISION_PREMIER = base.COMPETITION_PREMIER
DIVISION_SECOND = base.COMPETITION_SECOND

# 赛事进行状态语义：
#   未开始 —— 本届赛事存在但还没有已完成比赛；
#   进行中 —— 已有已完成比赛且冠军/晋级方未全部产生；
#   已结束 —— 冠军（联赛）或晋级方（附加赛）已产生；
#   未举办 —— 杯赛在本赛季未激活（优胜者杯/挑战杯自第 2 赛季、超级杯自
#             第 3 赛季起举办；第 1 赛季无杯赛）。
STATUS_NOT_STARTED = "未开始"
STATUS_IN_PROGRESS = "进行中"
STATUS_FINISHED = "已结束"
STATUS_NOT_HELD = "未举办"

LEADERBOARD_SIZE = 10

# 排名链占位阵容（Team 构造要求 1GK/4DF/3MF/3FW，排名计算只使用队名）。
_PLACEHOLDER_SLOTS = (("GK", (1,)), ("DF", (1, 2, 3, 4)), ("MF", (1, 2, 3)), ("FW", (1, 2, 3)))
_PLACEHOLDER_TEAMS: Dict[str, Team] = {}

_DIVISION_BY_CATEGORY = {"premier": DIVISION_PREMIER, "second": DIVISION_SECOND}
_LEAGUE_CATEGORIES = ("premier", "second")

# 杯赛 (competition, round_number) -> 运行时事件键。同一赛事内轮次号唯一。
_EVENT_BY_ROUND: Dict[str, Dict[int, str]] = {
    base.COMPETITION_WINNERS_CUP: {
        1: "winners_cup_group_1",
        2: "winners_cup_group_2",
        3: "winners_cup_group_3",
        4: "winners_cup_group_4",
        5: "winners_cup_group_5",
        6: "winners_cup_group_6",
        7: "winners_cup_quarterfinal_leg_1",
        8: "winners_cup_quarterfinal_leg_2",
        9: "winners_cup_semifinal_leg_1",
        10: "winners_cup_semifinal_leg_2",
        11: "winners_cup_final_leg_1",
        12: "winners_cup_final_leg_2",
    },
    base.COMPETITION_CHALLENGE_CUP: {
        1: "challenge_cup_r32",
        2: "challenge_cup_r16",
        3: "challenge_cup_quarterfinal",
        4: "challenge_cup_semifinal",
        5: "challenge_cup_final",
    },
    base.COMPETITION_SUPER_CUP: {1: "super_cup_semifinal", 2: "super_cup_final"},
}
_CUP_STATE_KEY = {
    base.COMPETITION_WINNERS_CUP: "winners_cup",
    base.COMPETITION_CHALLENGE_CUP: "challenge_cup",
    base.COMPETITION_SUPER_CUP: "super_cup",
}
# 优胜者杯淘汰赛：本回合 leg_1 -> 下一回合 leg_1（下一回合签表参赛者即晋级方）。
_WINNERS_NEXT_LEG1 = {
    "winners_cup_quarterfinal_leg_1": "winners_cup_semifinal_leg_1",
    "winners_cup_semifinal_leg_1": "winners_cup_final_leg_1",
}

AWARD_TYPE_ORDER = {"top_scorer": 0, "assist_leader": 1, "mvp": 2}


# -- DTO ----------------------------------------------------------------


@dataclass(frozen=True)
class StandingRow:
    """联赛积分榜行（20 行，含 team_id）。"""

    rank: Optional[int]  # 名次；整榜 0 场时为 None
    team_id: int
    team_name: str
    played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    points: int


@dataclass(frozen=True)
class LeaderboardEntry:
    """球员在单一赛事内的聚合统计行。"""

    player: base.PlayerRef
    ability: int
    matches_played: int
    goals: int
    assists: int
    chances_created: int
    successful_defenses: int
    successful_saves: int
    clean_sheets: int
    rating: float


@dataclass(frozen=True)
class CompetitionLeaderboards:
    """赛事榜单（各前 10，确定性排序）。"""

    top_scorers: Tuple[LeaderboardEntry, ...]  # 按进球
    top_assisters: Tuple[LeaderboardEntry, ...]  # 按助攻
    top_rated: Tuple[LeaderboardEntry, ...]  # 按评分


@dataclass(frozen=True)
class AwardLine:
    """awards 表行的赛事投影（player_key 已转稳定 ID）。"""

    player: base.PlayerRef
    award_type: str  # top20 / top_scorer / assist_leader / mvp
    rank: Optional[int]
    score: Optional[float]
    team_name: Optional[str]


@dataclass(frozen=True)
class CupStageRow:
    """杯赛单场比赛行（含该淘汰赛段的晋级方）。"""

    round_number: int
    match: base.MatchRef
    match_winner: Optional[base.TeamRef]  # 单场比分胜者；平局为 None
    advancing: Optional[base.TeamRef]  # 晋级/夺冠方；小组赛或未决出为 None


@dataclass(frozen=True)
class CompetitionOverview:
    """赛事一览行。"""

    competition: base.CompetitionRef
    season_number: int
    status: str  # 未开始 / 进行中 / 已结束 / 未举办（语义见模块常量注释）
    completed_matches: int
    total_matches: Optional[int]  # 联赛固定 380；杯赛/附加赛赛程不预生成，为 None
    champion: Optional[str]


@dataclass(frozen=True)
class CompetitionProfile:
    """赛事详情。

    - 联赛：``standings`` 20 行，``stage_rows`` 为空。
    - 杯赛：``standings`` 为 None，``stage_rows`` 按轮次重建。
    - 升级附加赛：``standings`` 为 None，``stage_rows`` 为空，``champion``
      即升级成功方（来自归档 last_transition，或按引擎两回合规则从
      matches + 运行时签表确定性推得）。
    """

    competition: base.CompetitionRef
    season_number: int
    standings: Optional[Tuple[StandingRow, ...]]
    stage_rows: Tuple[CupStageRow, ...]
    matches: Tuple[base.MatchRef, ...]
    leaderboards: CompetitionLeaderboards
    awards: Tuple[AwardLine, ...]
    champion: Optional[str]


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


def load_runtime(conn: sqlite3.Connection, season_id: int) -> dict:
    row = conn.execute("SELECT data_json FROM season_runtime WHERE season_id = ?", (season_id,)).fetchone()
    if row is None:
        return {}
    return json.loads(row["data_json"])


def compute_division_standings(
    conn: sqlite3.Connection,
    season_id: int,
    category: str,
    resolutions: List[dict],
) -> List[TableRow]:
    """按排名链重放某分区全部已完成联赛比赛（与 team_queries 同一数据源）。"""
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
    archive = load_archive(conn, season_number)
    if not archive:
        return []
    return list(archive.get("ranking_playoffs", {}).get(_DIVISION_BY_CATEGORY[category], []))


def league_standings_rows(
    conn: sqlite3.Connection,
    season_id: int,
    season_number: int,
    category: str,
) -> Tuple[StandingRow, ...]:
    ordered = compute_division_standings(conn, season_id, category, _resolutions_for(conn, season_number, category))
    ids_by_name = {row["name"]: int(row["team_id"]) for row in conn.execute("SELECT team_id, name FROM teams")}
    rows: List[StandingRow] = []
    for index, row in enumerate(ordered, start=1):
        rows.append(
            StandingRow(
                rank=index if row.played > 0 else None,
                team_id=ids_by_name.get(row.team.name, -1),
                team_name=row.team.name,
                played=row.played,
                wins=row.wins,
                draws=row.draws,
                losses=row.losses,
                goals_for=row.goals_for,
                goals_against=row.goals_against,
                points=row.points,
            )
        )
    return tuple(rows)


def _match_refs(conn: sqlite3.Connection, season_id: int, season_number: int, extra_sql: str, parameters: tuple) -> List[base.MatchRef]:
    refs_by_id = base.team_ref_by_id(conn)
    rows = conn.execute(
        f"""
        SELECT match_id, competition, week_number, round_number, ordinal,
               home_team_id, away_team_id, status, home_goals, away_goals
        FROM matches
        WHERE season_id = ? {extra_sql}
        ORDER BY week_number, round_number, category, ordinal
        """,
        (season_id, *parameters),
    ).fetchall()
    refs: List[base.MatchRef] = []
    for row in rows:
        completed = row["status"] == base.MATCH_STATUS_COMPLETED
        refs.append(
            base.MatchRef(
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
        )
    return refs


def _player_identity(conn: sqlite3.Connection) -> Dict[str, Tuple[base.PlayerRef, int]]:
    """players 表中的 player_id -> (稳定 PlayerRef, ability)。

    默认球员的稳定 ID 按 (team_id, slot_number) 合成；真实球员的稳定 ID 即
    注册表 ID（``real::<slug>``）。
    """
    identity: Dict[str, Tuple[base.PlayerRef, int]] = {}
    for row in conn.execute(
        """
        SELECT player_id, name, position, ability, is_real, team_id, slot_number
        FROM players
        """
    ):
        is_real = bool(row["is_real"])
        display_name = row["name"] or f"默认 {row['position']} {int(row['slot_number'])}"
        if is_real:
            player_id = row["player_id"]
        else:
            player_id = base.default_player_id(int(row["team_id"]), int(row["slot_number"]))
        identity[row["player_id"]] = (
            base.PlayerRef(player_id=player_id, display_name=display_name, position=row["position"], is_real=is_real),
            int(row["ability"]),
        )
    return identity


_DEFAULT_ABILITY_SQL = "SELECT ability FROM players WHERE is_real = 0 ORDER BY team_id, roster_index LIMIT 1"
_POSITION_TOKENS = {"gk", "df", "mf", "fw"}


def _default_ability_fallback(conn: sqlite3.Connection) -> int:
    """默认球员的统一基础能力（配置值，全联盟一致）；无默认球员时为 0。"""
    row = conn.execute(_DEFAULT_ABILITY_SQL).fetchone()
    return int(row["ability"]) if row else 0


def _synthetic_default_ref(player_id: str, team_id: int) -> Optional[base.PlayerRef]:
    """比赛统计中已不在注册表的默认球员 ID -> 稳定身份。

    默认球员 ID 形如 ``{队名slug}-{gk|df|mf|fw}-{槽位}[-default]``（阶段 1 起
    替换产生的占位球员带 ``-default`` 后缀）。同一 (球队, 槽位) 的默认球员
    可能先后以两种 ID 出场，统一收敛到 ``base.default_player_id``。
    """
    base_id = player_id[: -len("-default")] if player_id.endswith("-default") else player_id
    parts = base_id.rsplit("-", 2)
    if len(parts) != 3:
        return None
    _slug, position_token, slot_token = parts
    if position_token not in _POSITION_TOKENS or not slot_token.isdigit():
        return None
    slot = int(slot_token)
    position = position_token.upper()
    return base.PlayerRef(
        player_id=base.default_player_id(team_id, slot),
        display_name=f"默认 {position} {slot}",
        position=position,
        is_real=False,
    )


def _aggregate_competition_stats(conn: sqlite3.Connection, season_id: int, competition: str) -> List[LeaderboardEntry]:
    """按赛事聚合 player_match_stats（appeared=1，含全部六项统计）。

    聚合键是稳定 ID：注册表中的球员直接映射；赛季中被替换掉的默认球员
    （比赛时 ID 已不在当前注册表）按 ID 中的槽位 + 比赛当时 team_id 合成
    身份，同一 (球队, 槽位) 的多个历史 ID 合并为一条。
    """
    identity = _player_identity(conn)
    fallback_ability = _default_ability_fallback(conn)
    accumulated: Dict[str, Dict[str, object]] = {}
    for row in conn.execute(
        """
        SELECT pms.player_id AS player_id,
               MIN(pms.team_id) AS team_id,
               SUM(pms.appeared) AS matches_played,
               SUM(pms.goals) AS goals,
               SUM(pms.assists) AS assists,
               SUM(pms.chances_created) AS chances_created,
               SUM(pms.successful_defenses) AS successful_defenses,
               SUM(pms.successful_saves) AS successful_saves,
               SUM(pms.clean_sheets) AS clean_sheets
        FROM player_match_stats AS pms
        JOIN matches AS m ON m.match_id = pms.match_id
        WHERE m.season_id = ? AND m.competition = ? AND pms.appeared = 1
        GROUP BY pms.player_id
        """,
        (season_id, competition),
    ):
        raw_id = row["player_id"]
        known = identity.get(raw_id)
        if known is not None:
            ref, ability = known
        else:
            synthetic = _synthetic_default_ref(raw_id, int(row["team_id"] or 0))
            if synthetic is None:
                continue
            ref, ability = synthetic, fallback_ability
        bucket = accumulated.get(ref.player_id)
        if bucket is None:
            bucket = accumulated[ref.player_id] = {
                "ref": ref,
                "ability": ability,
                "matches_played": 0,
                "goals": 0,
                "assists": 0,
                "chances_created": 0,
                "successful_defenses": 0,
                "successful_saves": 0,
                "clean_sheets": 0,
            }
        bucket["matches_played"] += int(row["matches_played"] or 0)
        bucket["goals"] += int(row["goals"] or 0)
        bucket["assists"] += int(row["assists"] or 0)
        bucket["chances_created"] += int(row["chances_created"] or 0)
        bucket["successful_defenses"] += int(row["successful_defenses"] or 0)
        bucket["successful_saves"] += int(row["successful_saves"] or 0)
        bucket["clean_sheets"] += int(row["clean_sheets"] or 0)

    entries: List[LeaderboardEntry] = []
    for bucket in accumulated.values():
        stats = PlayerSeasonStats(
            player=Player(
                player_id=bucket["ref"].player_id,
                name=bucket["ref"].display_name,
                position=bucket["ref"].position,
                ability=int(bucket["ability"]),
                is_real=bucket["ref"].is_real,
                slot_number=0,
            ),
            team_name="",
        )
        stats.goals = int(bucket["goals"])
        stats.assists = int(bucket["assists"])
        stats.chances_created = int(bucket["chances_created"])
        stats.successful_defenses = int(bucket["successful_defenses"])
        stats.successful_saves = int(bucket["successful_saves"])
        stats.clean_sheets = int(bucket["clean_sheets"])
        matches_played = int(bucket["matches_played"])
        entries.append(
            LeaderboardEntry(
                player=bucket["ref"],
                ability=int(bucket["ability"]),
                matches_played=matches_played,
                goals=stats.goals,
                assists=stats.assists,
                chances_created=stats.chances_created,
                successful_defenses=stats.successful_defenses,
                successful_saves=stats.successful_saves,
                clean_sheets=stats.clean_sheets,
                rating=formulas.calculate_player_rating(stats, matches_played),
            )
        )
    return entries


def _leaderboards(
    conn: sqlite3.Connection,
    season_id: int,
    competition: str,
    is_real: Optional[bool] = None,
) -> CompetitionLeaderboards:
    entries = _aggregate_competition_stats(conn, season_id, competition)
    if is_real is not None:
        # “只显示真实球员”必须在截取前 N 名之前过滤，否则排名失真。
        entries = [entry for entry in entries if entry.player.is_real == is_real]
    # 并列时确定性排序：统计降序 → 评分降序 → 能力降序 → 名称升序。
    # 评分榜的主统计即评分，并列时以（进球+助攻）作次级统计（与引擎榜单一致）。
    scorers = sorted(entries, key=lambda e: (-e.goals, -e.rating, -e.ability, e.player.display_name))
    assisters = sorted(entries, key=lambda e: (-e.assists, -e.rating, -e.ability, e.player.display_name))
    rated = sorted(entries, key=lambda e: (-e.rating, -(e.goals + e.assists), -e.ability, e.player.display_name))
    return CompetitionLeaderboards(
        top_scorers=tuple(scorers[:LEADERBOARD_SIZE]),
        top_assisters=tuple(assisters[:LEADERBOARD_SIZE]),
        top_rated=tuple(rated[:LEADERBOARD_SIZE]),
    )


def _competition_awards(conn: sqlite3.Connection, season_id: int, competition: str) -> Tuple[AwardLine, ...]:
    """awards 表中该赛事的奖项行（player_key 经显示名收敛为稳定 ID）。"""
    positions_by_name = {
        row["name"]: row["position"]
        for row in conn.execute("SELECT name, position FROM players WHERE is_real = 1 AND name IS NOT NULL")
    }
    lines: List[AwardLine] = []
    for row in conn.execute(
        """
        SELECT award_type, rank, player_key, player_label, team_name, score
        FROM awards WHERE season_id = ? AND competition = ?
        ORDER BY award_row_id
        """,
        (season_id, competition),
    ):
        label = row["player_label"] or row["player_key"] or ""
        lines.append(
            AwardLine(
                player=base.PlayerRef(
                    player_id=base.canonical_player_id_for_name(label),
                    display_name=label,
                    position=positions_by_name.get(label, ""),
                    is_real=True,
                ),
                award_type=row["award_type"],
                rank=int(row["rank"]) if row["rank"] is not None else None,
                score=float(row["score"]) if row["score"] is not None else None,
                team_name=row["team_name"],
            )
        )
    lines.sort(key=lambda line: (AWARD_TYPE_ORDER.get(line.award_type, 9), line.rank if line.rank is not None else 10_000))
    return tuple(lines)


# -- 杯赛 stage_rows ----------------------------------------------------


def _cup_stage_rows(
    conn: sqlite3.Connection,
    season_id: int,
    season_number: int,
    competition: str,
    cup_state: dict,
    refs_by_name: Dict[str, base.TeamRef],
) -> Tuple[CupStageRow, ...]:
    cup = cup_state.get(_CUP_STATE_KEY.get(competition, ""), {})
    event_by_round = _EVENT_BY_ROUND.get(competition, {})
    knockout_pairs = cup.get("knockout_pairs") or {}
    event_winners = cup.get("winners") or {}
    refs_by_id = base.team_ref_by_id(conn)
    rows: List[CupStageRow] = []
    for row in conn.execute(
        """
        SELECT match_id, competition, week_number, round_number, ordinal,
               home_team_id, away_team_id, status, home_goals, away_goals
        FROM matches
        WHERE season_id = ? AND category = 'cup' AND competition = ?
        ORDER BY round_number, ordinal, match_id
        """,
        (season_id, competition),
    ):
        round_number = int(row["round_number"])
        event_key = event_by_round.get(round_number)
        completed = row["status"] == base.MATCH_STATUS_COMPLETED
        match = base.MatchRef(
            match_id=row["match_id"],
            season_number=season_number,
            competition=row["competition"],
            week_number=int(row["week_number"]),
            round_number=round_number,
            status=row["status"],
            home=refs_by_id[int(row["home_team_id"])],
            away=refs_by_id[int(row["away_team_id"])],
            home_goals=int(row["home_goals"]) if completed else None,
            away_goals=int(row["away_goals"]) if completed else None,
        )
        match_winner = None
        if completed and row["home_goals"] is not None and row["away_goals"] is not None:
            if int(row["home_goals"]) > int(row["away_goals"]):
                match_winner = match.home
            elif int(row["away_goals"]) > int(row["home_goals"]):
                match_winner = match.away
        advancing = _advancing_team(
            competition,
            event_key,
            cup,
            knockout_pairs,
            event_winners,
            int(row["ordinal"]),
            match,
            refs_by_name,
        )
        rows.append(CupStageRow(round_number=round_number, match=match, match_winner=match_winner, advancing=advancing))
    # 契约要求：按 round_number + match_id 确定性排序。
    rows.sort(key=lambda item: (item.round_number, item.match.match_id))
    return tuple(rows)


def _ref_by_name(refs_by_name: Dict[str, base.TeamRef], name: Optional[str]) -> Optional[base.TeamRef]:
    if not name:
        return None
    return refs_by_name.get(name)


def _advancing_team(
    competition: str,
    event_key: Optional[str],
    cup: dict,
    knockout_pairs: dict,
    event_winners: dict,
    ordinal: int,
    match: base.MatchRef,
    refs_by_name: Dict[str, base.TeamRef],
) -> Optional[base.TeamRef]:
    """该场比赛所属淘汰赛段的晋级方；小组赛或尚未决出时为 None。

    数据来源全部是 cup_state 的结构性签表/晋级记录（下一回合签表参赛者、
    winners 列表、finalists、champion），不依赖显示文本推断。
    """
    if event_key is None:
        return None
    if competition == base.COMPETITION_WINNERS_CUP:
        if event_key.startswith("winners_cup_group_"):
            return None
        leg1_key = event_key[:-len("_leg_2")] + "_leg_1" if event_key.endswith("_leg_2") else event_key
        pairs = knockout_pairs.get(leg1_key) or []
        if ordinal >= len(pairs):
            return None
        pair = pairs[ordinal]
        pair_teams = {pair.get("home"), pair.get("away")} - {None}
        if leg1_key == "winners_cup_final_leg_1":
            champion = cup.get("champion")
            return _ref_by_name(refs_by_name, champion) if champion in pair_teams else None
        next_leg1 = _WINNERS_NEXT_LEG1.get(leg1_key)
        next_pairs = knockout_pairs.get(next_leg1) or [] if next_leg1 else []
        survivors = {name for pairs_entry in next_pairs for name in (pairs_entry.get("home"), pairs_entry.get("away")) if name}
        advancing_names = pair_teams & survivors
        if len(advancing_names) == 1:
            return _ref_by_name(refs_by_name, next(iter(advancing_names)))
        return None
    if competition == base.COMPETITION_CHALLENGE_CUP:
        winners = event_winners.get(event_key) or []
        if ordinal < len(winners):
            return _ref_by_name(refs_by_name, winners[ordinal])
        return None
    if competition == base.COMPETITION_SUPER_CUP:
        if event_key == "super_cup_semifinal":
            finalists = cup.get("finalists") or []
            if ordinal < len(finalists):
                return _ref_by_name(refs_by_name, finalists[ordinal])
            return None
        return _ref_by_name(refs_by_name, cup.get("champion"))
    return None


# -- 冠军判定 -----------------------------------------------------------


def _league_complete(ordered: List[TableRow]) -> bool:
    return bool(ordered) and all(row.played >= 38 for row in ordered)


def season_champion(
    conn: sqlite3.Connection,
    season_id: int,
    season_number: int,
    competition: str,
    archive: Optional[dict] = None,
    runtime: Optional[dict] = None,
    ordered_by_category: Optional[Dict[str, List[TableRow]]] = None,
) -> Optional[str]:
    """赛事冠军（附加赛为升级成功方）的队名；未决出为 None。"""
    if archive is None:
        archive = load_archive(conn, season_number)
    if runtime is None:
        runtime = load_runtime(conn, season_id)

    if competition in (DIVISION_PREMIER, DIVISION_SECOND):
        category = "premier" if competition == DIVISION_PREMIER else "second"
        if archive:
            order_key = "premier_order" if category == "premier" else "second_order"
            order = archive.get(order_key) or []
            if order:
                return order[0]
        if ordered_by_category is not None and category in ordered_by_category:
            ordered = ordered_by_category[category]
        else:
            ordered = compute_division_standings(conn, season_id, category, _resolutions_for(conn, season_number, category))
        if _league_complete(ordered):
            return ordered[0].team.name
        return None

    if competition in _CUP_STATE_KEY:
        if archive:
            champion = (archive.get("cup_champions") or {}).get(competition)
            if champion:
                return champion
        cup_state = runtime.get("cup_state") or {}
        return cup_state.get(_CUP_STATE_KEY[competition], {}).get("champion")

    if competition == base.COMPETITION_PLAYOFF:
        if archive:
            winner = (archive.get("last_transition") or {}).get("playoff_winner")
            if winner:
                return winner
        return _playoff_winner_from_runtime(conn, season_id, runtime)
    return None


def _playoff_winner_from_runtime(conn: sqlite3.Connection, season_id: int, runtime: dict) -> Optional[str]:
    """归档缺失时按引擎两回合规则（总比分 → 客场进球 → 高种子）确定性推算。"""
    final = (runtime.get("promotion_playoff") or {}).get("final") or {}
    higher_seed = final.get("higher_seed")
    lower_seed = final.get("lower_seed")
    if not higher_seed or not lower_seed:
        return None
    names_by_id = {int(row["team_id"]): row["name"] for row in conn.execute("SELECT team_id, name FROM teams")}
    legs = []
    for row in conn.execute(
        """
        SELECT home_team_id, away_team_id, home_goals, away_goals
        FROM matches
        WHERE season_id = ? AND category = 'playoff' AND status = 'completed'
        ORDER BY round_number, ordinal
        """,
        (season_id,),
    ):
        teams = {names_by_id.get(int(row["home_team_id"])), names_by_id.get(int(row["away_team_id"]))}
        if teams == {higher_seed, lower_seed}:
            legs.append((names_by_id[int(row["home_team_id"])], int(row["home_goals"]), names_by_id[int(row["away_team_id"])], int(row["away_goals"])))
    if len(legs) < 2:
        return None
    aggregate = {higher_seed: 0, lower_seed: 0}
    away_goals = {higher_seed: 0, lower_seed: 0}
    for home_name, home_score, away_name, away_score in legs:
        aggregate[home_name] += home_score
        aggregate[away_name] += away_score
        away_goals[away_name] += away_score
    if aggregate[higher_seed] != aggregate[lower_seed]:
        return higher_seed if aggregate[higher_seed] > aggregate[lower_seed] else lower_seed
    if away_goals[higher_seed] != away_goals[lower_seed]:
        return higher_seed if away_goals[higher_seed] > away_goals[lower_seed] else lower_seed
    return higher_seed


def _league_status(ordered: List[TableRow], completed: int, total: int, champion: Optional[str]) -> str:
    if champion is not None and _league_complete(ordered):
        return STATUS_FINISHED
    if completed == 0:
        return STATUS_NOT_STARTED
    if completed >= total:
        return STATUS_FINISHED
    return STATUS_IN_PROGRESS


# -- 公开查询 -----------------------------------------------------------


def list_competitions(conn: sqlite3.Connection, season_number: int) -> List[CompetitionOverview]:
    """六个规范赛事各一行（含冠军与完成进度）。"""
    season_id = base.season_id_for(conn, season_number)
    archive = load_archive(conn, season_number)
    runtime = load_runtime(conn, season_id)
    cup_state = runtime.get("cup_state") or {}

    ordered_by_category: Dict[str, List[TableRow]] = {}
    counts: Dict[str, Tuple[int, Optional[int]]] = {}
    for category, competition in (("premier", DIVISION_PREMIER), ("second", DIVISION_SECOND)):
        rows = conn.execute(
            """
            SELECT SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                   COUNT(*) AS total
            FROM matches WHERE season_id = ? AND category = ?
            """,
            (season_id, category),
        ).fetchone()
        counts[competition] = (int(rows["completed"] or 0), int(rows["total"] or 0))
        ordered_by_category[category] = compute_division_standings(
            conn, season_id, category, _resolutions_for(conn, season_number, category)
        )

    overviews: List[CompetitionOverview] = []
    for competition in base.ALL_COMPETITIONS:
        ref = base.CompetitionRef(competition_id=competition, display_name=competition)
        if competition in (DIVISION_PREMIER, DIVISION_SECOND):
            completed, total = counts[competition]
            champion = season_champion(
                conn, season_id, season_number, competition, archive, runtime, ordered_by_category
            )
            status = _league_status(
                ordered_by_category["premier" if competition == DIVISION_PREMIER else "second"],
                completed,
                total,
                champion,
            )
            overviews.append(
                CompetitionOverview(
                    competition=ref,
                    season_number=season_number,
                    status=status,
                    completed_matches=completed,
                    total_matches=total,
                    champion=champion,
                )
            )
            continue

        if competition == base.COMPETITION_PLAYOFF:
            row = conn.execute(
                "SELECT SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed FROM matches WHERE season_id = ? AND category = 'playoff'",
                (season_id,),
            ).fetchone()
            completed = int(row["completed"] or 0)
            champion = season_champion(conn, season_id, season_number, competition, archive, runtime)
            if champion is not None:
                status = STATUS_FINISHED
            elif completed == 0:
                status = STATUS_NOT_STARTED
            else:
                status = STATUS_IN_PROGRESS
            overviews.append(
                CompetitionOverview(
                    competition=ref,
                    season_number=season_number,
                    status=status,
                    completed_matches=completed,
                    total_matches=None,
                    champion=champion,
                )
            )
            continue

        # 杯赛
        cup = cup_state.get(_CUP_STATE_KEY[competition], {})
        if not cup.get("active"):
            overviews.append(
                CompetitionOverview(
                    competition=ref,
                    season_number=season_number,
                    status=STATUS_NOT_HELD,
                    completed_matches=0,
                    total_matches=None,
                    champion=None,
                )
            )
            continue
        row = conn.execute(
            "SELECT SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed FROM matches WHERE season_id = ? AND category = 'cup' AND competition = ?",
            (season_id, competition),
        ).fetchone()
        completed = int(row["completed"] or 0)
        champion = season_champion(conn, season_id, season_number, competition, archive, runtime)
        if champion is not None:
            status = STATUS_FINISHED
        elif completed == 0:
            status = STATUS_NOT_STARTED
        else:
            status = STATUS_IN_PROGRESS
        overviews.append(
            CompetitionOverview(
                competition=ref,
                season_number=season_number,
                status=status,
                completed_matches=completed,
                total_matches=None,
                champion=champion,
            )
        )
    return overviews


def get_competition_profile(
    conn: sqlite3.Connection,
    competition_id: str,
    season_number: int,
    *,
    leaderboards_is_real: Optional[bool] = None,
) -> CompetitionProfile:
    """赛事详情：积分榜 / 杯赛签表 / 全部比赛 / 榜单 / 奖项 / 冠军。"""
    if competition_id not in base.ALL_COMPETITIONS:
        raise KeyError(f"未知赛事：{competition_id}")
    season_id = base.season_id_for(conn, season_number)
    archive = load_archive(conn, season_number)
    runtime = load_runtime(conn, season_id)
    ref = base.CompetitionRef(competition_id=competition_id, display_name=competition_id)
    champion = season_champion(conn, season_id, season_number, competition_id, archive, runtime)

    if competition_id in (DIVISION_PREMIER, DIVISION_SECOND):
        category = "premier" if competition_id == DIVISION_PREMIER else "second"
        standings_rows = league_standings_rows(conn, season_id, season_number, category)
        matches = _match_refs(conn, season_id, season_number, "AND competition = ?", (competition_id,))
        return CompetitionProfile(
            competition=ref,
            season_number=season_number,
            standings=standings_rows,
            stage_rows=(),
            matches=tuple(matches),
            leaderboards=_leaderboards(conn, season_id, competition_id, is_real=leaderboards_is_real),
            awards=_competition_awards(conn, season_id, competition_id),
            champion=champion,
        )

    if competition_id == base.COMPETITION_PLAYOFF:
        matches = _match_refs(conn, season_id, season_number, "AND category = 'playoff'", ())
        return CompetitionProfile(
            competition=ref,
            season_number=season_number,
            standings=None,
            stage_rows=(),
            matches=tuple(matches),
            leaderboards=_leaderboards(conn, season_id, competition_id, is_real=leaderboards_is_real),
            awards=(),
            champion=champion,
        )

    # 杯赛
    refs_by_name = {team_ref.display_name: team_ref for team_ref in base.load_team_refs(conn)}
    stage_rows = _cup_stage_rows(
        conn, season_id, season_number, competition_id, runtime.get("cup_state") or {}, refs_by_name
    )
    matches = _match_refs(conn, season_id, season_number, "AND category = 'cup' AND competition = ?", (competition_id,))
    return CompetitionProfile(
        competition=ref,
        season_number=season_number,
        standings=None,
        stage_rows=stage_rows,
        matches=tuple(matches),
        leaderboards=_leaderboards(conn, season_id, competition_id, is_real=leaderboards_is_real),
        awards=_competition_awards(conn, season_id, competition_id),
        champion=champion,
    )
