"""职业统计、历史榜单与荣誉查询。

事实只来自 persistence_v2 的 matches/player_match_stats；冠军名单只来自
season.championships（以及 archives 中同一记录），不按当前阵容反推。
所有聚合按稳定 player_id，不按姓名/当前球队合并。
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from football_simulator.game import rules
from football_simulator.persistence_v2 import store

METRICS = {"appearances": "出场", "goals": "进球", "assists": "助攻"}
TROPHY_IDS = (rules.PREMIER, rules.SECOND, *rules.CUP_IDS)


def _connect(save_name: str) -> sqlite3.Connection:
    return store.open_readonly(save_name)


def _world_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM world ORDER BY version DESC LIMIT 1").fetchone()
    return int(row["version"]) if row else 0


def _cache_key(save_name: str, conn: sqlite3.Connection, args=()) -> tuple:
    path = str(store.database_path(save_name).resolve())
    return (path, _world_version(conn), tuple(args))


_STATS_CACHE: Dict[tuple, list] = {}


def _facts(save_name: str, conn: Optional[sqlite3.Connection] = None) -> List[dict]:
    """一次 SQL 取回当前存档全部已出场事实（按比赛/球员唯一）。"""
    own = conn is None
    conn = conn or _connect(save_name)
    try:
        key = _cache_key(save_name, conn, ("facts",))
        cached = _STATS_CACHE.get(key)
        if cached is not None:
            return cached
        rows = conn.execute(
            """
            SELECT pms.player_id, pms.season_number, m.competition, pms.team_id,
                   pms.appeared, pms.goals, pms.assists, p.name, p.position,
                   p.is_real, m.match_id, m.week
            FROM player_match_stats pms
            JOIN matches m ON m.match_id = pms.match_id
            JOIN players p ON p.player_id = pms.player_id
            WHERE m.status = 'completed' AND pms.appeared = 1
            """
        ).fetchall()
        result = [dict(row) for row in rows]
        _STATS_CACHE.clear()
        _STATS_CACHE[key] = result
        return result
    finally:
        if own:
            conn.close()


def invalidate(save_name: Optional[str] = None) -> None:
    if save_name is None:
        _STATS_CACHE.clear()
        return
    prefix = str(store.database_path(save_name).resolve())
    for key in list(_STATS_CACHE):
        if key[0] == prefix:
            _STATS_CACHE.pop(key, None)


def career_totals(save_name: str, player_id: str) -> dict:
    conn = _connect(save_name)
    try:
        facts = _facts(save_name, conn)
    finally:
        conn.close()
    rows = [row for row in facts if row["player_id"] == player_id]
    return {
        "player_id": player_id,
        "appearances": sum(int(row["appeared"]) for row in rows),
        "goals": sum(int(row["goals"]) for row in rows),
        "assists": sum(int(row["assists"]) for row in rows),
        "competitions": len({row["competition"] for row in rows}),
    }


def career_splits(save_name: str, player_id: str) -> dict:
    conn = _connect(save_name)
    try:
        facts = [row for row in _facts(save_name, conn) if row["player_id"] == player_id]
    finally:
        conn.close()
    by_competition: Dict[str, dict] = {}
    by_season: Dict[int, dict] = {}
    by_season_competition: Dict[Tuple[int, str], dict] = {}
    by_team: Dict[str, int] = {}
    for row in facts:
        for target, key in (
            (by_competition, row["competition"]),
            (by_season, int(row["season_number"])),
            (by_season_competition, (int(row["season_number"]), row["competition"])),
        ):
            item = target.setdefault(key, {"appearances": 0, "goals": 0, "assists": 0})
            item["appearances"] += 1
            item["goals"] += int(row["goals"])
            item["assists"] += int(row["assists"])
        by_team[row["team_id"]] = by_team.get(row["team_id"], 0) + 1
    return {
        "competition": [
            {"competition": comp, "label": rules.competition_label(comp), **values}
            for comp, values in sorted(by_competition.items(), key=lambda kv: rules.ALL_COMPETITION_IDS.index(kv[0]) if kv[0] in rules.ALL_COMPETITION_IDS else 99)
        ],
        "season": [
            {"season": season, **values}
            for season, values in sorted(by_season.items())
        ],
        "season_competition": [
            {"season": season, "competition": comp, "label": rules.competition_label(comp), **values}
            for (season, comp), values in sorted(by_season_competition.items())
        ],
        "team": [{"team_id": team_id, "appearances": count} for team_id, count in sorted(by_team.items())],
    }


def _rank_rows(rows: List[dict], metric: str) -> List[dict]:
    rows = sorted(
        [row for row in rows if int(row.get(metric, 0)) > 0],
        key=lambda row: (-int(row.get(metric, 0)), row.get("player_id", "")),
    )
    previous = None
    rank = 1
    for index, row in enumerate(rows, start=1):
        value = int(row.get(metric, 0))
        if previous != value:
            rank = index
            previous = value
        row["rank"] = rank
    return rows


def leaderboards(
    save_name: str,
    *,
    competition_id: Optional[str] = None,
    scope: str = "all",
    metric: str = "goals",
    include_in_progress: bool = True,
    player_scope: str = "all",
    page: int = 1,
    page_size: int = 25,
    season_number: Optional[int] = None,
) -> dict:
    if metric not in METRICS:
        raise ValueError(f"未知指标：{metric}")
    conn = _connect(save_name)
    try:
        facts = _facts(save_name, conn)
    finally:
        conn.close()
    completed_seasons = set()
    if scope in ("edition", "season") and not include_in_progress:
        conn2 = _connect(save_name)
        try:
            completed_seasons = {int(row[0]) for row in conn2.execute("SELECT season_number FROM archives")}
            completed_editions = {str(row[0]) for row in conn2.execute("SELECT j.key FROM world w, json_each(w.data_json, '$.edition_archives') j")}
        finally:
            conn2.close()
        facts = [row for row in facts if int(row["season_number"]) in completed_seasons or f"{row['season_number']}:{row['competition']}" in completed_editions]
    if competition_id:
        facts = [row for row in facts if row["competition"] == competition_id]
    if scope == "edition":
        if season_number is None:
            raise ValueError("单届纪录需要 season_number")
        facts = [row for row in facts if int(row["season_number"]) == int(season_number)]
    elif scope == "season":
        if season_number is None:
            season_number = max((int(row["season_number"]) for row in facts), default=0)
        facts = [row for row in facts if int(row["season_number"]) == int(season_number)]
    if player_scope == "real":
        facts = [row for row in facts if int(row["is_real"])]
    grouped: Dict[object, dict] = {}
    for row in facts:
        if scope == "edition":
            key = (int(row["season_number"]), row["competition"], row["player_id"])
        elif scope == "season":
            key = (int(row["season_number"]), row["player_id"])
        else:
            key = row["player_id"]
        item = grouped.setdefault(
            key,
            {
                "player_id": row["player_id"],
                "player": row["name"] or row["player_id"],
                "is_real": bool(row["is_real"]),
                "team_id": row["team_id"],
                "appearances": 0,
                "goals": 0,
                "assists": 0,
                "season": int(row["season_number"]) if scope in ("edition", "season") else None,
                "competition": row["competition"] if scope == "edition" else None,
            },
        )
        item["appearances"] += 1
        item["goals"] += int(row["goals"])
        item["assists"] += int(row["assists"])
    ranked = _rank_rows(list(grouped.values()), metric)
    total = len(ranked)
    start = max(0, (max(1, int(page)) - 1) * int(page_size))
    page_rows = ranked[start : start + int(page_size)]
    return {
        "rows": page_rows,
        "metric": metric,
        "metric_label": METRICS[metric],
        "scope": scope,
        "competition_id": competition_id,
        "season_number": season_number,
        "include_in_progress": bool(include_in_progress),
        "player_scope": player_scope,
        "page": max(1, int(page)),
        "page_size": int(page_size),
        "total": total,
        "pages": max(1, (total + int(page_size) - 1) // int(page_size)),
        "empty": total == 0,
        "cutoff_season": max((int(row["season_number"]) for row in facts), default=0),
    }


def historical_edition(save_name: str, competition_id: str, season_number: int) -> dict:
    return leaderboards(
        save_name,
        competition_id=competition_id,
        scope="edition",
        metric="goals",
        include_in_progress=False,
        page=1,
        page_size=500,
        season_number=season_number,
    )


def _all_championships(world: dict) -> List[dict]:
    rows: Dict[str, dict] = {}
    for row in (world["season"].get("championships") or {}).values():
        rows[str(row.get("title_id"))] = row
    for archive in world.get("archives", []):
        for row in (archive.get("championships") or {}).values():
            rows.setdefault(str(row.get("title_id")), row)
    return list(rows.values())


def competition_champions(world: dict, competition_id: str) -> List[dict]:
    """One champion per completed edition, including titles decided this season.

    Prefer frozen championship records. Older archives can supply the champion
    from their final table/order or cup result; live league standings never do.
    This read-only projection does not fabricate player championship medals.
    """
    if competition_id not in TROPHY_IDS:
        return []
    editions = {}

    def add(number, team_id, source):
        number = int(number or 0)
        if number <= 0 or not team_id or number in editions:
            return
        team = (world.get("teams") or {}).get(team_id) or {}
        name = team.get("club_en") or team_id
        editions[number] = {"season": number, "team_id": team_id,
                            "team": name, "club": name, "source": source}

    def title_records(container, number):
        for title in (container.get("championships") or {}).values():
            if title.get("competition_id") == competition_id:
                add(number, title.get("champion_team_id"), "championship")

    def final_result(container, number):
        if competition_id in (rules.PREMIER, rules.SECOND):
            table = container.get(f"{competition_id}_table") or (container.get("tables") or {}).get(competition_id) or []
            if table:
                add(number, table[0].get("team_id"), "final_table")
            else:
                order = container.get(f"{competition_id}_order") or []
                if order:
                    add(number, order[0], "final_order")
        else:
            champion = (container.get("cup_champions") or {}).get(competition_id)
            if container.get("competition_id") == competition_id:
                champion = champion or (container.get("state") or {}).get("champion")
            add(number, champion, "cup_result")

    # Collect authoritative records before applying legacy fallbacks.
    archives = world.get("archives") or []
    for archive in archives:
        title_records(archive, archive.get("season_number"))
    for entry in (world.get("edition_archives") or {}).values():
        if entry.get("competition_id") == competition_id:
            title = entry.get("championship") or {}
            add(entry.get("season_number"), title.get("champion_team_id"), "championship")
    title_records(world.get("season") or {}, world.get("season_number"))
    for archive in archives:
        final_result(archive, archive.get("season_number"))
    for entry in (world.get("edition_archives") or {}).values():
        if entry.get("competition_id") == competition_id:
            final_result(entry, entry.get("season_number"))
    return [editions[number] for number in sorted(editions, reverse=True)]


def cumulative_team_honors(world: dict) -> dict:
    """Read settled season totals once per season; never recalculate honors.

    Archive snapshots take precedence over the current season, which can still
    contain the same settled facts after archiving and before next-season start.
    Unsettled current-season values do not enter the lifetime board.
    """
    editions = {}
    for archive in world.get("archives") or []:
        season_number = int(archive.get("season_number", 0))
        if season_number > 0:
            editions.setdefault(season_number, archive.get("honor_totals") or {})
    current = world.get("season") or {}
    current_number = int(world.get("season_number", 0))
    if current.get("finalized") and current_number > 0:
        editions.setdefault(current_number, current.get("honor_totals") or {})
    trophy_counts = {}
    for competition in TROPHY_IDS:
        for title in competition_champions(world, competition):
            team_id = title["team_id"]
            trophy_counts[team_id] = trophy_counts.get(team_id, 0) + 1
    rows = []
    for team_id, team in (world.get("teams") or {}).items():
        rows.append({"team_id": team_id, "team": team.get("club_en") or team_id,
                     "club": team.get("club_en") or "",
                     "points": sum(int(totals.get(team_id, 0)) for totals in editions.values()),
                     "trophies": trophy_counts.get(team_id, 0)})
    rows.sort(key=lambda row: (-row["points"], row["team"].casefold(), row["team_id"]))
    previous, rank = None, None
    for index, row in enumerate(rows, 1):
        if row["points"] != previous:
            rank = index
        row["rank"] = rank if editions else None
        previous = row["points"]
    return {"rows": rows, "seasons": sorted(editions), "current_pending": not bool(current.get("finalized"))}


def team_honors(world: dict, team_id: str) -> dict:
    rows = [row for row in _all_championships(world) if row.get("champion_team_id") == team_id]
    by_competition = {comp: [] for comp in TROPHY_IDS}
    for row in rows:
        by_competition.setdefault(row["competition_id"], []).append(row)
    titles = []
    for comp in TROPHY_IDS:
        facts = sorted(by_competition.get(comp, []), key=lambda row: (int(row["season_number"]), int(row.get("decided_week", 0))))
        titles.append(
            {
                "competition_id": comp,
                "label": rules.competition_label(comp),
                "count": len(facts),
                "champion": bool(facts),
                "active": rules.cup_active(comp, int(world["season_number"])) if comp in rules.CUP_IDS else True,
                "seasons": [row["season_number"] for row in facts],
                "titles": facts,
                "points": sum(
                    int(row.get("points", 0))
                    for row in (world["season"].get("honor_rows") or [])
                    if row.get("competition") == comp and row.get("team_id") == team_id
                ),
            }
        )
    total_points = 0
    seen_seasons = set()
    for archive in world.get("archives", []):
        total_points += int((archive.get("honor_totals") or {}).get(team_id, 0))
        seen_seasons.add(int(archive.get("season_number", 0)))
    total_points += int((world["season"].get("honor_totals") or {}).get(team_id, 0))
    seasons = sorted({int(row["season_number"]) for row in rows})
    return {
        "team_id": team_id,
        "titles": titles,
        "total_titles": len(rows),
        "honor_points": total_points,
        "points_pending": bool(rows) and not bool(world["season"].get("finalized")),
        "first_season": seasons[0] if seasons else None,
        "latest_season": seasons[-1] if seasons else None,
        "championships": rows,
    }


def award_team_history(world: dict, teams: dict) -> str:
    """Human-readable match counts from the frozen edition, when multi-club."""
    if len(teams or {}) < 2:
        return ""
    labels = []
    for team_id, count in teams.items():
        name = (world.get("teams", {}).get(team_id) or {}).get("club_en", team_id)
        labels.append(f"{name} {count} 场")
    return "本届效力：" + "；".join(labels)


def _award_rows(world: dict) -> List[dict]:
    rows: List[dict] = []
    season = world["season"]
    current = int(world["season_number"])
    sources = [(current, season.get("awards") or {})]
    sources.extend(
        (int(archive.get("season_number", 0)), archive.get("awards") or {})
        for archive in world.get("archives", [])
    )
    seen = set()
    for season_number, awards in sources:
        for comp, values in (awards.get("competitions") or {}).items():
            for award_type in ("top_scorer", "assist_leader", "mvp", "evp"):
                for winner in values.get(award_type) or []:
                    key = (season_number, comp, award_type, winner.get("player_id"))
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "season_number": season_number,
                            "competition": comp,
                            "competition_label": rules.competition_label(comp),
                            "award_type": award_type,
                            "award_label": {"top_scorer": "射手王", "assist_leader": "助攻王", "mvp": "MVP", "evp": "EVP"}[award_type],
                            "player_id": winner.get("player_id"),
                            "player": winner.get("label"),
                            "team_id": winner.get("team_id"),
                            "teams": dict(winner.get("teams") or {}),
                            "value": winner.get("value"),
                        }
                    )
    return rows


def award_leaderboard(world: dict, award_type: str) -> List[dict]:
    counts = {}
    for row in _award_rows(world):
        if row["award_type"] != award_type or not row.get("player_id"):
            continue
        pid = row["player_id"]
        if pid not in counts:
            counts[pid] = {"player_id": pid, "player": row["player"], "count": 0}
        counts[pid]["count"] += 1
    return sorted(counts.values(), key=lambda row: (-row["count"], row["player"], row["player_id"]))


def player_honors(world: dict, player_id: str) -> dict:
    awards = [row for row in _award_rows(world) if row.get("player_id") == player_id]
    titles = []
    for row in _all_championships(world):
        if player_id in (row.get("roster_player_ids") or []):
            titles.append(
                {
                    **row,
                    "competition_label": rules.competition_label(row["competition_id"]),
                    "team_name": (world.get("teams", {}).get(row["champion_team_id"]) or {}).get("club_en", row["champion_team_id"]),
                }
            )
    top20 = []
    seen_top = set()
    for archive in world.get("archives", []):
        for row in archive.get("top20") or []:
            if row.get("player_id") == player_id:
                key = (int(archive.get("season_number", 0)), row.get("player_id"))
                if key not in seen_top:
                    seen_top.add(key)
                    top20.append({**row, "season_number": archive.get("season_number")})
    current_top = (world["season"].get("awards") or {}).get("top20") or []
    for row in current_top:
        if row.get("player_id") == player_id:
            key = (int(world["season_number"]), row.get("player_id"))
            if key not in seen_top:
                seen_top.add(key)
                top20.append({**row, "season_number": int(world["season_number"])})
    return {"player_id": player_id, "awards": awards, "championships": titles, "top20": top20}


def annual_rank_rows(world: dict, player_id: str) -> List[dict]:
    """Read-only annual Top20 rows for one player, one row per season.

    Reuses ``player_honors`` so the archive-first identity dedupe is shared with
    the honors page.  Only the stored ``rank`` is exposed as a rank; the saved
    score/components remain untouched and are never used as a fallback.
    """
    rows = []
    seen = set()
    for row in player_honors(world, player_id)["top20"]:
        season = row.get("season_number")
        try:
            season = int(season)
        except (TypeError, ValueError):
            continue
        if season in seen:
            continue
        seen.add(season)
        raw_rank = row.get("rank")
        rank = None
        if isinstance(raw_rank, (int, float)) and not isinstance(raw_rank, bool):
            candidate = int(raw_rank)
            if candidate == raw_rank and 1 <= candidate <= 20:
                rank = candidate
        team_id = row.get("team_id")
        team = (world.get("teams") or {}).get(team_id) or {}
        club = team.get("club_en") or ""
        rows.append(
            {
                "season": season,
                "rank": rank,
                "player_id": row.get("player_id") or player_id,
                "team_id": team_id,
                "team": club or "—",
                "club": club,
            }
        )
    rows.sort(key=lambda item: item["season"])
    return rows
