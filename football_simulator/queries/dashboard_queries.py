"""首页（Dashboard）只读查询（阶段 2）。

口径：
- 当前赛季 = status='active' 的赛季（无 active 时取最新赛季，见
  ``base.resolve_current_season``）；meta（save_meta）提供周指针等标量。
- ``upcoming_matches`` / ``latest_results`` 只取当前赛季的联赛预生成赛程与
  已完成比赛，排序键固定，确定性截取前 8 场。
- ``league_leaders`` 基于 ``player_match_stats`` 聚合（与 competition_queries
  同口径：全部比赛、按比赛当时球队归属、appeared=1）。
- ``cup_champions_so_far`` 来自当前赛季 ``season_runtime.cup_state`` 中已决出
  的冠军。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from football_simulator.domain import formulas
from football_simulator.models import Player, PlayerSeasonStats
from football_simulator.queries import base

UPCOMING_LIMIT = 8
LATEST_LIMIT = 8
LEADER_LIMIT = 3

_PENDING_TYPES = ("ability_review", "transfer_review", "draft")
_CUP_STATE_KEY = {
    base.COMPETITION_WINNERS_CUP: "winners_cup",
    base.COMPETITION_CHALLENGE_CUP: "challenge_cup",
    base.COMPETITION_SUPER_CUP: "super_cup",
}
_LEAGUE_CATEGORIES = (
    (base.COMPETITION_PREMIER, "premier"),
    (base.COMPETITION_SECOND, "second"),
)


# -- DTO ----------------------------------------------------------------


@dataclass(frozen=True)
class PendingCounts:
    """当前待办计数（按 pending_actions.type）。"""

    ability_review: int
    transfer_review: int
    draft: int


@dataclass(frozen=True)
class DashboardLeaderboardEntry:
    """联赛射手/助攻榜行（前 3）。"""

    player: base.PlayerRef
    value: int  # 射手榜为进球数，助攻榜为助攻数
    rating: float


@dataclass(frozen=True)
class LeagueLeaders:
    competition: base.CompetitionRef
    top_scorers: Tuple[DashboardLeaderboardEntry, ...]
    assist_leaders: Tuple[DashboardLeaderboardEntry, ...]


@dataclass(frozen=True)
class CupChampionLine:
    """当前赛季已决出的杯赛冠军。"""

    competition: base.CompetitionRef
    champion: str


@dataclass(frozen=True)
class DashboardSnapshot:
    current_season: int
    current_week: int
    season_complete: bool
    pending_counts: PendingCounts
    upcoming_matches: Tuple[base.MatchRef, ...]  # scheduled，按周次升序前 8
    latest_results: Tuple[base.MatchRef, ...]  # completed，按周次降序前 8
    league_leaders: Tuple[LeagueLeaders, ...]
    cup_champions_so_far: Tuple[CupChampionLine, ...]


# -- 内部辅助 -----------------------------------------------------------


def _load_meta(conn: sqlite3.Connection) -> Dict[str, object]:
    return {
        row["key"]: json.loads(row["value_json"])
        for row in conn.execute("SELECT key, value_json FROM save_meta")
    }


def _current_season_id(conn: sqlite3.Connection, meta: Dict[str, object]) -> Tuple[int, int]:
    season_number = meta.get("season_number")
    if season_number is not None:
        return int(season_number), base.season_id_for(conn, int(season_number))
    season = base.resolve_current_season(conn)
    return season.season_number, base.season_id_for(conn, season.season_number)


def _match_refs(
    conn: sqlite3.Connection,
    season_id: int,
    season_number: int,
    order_sql: str,
    limit: int,
) -> Tuple[base.MatchRef, ...]:
    refs_by_id = base.team_ref_by_id(conn)
    rows = conn.execute(
        f"""
        SELECT match_id, competition, week_number, round_number, ordinal,
               home_team_id, away_team_id, status, home_goals, away_goals
        FROM matches
        WHERE season_id = ? {order_sql}
        LIMIT ?
        """,
        (season_id, limit),
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
    return tuple(refs)


def _player_identity(conn: sqlite3.Connection) -> Dict[str, Tuple[base.PlayerRef, int]]:
    """players 表中的 player_id -> (稳定 PlayerRef, ability)。"""
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
    row = conn.execute(_DEFAULT_ABILITY_SQL).fetchone()
    return int(row["ability"]) if row else 0


def _synthetic_default_ref(player_id: str, team_id: int) -> Optional[base.PlayerRef]:
    """比赛统计中已不在注册表的默认球员 ID -> 稳定身份（见 competition_queries 同名逻辑）。"""
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


def _league_leaders(
    conn: sqlite3.Connection,
    season_id: int,
    is_real: Optional[bool] = None,
) -> Tuple[LeagueLeaders, ...]:
    identity = _player_identity(conn)
    fallback_ability = _default_ability_fallback(conn)
    leaders: List[LeagueLeaders] = []
    for competition, category in _LEAGUE_CATEGORIES:
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
            WHERE m.season_id = ? AND m.category = ? AND pms.appeared = 1
            GROUP BY pms.player_id
            """,
            (season_id, category),
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

        if is_real is not None:
            # “只显示真实球员”必须在截取前 N 名之前过滤，否则排名失真。
            accumulated = {
                player_id: bucket
                for player_id, bucket in accumulated.items()
                if bucket["ref"].is_real == is_real
            }

        scorers: List[DashboardLeaderboardEntry] = []
        assisters: List[DashboardLeaderboardEntry] = []
        entry_ability: Dict[str, int] = {}
        for bucket in accumulated.values():
            ref = bucket["ref"]
            stats = PlayerSeasonStats(
                player=Player(
                    player_id=ref.player_id,
                    name=ref.display_name,
                    position=ref.position,
                    ability=int(bucket["ability"]),
                    is_real=ref.is_real,
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
            rating = formulas.calculate_player_rating(stats, matches_played)
            scorers.append(DashboardLeaderboardEntry(player=ref, value=stats.goals, rating=rating))
            assisters.append(DashboardLeaderboardEntry(player=ref, value=stats.assists, rating=rating))
            entry_ability[ref.player_id] = int(bucket["ability"])
        # 并列时确定性排序：统计降序 → 评分降序 → 能力降序 → 名称升序，
        # 与 competition_queries 的赛事榜单完全一致（跨域一致性测试锁定）。
        scorers.sort(
            key=lambda entry: (
                -entry.value,
                -entry.rating,
                -entry_ability.get(entry.player.player_id, 0),
                entry.player.display_name,
            )
        )
        assisters.sort(
            key=lambda entry: (
                -entry.value,
                -entry.rating,
                -entry_ability.get(entry.player.player_id, 0),
                entry.player.display_name,
            )
        )
        leaders.append(
            LeagueLeaders(
                competition=base.CompetitionRef(competition_id=competition, display_name=competition),
                top_scorers=tuple(scorers[:LEADER_LIMIT]),
                assist_leaders=tuple(assisters[:LEADER_LIMIT]),
            )
        )
    return tuple(leaders)


# -- 公开查询 -----------------------------------------------------------


def get_dashboard(
    conn: sqlite3.Connection,
    *,
    leaderboards_is_real: Optional[bool] = None,
) -> DashboardSnapshot:
    """首页快照：赛季指针、待办计数、赛程速览、榜单与已决出杯赛冠军。"""
    meta = _load_meta(conn)
    season_number, season_id = _current_season_id(conn, meta)
    season_complete = bool(meta.get("season_complete", False))
    current_week = int(meta.get("current_week") or 0)

    counts = {row["type"]: int(row["total"]) for row in conn.execute(
        "SELECT type, COUNT(*) AS total FROM pending_actions GROUP BY type"
    )}
    pending_counts = PendingCounts(
        ability_review=counts.get("ability_review", 0),
        transfer_review=counts.get("transfer_review", 0),
        draft=counts.get("draft", 0),
    )

    upcoming = _match_refs(
        conn,
        season_id,
        season_number,
        "AND status = 'scheduled' ORDER BY week_number, round_number, category, ordinal",
        UPCOMING_LIMIT,
    )
    latest = _match_refs(
        conn,
        season_id,
        season_number,
        "AND status = 'completed' ORDER BY week_number DESC, round_number DESC, category DESC, ordinal DESC",
        LATEST_LIMIT,
    )

    runtime_row = conn.execute(
        "SELECT data_json FROM season_runtime WHERE season_id = ?",
        (season_id,),
    ).fetchone()
    runtime = json.loads(runtime_row["data_json"]) if runtime_row else {}
    cup_state = runtime.get("cup_state") or {}
    cup_champions: List[CupChampionLine] = []
    for competition, state_key in _CUP_STATE_KEY.items():
        champion = (cup_state.get(state_key) or {}).get("champion")
        if champion:
            cup_champions.append(
                CupChampionLine(
                    competition=base.CompetitionRef(competition_id=competition, display_name=competition),
                    champion=champion,
                )
            )

    return DashboardSnapshot(
        current_season=season_number,
        current_week=current_week,
        season_complete=season_complete,
        pending_counts=pending_counts,
        upcoming_matches=upcoming,
        latest_results=latest,
        league_leaders=_league_leaders(conn, season_id, is_real=leaderboards_is_real),
        cup_champions_so_far=tuple(cup_champions),
    )
