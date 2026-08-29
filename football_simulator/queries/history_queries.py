"""历史维度的只读查询（阶段 2）。

数据来源是 ``season_archives.archive_json``（赛季归档字典，阶段 0 已锁定形状）。
归档中的历史键（``real::<显示名>``、legacy player_id）统一经
``base.canonical_player_id_for_name`` 收敛为稳定 ID；归档缺失时相应字段返回
空集合 / None，仅 ``get_season_archive_detail`` 按契约抛 KeyError。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from football_simulator.queries import base

_CUP_STATE_KEY = {
    base.COMPETITION_WINNERS_CUP: "winners_cup",
    base.COMPETITION_CHALLENGE_CUP: "challenge_cup",
    base.COMPETITION_SUPER_CUP: "super_cup",
}
_DIVISION_ORDER = {base.COMPETITION_PREMIER: 0, base.COMPETITION_SECOND: 1}
_AWARD_TYPE_ORDER = {"top_scorer": 0, "assist_leader": 1, "mvp": 2}


# -- DTO ----------------------------------------------------------------


@dataclass(frozen=True)
class SeasonSummary:
    """赛季总览行（冠军信息来自归档；无归档时冠军字段为 None）。"""

    season_number: int
    status: str  # seasons.status：active / completed
    premier_champion: Optional[str]
    winners_cup_champion: Optional[str]
    challenge_cup_champion: Optional[str]
    super_cup_champion: Optional[str]
    top20_top3: Tuple[str, ...]  # Top20 前三名姓名（不足三名时按实际数量）


@dataclass(frozen=True)
class SeasonTeamOrder:
    """赛季末球队名次行。"""

    rank: int
    team: base.TeamRef


@dataclass(frozen=True)
class CupChampions:
    winners_cup: Optional[str]
    challenge_cup: Optional[str]
    super_cup: Optional[str]


@dataclass(frozen=True)
class Top20Line:
    rank: int
    score: float
    player: base.PlayerRef
    label: str
    team_name: str


@dataclass(frozen=True)
class CompetitionAwardLine:
    """归档中的赛事个人奖项（top_scorer / assist_leader / mvp）。"""

    competition: str
    award_type: str
    player: base.PlayerRef
    team_name: Optional[str]
    score: Optional[float]


@dataclass(frozen=True)
class TeamHonorLine:
    """球队荣誉表行（与球员个人奖项分开展示）。"""

    team_name: str
    division: str
    league_result: str
    winners_cup_result: str
    challenge_cup_result: str
    super_cup_result: str
    honor_points: int
    total_titles: int


@dataclass(frozen=True)
class SettlementPointLine:
    """球员身价/评分结算点（保留原语义，player_id 转稳定 ID）。"""

    player: base.PlayerRef
    stage: str
    week_number: int
    season_rating: Optional[float]
    market_value: Optional[float]
    team_name: str


@dataclass(frozen=True)
class SeasonArchiveDetail:
    season_number: int
    status: str
    premier_order: Tuple[SeasonTeamOrder, ...]
    second_order: Tuple[SeasonTeamOrder, ...]
    cup_champions: CupChampions
    top20: Tuple[Top20Line, ...]
    competition_awards: Tuple[CompetitionAwardLine, ...]
    team_honor_table: Tuple[TeamHonorLine, ...]
    player_settlement_points: Tuple[SettlementPointLine, ...]


@dataclass(frozen=True)
class CompetitionSeasonLine:
    """跨赛季赛事历史行（仅包含已有归档的赛季）。"""

    season_number: int
    champion: Optional[str]  # 冠军队名（升级附加赛为升级成功方）
    champion_player: Optional[base.PlayerRef]  # 该赛事 MVP（冠军球员）；无则 None


# -- 内部辅助 -----------------------------------------------------------


def _load_archive_row(conn: sqlite3.Connection, season_number: int) -> Tuple[Optional[dict], Optional[str]]:
    row = conn.execute(
        """
        SELECT sa.archive_json, s.status FROM season_archives AS sa
        JOIN seasons AS s ON s.season_id = sa.season_id
        WHERE s.season_number = ?
        """,
        (int(season_number),),
    ).fetchone()
    if row is None:
        return None, None
    return json.loads(row["archive_json"]), row["status"]


def _team_ref_by_name(conn: sqlite3.Connection) -> Dict[str, base.TeamRef]:
    return {ref.display_name: ref for ref in base.load_team_refs(conn)}


def _canonical_ref(label: str, position: str) -> base.PlayerRef:
    return base.PlayerRef(
        player_id=base.canonical_player_id_for_name(label),
        display_name=label,
        position=position or "",
        is_real=True,
    )


def _ordered_team_refs(
    conn: sqlite3.Connection,
    names: List[str],
) -> Tuple[SeasonTeamOrder, ...]:
    refs_by_name = _team_ref_by_name(conn)
    rows: List[SeasonTeamOrder] = []
    for index, name in enumerate(names, start=1):
        ref = refs_by_name.get(name)
        if ref is None:
            raise KeyError(f"归档中的球队不在注册表中：{name}")
        rows.append(SeasonTeamOrder(rank=index, team=ref))
    return tuple(rows)


# -- 公开查询 -----------------------------------------------------------


def list_season_summaries(conn: sqlite3.Connection) -> List[SeasonSummary]:
    """每个赛季一行（含无归档的进行中赛季；冠军字段为 None）。"""
    rows: List[SeasonSummary] = []
    for row in conn.execute(
        """
        SELECT s.season_number, s.status, sa.archive_json
        FROM seasons AS s
        LEFT JOIN season_archives AS sa ON sa.season_id = s.season_id
        ORDER BY s.season_number
        """
    ):
        archive = json.loads(row["archive_json"]) if row["archive_json"] else None
        season_number = int(row["season_number"])
        premier_champion = None
        cup_champions: Dict[str, Optional[str]] = {}
        top3: Tuple[str, ...] = ()
        if archive:
            premier_order = archive.get("premier_order") or []
            if premier_order:
                premier_champion = premier_order[0]
            raw_champions = archive.get("cup_champions") or {}
            cup_champions = {key: raw_champions.get(competition) for competition, key in _CUP_STATE_KEY.items()}
            top20 = (archive.get("season_awards") or {}).get("top20") or []
            top3 = tuple(item.get("label") or "" for item in top20[:3])
        rows.append(
            SeasonSummary(
                season_number=season_number,
                status=row["status"],
                premier_champion=premier_champion,
                winners_cup_champion=cup_champions.get("winners_cup"),
                challenge_cup_champion=cup_champions.get("challenge_cup"),
                super_cup_champion=cup_champions.get("super_cup"),
                top20_top3=top3,
            )
        )
    return rows


def get_season_archive_detail(conn: sqlite3.Connection, season_number: int) -> SeasonArchiveDetail:
    """某赛季归档详情；该赛季无归档时抛 KeyError。"""
    archive, status = _load_archive_row(conn, season_number)
    if archive is None:
        raise KeyError(f"第 {season_number} 赛季还没有赛季归档。")

    cup_raw = archive.get("cup_champions") or {}
    cup_champions = CupChampions(
        winners_cup=cup_raw.get(base.COMPETITION_WINNERS_CUP),
        challenge_cup=cup_raw.get(base.COMPETITION_CHALLENGE_CUP),
        super_cup=cup_raw.get(base.COMPETITION_SUPER_CUP),
    )

    top20 = tuple(
        Top20Line(
            rank=int(item["rank"]),
            score=float(item["score"]),
            player=_canonical_ref(item.get("label") or "", item.get("position") or ""),
            label=item.get("label") or "",
            team_name=item.get("team_name") or "",
        )
        for item in (archive.get("season_awards") or {}).get("top20") or []
    )

    competition_awards: List[CompetitionAwardLine] = []
    for competition, values in ((archive.get("season_awards") or {}).get("competitions") or {}).items():
        for award_type in ("top_scorer", "assist_leader", "mvp"):
            item = values.get(award_type)
            if not item:
                continue
            competition_awards.append(
                CompetitionAwardLine(
                    competition=competition,
                    award_type=award_type,
                    player=_canonical_ref(item.get("label") or "", item.get("position") or ""),
                    team_name=item.get("team_name"),
                    score=float(item["score"]) if item.get("score") is not None else None,
                )
            )
    competition_awards.sort(
        key=lambda line: (_AWARD_TYPE_ORDER.get(line.award_type, 9), line.competition)
    )

    honor_rows = list(archive.get("team_stats") or [])
    honor_rows.sort(
        key=lambda row: (
            _DIVISION_ORDER.get(row.get("division") or "", 9),
            row.get("league_rank") if row.get("league_rank") is not None else 10_000,
            row.get("team_name") or "",
        )
    )
    team_honor_table = tuple(
        TeamHonorLine(
            team_name=row.get("team_name") or "",
            division=row.get("division") or "",
            league_result=row.get("league_result") or "",
            winners_cup_result=row.get("winners_cup_result") or "",
            challenge_cup_result=row.get("challenge_cup_result") or "",
            super_cup_result=row.get("super_cup_result") or "",
            honor_points=int(row.get("honor_points") or 0),
            total_titles=int(row.get("total_titles") or 0),
        )
        for row in honor_rows
    )

    settlement_points = tuple(
        SettlementPointLine(
            player=_canonical_ref(point.get("label") or "", point.get("position") or ""),
            stage=point.get("stage") or "",
            week_number=int(point.get("week_number") or 0),
            season_rating=float(point["season_rating"]) if point.get("season_rating") is not None else None,
            market_value=float(point["market_value"]) if point.get("market_value") is not None else None,
            team_name=point.get("team_display_name") or point.get("team_name") or "",
        )
        for point in archive.get("player_settlement_points") or []
    )

    return SeasonArchiveDetail(
        season_number=int(archive.get("season_number") or season_number),
        status=status or "completed",
        premier_order=_ordered_team_refs(conn, archive.get("premier_order") or []),
        second_order=_ordered_team_refs(conn, archive.get("second_order") or []),
        cup_champions=cup_champions,
        top20=top20,
        competition_awards=tuple(competition_awards),
        team_honor_table=team_honor_table,
        player_settlement_points=settlement_points,
    )


def get_competition_history(conn: sqlite3.Connection, competition_id: str) -> List[CompetitionSeasonLine]:
    """跨赛季赛事历史（冠军 / 冠军球员）；未知赛事抛 KeyError。"""
    if competition_id not in base.ALL_COMPETITIONS:
        raise KeyError(f"未知赛事：{competition_id}")
    rows: List[CompetitionSeasonLine] = []
    for row in conn.execute(
        "SELECT archive_json FROM season_archives AS sa JOIN seasons AS s ON s.season_id = sa.season_id ORDER BY s.season_number"
    ):
        archive = json.loads(row["archive_json"])
        champion = _archive_champion(archive, competition_id)
        champion_player = _archive_champion_player(archive, competition_id)
        rows.append(
            CompetitionSeasonLine(
                season_number=int(archive.get("season_number") or 0),
                champion=champion,
                champion_player=champion_player,
            )
        )
    return rows


def _archive_champion(archive: dict, competition_id: str) -> Optional[str]:
    if competition_id == base.COMPETITION_PREMIER:
        order = archive.get("premier_order") or []
        return order[0] if order else None
    if competition_id == base.COMPETITION_SECOND:
        order = archive.get("second_order") or []
        return order[0] if order else None
    if competition_id == base.COMPETITION_PLAYOFF:
        return (archive.get("last_transition") or {}).get("playoff_winner")
    return (archive.get("cup_champions") or {}).get(competition_id)


def _archive_champion_player(archive: dict, competition_id: str) -> Optional[base.PlayerRef]:
    awards = (archive.get("season_awards") or {}).get("competitions") or {}
    item = (awards.get(competition_id) or {}).get("mvp")
    if not item:
        return None
    return _canonical_ref(item.get("label") or "", item.get("position") or "")
