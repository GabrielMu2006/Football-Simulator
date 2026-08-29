"""SQLite 存档仓库（阶段 1）。

职责：把 state.py 的“内存工作字典”（与旧 state.json 完全同形）与规范化
SQLite 表互转。冻结的玩法代码继续消费该字典；SQLite 成为唯一事实源：

- 每个公开操作在一个 ``BEGIN IMMEDIATE`` 事务内完成“物化 → 模拟 → 持久化”；
- 赛季创建时即为两级联赛全部 760 场比赛生成稳定 ``match_id``（未赛比赛
  status='scheduled'），杯赛/附加赛比赛在产生时获得同规则 ID；
- ``player_match_stats`` 按比赛当时两队注册阵容写入（appeared=1，六项可为 0），
  ``team_id`` 为比赛当时所属球队 —— 供查询使用，不改变任何聚合公式；
- ``season_archives`` 保存赛季归档 JSON（与旧 history 元素同形）并派生规范化
  ``awards`` 行；历史赛季数据不再随每周写盘被整体重写。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from football_simulator.persistence import connection as db_connection
from football_simulator.persistence.schema import apply_schema
from football_simulator.schedule import build_league_schedule, build_week_calendar

CATEGORY_BY_MATCHDAY_KEY = {
    "premier_matchdays": "premier",
    "second_matchdays": "second",
    "cup_matchdays": "cup",
    "playoff_matchdays": "playoff",
}
STAT_COLUMNS = (
    "goals",
    "assists",
    "chances_created",
    "successful_defenses",
    "successful_saves",
    "clean_sheets",
)
EXTRA_KEYS = (
    "last_transition",
    "last_ability_review",
    "last_transfer_review",
    "last_draft",
    "roster_fix_summary",
)
DIVISIONS = ("一级联赛", "次级联赛")
LEAGUE_CATEGORY_BY_DIVISION = {"一级联赛": "premier", "次级联赛": "second"}


def match_id_for(season_number: int, category: str, week_number: int, round_number: int, ordinal: int) -> str:
    return f"m-{season_number}-{category}-w{week_number}-r{round_number}-o{ordinal}"


class SaveRepository:
    """单个存档数据库的读写仓库。事务由调用方显式控制。"""

    def __init__(self, save_name: str, conn: sqlite3.Connection) -> None:
        self.save_name = save_name
        self._conn = conn

    # -- 打开/创建 -------------------------------------------------------

    @classmethod
    def open(cls, save_dir: Path, save_name: str) -> "SaveRepository":
        conn = db_connection.connect(save_dir)
        return cls(save_name, conn)

    @classmethod
    def create(cls, save_dir: Path, save_name: str) -> "SaveRepository":
        save_dir.mkdir(parents=True, exist_ok=True)
        conn = db_connection.connect(save_dir, create=True)
        apply_schema(conn)
        return cls(save_name, conn)

    def close(self) -> None:
        self._conn.close()

    # -- 事务 ------------------------------------------------------------

    def begin(self) -> None:
        db_connection.begin_immediate(self._conn)

    def commit(self) -> None:
        db_connection.commit(self._conn)

    def rollback(self) -> None:
        db_connection.rollback(self._conn)

    # -- 物化：表 → 工作字典 ---------------------------------------------

    def load_state(self) -> Optional[dict]:
        """物化工作字典。空库（尚未初始化）返回 None。"""
        if self._conn.execute("SELECT COUNT(*) FROM save_meta").fetchone()[0] == 0:
            return None
        meta = self._load_meta()
        teams = self._load_teams()
        season_id = self._require_season_id(int(meta["season_number"]))
        runtime_data = self._load_season_runtime(season_id)
        extras = runtime_data.get("extras", {})

        state: dict = {
            "save_name": meta["save_name"],
            "season_number": int(meta["season_number"]),
            "current_week": int(meta["current_week"]),
            "season_complete": bool(meta["season_complete"]),
            "premier_team_names": [team["name"] for team in teams if team["division"] == "一级联赛"],
            "second_team_names": [team["name"] for team in teams if team["division"] == "次级联赛"],
            "next_premier_team_names": meta["next_premier_team_names"],
            "next_second_team_names": meta["next_second_team_names"],
            "premier_teams": [team["data"] for team in teams if team["division"] == "一级联赛"],
            "second_teams": [team["data"] for team in teams if team["division"] == "次级联赛"],
            "weeks": meta["weeks"],
            "simulated_weeks": self._load_simulated_weeks(season_id, teams, meta),
            "promotion_playoff": runtime_data.get("promotion_playoff", {}),
            "ranking_playoffs": runtime_data.get("ranking_playoffs", {}),
            "cup_state": runtime_data.get("cup_state", {}),
            "history": self._load_history(),
            "real_player_pool": self._load_pool(),
            "pending_ability_review": self._load_pending("ability_review"),
            "pending_transfer_review": self._load_pending("transfer_review"),
            "pending_draft": self._load_pending("draft", single=True),
            "transfer_history": self._load_transfers(),
            "draft_pool_index": int(meta["draft_pool_index"]),
            "settlement_cache": self._load_settlements(season_id),
            "player_registry": self._load_registry(teams),
        }
        for key in EXTRA_KEYS:
            if key in extras:
                state[key] = extras[key]
        return state

    def _load_meta(self) -> dict:
        meta: dict = {}
        for row in self._conn.execute("SELECT key, value_json FROM save_meta"):
            meta[row["key"]] = json.loads(row["value_json"])
        for required in ("save_name", "season_number", "current_week", "season_complete"):
            if required not in meta:
                raise db_connection.SaveDatabaseError(f"存档元数据缺少 {required}，数据库可能损坏。")
        return meta

    def _load_teams(self) -> List[dict]:
        teams: List[dict] = []
        for row in self._conn.execute("SELECT team_id, name, division FROM teams ORDER BY ordinal"):
            roster = []
            for player in self._conn.execute(
                """
                SELECT player_id, name, position, ability, is_real, slot_number, initial_market_value
                FROM players WHERE team_id = ? ORDER BY roster_index
                """,
                (row["team_id"],),
            ):
                roster.append(
                    {
                        "player_id": player["player_id"],
                        "name": player["name"],
                        "position": player["position"],
                        "ability": int(player["ability"]),
                        "is_real": bool(player["is_real"]),
                        "slot_number": int(player["slot_number"]),
                        "initial_market_value": player["initial_market_value"],
                    }
                )
            teams.append(
                {
                    "team_id": row["team_id"],
                    "name": row["name"],
                    "division": row["division"],
                    "data": {"name": row["name"], "division": row["division"], "roster": roster},
                }
            )
        return teams

    def _team_id_by_name(self, teams: List[dict]) -> Dict[str, int]:
        return {team["name"]: team["team_id"] for team in teams}

    def _load_simulated_weeks(self, season_id: int, teams: List[dict], meta: dict) -> List[dict]:
        # 旧字典恒包含第 1..current_week 周的条目（包括没有比赛的休赛周与
        # 空杯赛周），比赛日内容来自已完成比赛行。
        current_week = int(meta["current_week"])
        if current_week <= 0:
            return []
        weeks_meta = {int(week["week_number"]): week for week in meta["weeks"]}
        team_names_by_id = {team["team_id"]: team["name"] for team in teams}
        rows = self._conn.execute(
            """
            SELECT match_id, category, week_number, round_number, ordinal, home_team_id, away_team_id,
                   home_goals, away_goals, competition
            FROM matches
            WHERE season_id = ? AND status = 'completed'
            ORDER BY week_number, category, round_number, ordinal
            """,
            (season_id,),
        ).fetchall()

        # 先按 (week, category) 收集，再按 (round_number, competition) 分组为
        # 比赛日，恢复旧字典的嵌套结构。
        week_categories: Dict[Tuple[int, str], List[sqlite3.Row]] = {}
        for row in rows:
            week_categories.setdefault((int(row["week_number"]), row["category"]), []).append(row)

        matchday_key_by_category = {
            "premier": "premier_matchdays",
            "second": "second_matchdays",
            "cup": "cup_matchdays",
            "playoff": "playoff_matchdays",
        }
        weeks: List[dict] = []
        for week_number in range(1, current_week + 1):
            week_meta = weeks_meta.get(week_number, {})
            week_entry = {
                "week_number": week_number,
                "label": week_meta.get("label", f"第 {week_number} 周"),
                "kind": week_meta.get("kind", "league_week"),
                "premier_matchdays": [],
                "second_matchdays": [],
                "cup_matchdays": [],
                "playoff_matchdays": [],
            }
            for category in ("premier", "second", "cup", "playoff"):
                category_rows = week_categories.get((week_number, category), [])
                if not category_rows:
                    continue
                matchdays: List[dict] = []
                grouped: Dict[Tuple[int, str], List[sqlite3.Row]] = {}
                for row in category_rows:
                    grouped.setdefault((int(row["round_number"]), row["competition"]), []).append(row)
                for (round_number, competition), result_rows in grouped.items():
                    matchdays.append(
                        {
                            "round_number": round_number,
                            "competition": competition,
                            "results": [
                                self._load_result(row, team_names_by_id) for row in result_rows
                            ],
                        }
                    )
                week_entry[matchday_key_by_category[category]] = matchdays
            weeks.append(week_entry)
        return weeks

    def _load_result(self, row: sqlite3.Row, team_names_by_id: Dict[int, str]) -> dict:
        match_id = row["match_id"]
        return {
            "home_team": team_names_by_id[row["home_team_id"]],
            "away_team": team_names_by_id[row["away_team_id"]],
            "home_goals": row["home_goals"],
            "away_goals": row["away_goals"],
            "key_events": [
                event["event_text"]
                for event in self._conn.execute(
                    "SELECT event_text FROM match_events WHERE match_id = ? ORDER BY sequence_no",
                    (match_id,),
                )
            ],
            "competition": row["competition"],
            "player_stats": self._load_result_player_stats(match_id),
        }

    def _load_result_player_stats(self, match_id: str) -> Dict[str, dict]:
        # 旧字典形状：只包含真实球员的非零增量（零值 appeared 行仅存于数据库，
        # 供查询层使用）。player_id 在真实球员中唯一，直接按 ID 关联，避免
        # 转会后 team_id 变化丢失历史归属行。
        stats: Dict[str, dict] = {}
        for row in self._conn.execute(
            f"""
            SELECT pms.player_id AS player_id, {', '.join(f'pms.{column}' for column in STAT_COLUMNS)}
            FROM player_match_stats AS pms
            JOIN players AS p ON p.player_id = pms.player_id
            WHERE pms.match_id = ? AND p.is_real = 1
            ORDER BY pms.rowid
            """,
            (match_id,),
        ):
            delta = {column: int(row[column]) for column in STAT_COLUMNS}
            if any(delta.values()):
                stats[row["player_id"]] = delta
        return stats

    def _load_season_runtime(self, season_id: int) -> dict:
        row = self._conn.execute(
            "SELECT data_json FROM season_runtime WHERE season_id = ?",
            (season_id,),
        ).fetchone()
        if row is None:
            return {}
        return json.loads(row["data_json"])

    def _load_history(self) -> List[dict]:
        rows = self._conn.execute(
            """
            SELECT sa.archive_json FROM season_archives AS sa
            JOIN seasons AS s ON s.season_id = sa.season_id
            ORDER BY s.season_number
            """
        ).fetchall()
        return [json.loads(row["archive_json"]) for row in rows]

    def _load_pool(self) -> List[dict]:
        return [
            {
                "name": row["name"],
                "position": row["position"],
                "ability": int(row["ability"]),
                "initial_market_value": row["initial_market_value"],
            }
            for row in self._conn.execute(
                "SELECT name, position, ability, initial_market_value FROM real_player_pool ORDER BY ordinal"
            )
        ]

    def _load_pending(self, action_type: str, single: bool = False):
        rows = self._conn.execute(
            "SELECT payload_json FROM pending_actions WHERE type = ? ORDER BY ordinal",
            (action_type,),
        ).fetchall()
        payloads = [json.loads(row["payload_json"]) for row in rows]
        if single:
            return payloads[0] if payloads else {}
        return payloads

    def _load_transfers(self) -> List[dict]:
        return [
            {
                "season_number": int(row["season_number"]),
                "week_number": int(row["week_number"]),
                "window": row["window"],
                "trade_id": row["trade_id"],
                "team_a": row["team_a"],
                "team_b": row["team_b"],
                "team_a_players": json.loads(row["team_a_players_json"]),
                "team_b_players": json.loads(row["team_b_players_json"]),
                "team_a_total_value": row["team_a_total_value"],
                "team_b_total_value": row["team_b_total_value"],
                "value_gap": row["value_gap"],
                "approved": bool(row["approved"]),
                "status": row["status"],
                "recalculated": bool(row["recalculated"]),
                "reason": row["reason"],
            }
            for row in self._conn.execute("SELECT * FROM transfers ORDER BY transfer_row_id")
        ]

    def _load_settlements(self, season_id: int) -> dict:
        cache: dict = {"winter": {}, "final": {}}
        for row in self._conn.execute(
            "SELECT stage, player_key, season_rating, market_value FROM player_settlements WHERE season_id = ?",
            (season_id,),
        ):
            cache[row["stage"]][row["player_key"]] = {
                "season_rating": row["season_rating"],
                "market_value": row["market_value"],
            }
        return cache

    def _load_registry(self, teams: List[dict]) -> List[dict]:
        registry: List[dict] = []
        for team in teams:
            for player in team["data"]["roster"]:
                if not player["is_real"]:
                    continue
                item = dict(player)
                item["team_name"] = team["name"]
                item["division"] = team["division"]
                registry.append(item)
        return registry

    def _require_season_id(self, season_number: int) -> int:
        row = self._conn.execute(
            "SELECT season_id FROM seasons WHERE season_number = ?",
            (season_number,),
        ).fetchone()
        if row is None:
            raise db_connection.SaveDatabaseError(f"存档缺少第 {season_number} 赛季记录，数据库可能损坏。")
        return int(row["season_id"])

    # -- 持久化：工作字典 → 表 -------------------------------------------

    def persist_state(self, state: dict) -> None:
        """把工作字典写入当前赛季相关表。必须在活动事务内调用。"""
        season_id = self._ensure_season_row(state)
        self._persist_meta(state)
        self._persist_teams(state)
        self._persist_pool(state)
        self._persist_season_runtime(state, season_id)
        self._persist_scheduled_fixtures(state, season_id)
        self._persist_simulated_weeks(state, season_id)
        self._persist_settlements(state, season_id)
        self._persist_pending(state)
        self._persist_transfers(state)
        self._persist_draft_log(state)
        self._persist_history(state)

    def _ensure_season_row(self, state: dict) -> int:
        season_number = int(state["season_number"])
        self._conn.execute(
            "INSERT OR IGNORE INTO seasons (season_number, status) VALUES (?, 'active')",
            (season_number,),
        )
        status = "completed" if state.get("season_complete") else "active"
        self._conn.execute(
            "UPDATE seasons SET status = ? WHERE season_number = ?",
            (status, season_number),
        )
        return self._require_season_id(season_number)

    def _persist_meta(self, state: dict) -> None:
        meta = {
            "save_name": state["save_name"],
            "season_number": int(state["season_number"]),
            "current_week": int(state["current_week"]),
            "season_complete": bool(state.get("season_complete", False)),
            "premier_team_names": list(state.get("premier_team_names", [])),
            "second_team_names": list(state.get("second_team_names", [])),
            "next_premier_team_names": list(state.get("next_premier_team_names", [])),
            "next_second_team_names": list(state.get("next_second_team_names", [])),
            "weeks": state.get("weeks", []),
            "draft_pool_index": int(state.get("draft_pool_index", 0)),
        }
        for key, value in meta.items():
            self._conn.execute(
                """
                INSERT INTO save_meta (key, value_json) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                """,
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def _persist_teams(self, state: dict) -> None:
        # 升降级会让球队分区互换，直接 UPSERT 最终 ordinal 会互相冲突；
        # 先把现有 ordinal 挪到负数临时区，再写入最终位置（team_id 稳定不变）。
        self._conn.execute("UPDATE teams SET ordinal = ordinal - 10000")
        ordinal = 0
        for division in DIVISIONS:
            for team_data in state.get("premier_teams" if division == "一级联赛" else "second_teams", []):
                self._conn.execute(
                    """
                    INSERT INTO teams (ordinal, name, division) VALUES (?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET division = excluded.division, ordinal = excluded.ordinal
                    """,
                    (ordinal, team_data["name"], team_data["division"]),
                )
                ordinal += 1

        team_rows = {
            row["name"]: row["team_id"]
            for row in self._conn.execute("SELECT team_id, name FROM teams")
        }
        for division in DIVISIONS:
            for team_data in state.get("premier_teams" if division == "一级联赛" else "second_teams", []):
                team_id = team_rows[team_data["name"]]
                self._conn.execute("DELETE FROM players WHERE team_id = ?", (team_id,))
                for roster_index, player in enumerate(team_data["roster"]):
                    self._conn.execute(
                        """
                        INSERT INTO players (team_id, roster_index, player_id, name, position, ability, is_real, slot_number, initial_market_value)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            team_id,
                            roster_index,
                            player["player_id"],
                            player["name"],
                            player["position"],
                            int(player["ability"]),
                            1 if player["is_real"] else 0,
                            int(player["slot_number"]),
                            player.get("initial_market_value"),
                        ),
                    )

    def _persist_pool(self, state: dict) -> None:
        self._conn.execute("DELETE FROM real_player_pool")
        for ordinal, profile in enumerate(state.get("real_player_pool", [])):
            self._conn.execute(
                "INSERT INTO real_player_pool (ordinal, name, position, ability, initial_market_value) VALUES (?, ?, ?, ?, ?)",
                (
                    ordinal,
                    profile["name"],
                    profile["position"],
                    int(profile["ability"]),
                    profile.get("initial_market_value"),
                ),
            )

    def _persist_season_runtime(self, state: dict, season_id: int) -> None:
        extras = {key: state[key] for key in EXTRA_KEYS if key in state}
        payload = {
            "promotion_playoff": state.get("promotion_playoff", {}),
            "ranking_playoffs": state.get("ranking_playoffs", {}),
            "cup_state": state.get("cup_state", {}),
            "extras": extras,
        }
        self._conn.execute(
            """
            INSERT INTO season_runtime (season_id, data_json) VALUES (?, ?)
            ON CONFLICT(season_id) DO UPDATE SET data_json = excluded.data_json
            """,
            (season_id, json.dumps(payload, ensure_ascii=False)),
        )

    def _persist_scheduled_fixtures(self, state: dict, season_id: int) -> None:
        """为两级联赛尚未入库的比赛生成 scheduled 行（含稳定 match_id）。

        使用 INSERT OR IGNORE：已完成的比赛行不会被覆盖；球队循环顺序在赛季内
        恒定，因此 (season, category, week, round, ordinal) 唯一且可重现。
        每赛季只需生成一次，由 save_meta 标记防止每周重复执行。
        """
        season_number = int(state["season_number"])
        flag_key = f"fixtures_created_season_{season_number}"
        if self._meta_flag_set(flag_key):
            return
        premier_teams = [self._team_object(team_data) for team_data in state.get("premier_teams", [])]
        second_teams = [self._team_object(team_data) for team_data in state.get("second_teams", [])]
        if not premier_teams or not second_teams:
            return
        weeks = build_week_calendar(build_league_schedule(premier_teams))
        schedule_by_division = {
            "premier": {fixtures[0].round_number: fixtures for fixtures in build_league_schedule(premier_teams)},
            "second": {fixtures[0].round_number: fixtures for fixtures in build_league_schedule(second_teams)},
        }
        team_ids = {
            row["name"]: row["team_id"]
            for row in self._conn.execute("SELECT team_id, name FROM teams")
        }
        for week in weeks:
            for category, round_numbers in (
                ("premier", week.premier_round_numbers),
                ("second", week.second_round_numbers),
            ):
                for round_number in round_numbers:
                    fixtures = schedule_by_division[category].get(round_number, [])
                    for ordinal, fixture in enumerate(fixtures):
                        self._conn.execute(
                            """
                            INSERT OR IGNORE INTO matches (
                                match_id, season_id, category, competition, week_number, round_number, ordinal,
                                home_team_id, away_team_id, status
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled')
                            """,
                            (
                                match_id_for(season_number, category, week.week_number, round_number, ordinal),
                                season_id,
                                category,
                                fixture.competition,
                                week.week_number,
                                round_number,
                                ordinal,
                                team_ids[fixture.home_team.name],
                                team_ids[fixture.away_team.name],
                            ),
                        )
        self._set_meta_flag(flag_key)

    def _meta_flag_set(self, key: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM save_meta WHERE key = ?", (key,)).fetchone()
        return row is not None

    def _set_meta_flag(self, key: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO save_meta (key, value_json) VALUES (?, 'true')",
            (key,),
        )

    def reset_season_data(self, season_number: int) -> None:
        """清空某赛季的全部派生数据（赛季中途重新初始化时调用）。

        旧 JSON 语义：重新初始化会用全新状态覆盖当前赛季。SQLite 下对应
        删除该赛季的比赛、统计、结算、运行时状态与归档，赛程行随标记
        一起重建；teams/players/transfers 等跨赛季数据不受影响。
        """
        row = self._conn.execute(
            "SELECT season_id FROM seasons WHERE season_number = ?",
            (season_number,),
        ).fetchone()
        if row is None:
            return
        season_id = int(row["season_id"])
        self._conn.execute(
            "DELETE FROM player_match_stats WHERE match_id IN (SELECT match_id FROM matches WHERE season_id = ?)",
            (season_id,),
        )
        self._conn.execute(
            "DELETE FROM match_events WHERE match_id IN (SELECT match_id FROM matches WHERE season_id = ?)",
            (season_id,),
        )
        self._conn.execute("DELETE FROM matches WHERE season_id = ?", (season_id,))
        self._conn.execute("DELETE FROM player_settlements WHERE season_id = ?", (season_id,))
        self._conn.execute("DELETE FROM season_runtime WHERE season_id = ?", (season_id,))
        self._conn.execute("DELETE FROM season_archives WHERE season_id = ?", (season_id,))
        self._conn.execute("DELETE FROM awards WHERE season_id = ?", (season_id,))
        self._conn.execute("DELETE FROM drafts WHERE season_number = ?", (season_number,))
        self._conn.execute(
            "DELETE FROM save_meta WHERE key = ?",
            (f"fixtures_created_season_{season_number}",),
        )

    @staticmethod
    def _team_object(team_data: dict):
        from football_simulator.models import Player, Team

        roster = tuple(
            Player(
                player_id=player["player_id"],
                name=player["name"],
                position=player["position"],
                ability=int(player["ability"]),
                is_real=bool(player["is_real"]),
                slot_number=int(player["slot_number"]),
                initial_market_value=player.get("initial_market_value"),
            )
            for player in team_data["roster"]
        )
        return Team(name=team_data["name"], roster=roster, division=team_data["division"])

    def _persist_simulated_weeks(self, state: dict, season_id: int) -> None:
        season_number = int(state["season_number"])
        team_ids = {
            row["name"]: row["team_id"]
            for row in self._conn.execute("SELECT team_id, name FROM teams")
        }
        roster_by_player_id = self._roster_index(state, team_ids)
        existing_match_ids = {
            (row["category"], int(row["week_number"]), int(row["round_number"]), int(row["ordinal"])): row["match_id"]
            for row in self._conn.execute(
                "SELECT match_id, category, week_number, round_number, ordinal FROM matches WHERE season_id = ?",
                (season_id,),
            )
        }
        already_completed = {
            row["match_id"]
            for row in self._conn.execute(
                "SELECT match_id FROM matches WHERE season_id = ? AND status = 'completed'",
                (season_id,),
            )
        }

        for simulated_week in state.get("simulated_weeks", []):
            week_number = int(simulated_week["week_number"])
            for matchday_key, category in CATEGORY_BY_MATCHDAY_KEY.items():
                for matchday in simulated_week.get(matchday_key, []):
                    round_number = int(matchday["round_number"])
                    competition = matchday.get("competition", "一级联赛")
                    for ordinal, result in enumerate(matchday.get("results", [])):
                        key = (category, week_number, round_number, ordinal)
                        match_id = existing_match_ids.get(key) or match_id_for(
                            season_number, category, week_number, round_number, ordinal
                        )
                        if match_id in already_completed:
                            # 已完成的比赛不可变：比分与详情（含比赛当时的
                            # player_match_stats 归属）只在首次完成时写入，
                            # 之后的 persist 不得用当前阵容重写历史归属。
                            continue
                        self._conn.execute(
                            """
                            INSERT INTO matches (
                                match_id, season_id, category, competition, week_number, round_number, ordinal,
                                home_team_id, away_team_id, status, home_goals, away_goals
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?)
                            ON CONFLICT(match_id) DO UPDATE SET
                                status = 'completed',
                                home_goals = excluded.home_goals,
                                away_goals = excluded.away_goals
                            """,
                            (
                                match_id,
                                season_id,
                                category,
                                competition,
                                week_number,
                                round_number,
                                ordinal,
                                team_ids[result["home_team"]],
                                team_ids[result["away_team"]],
                                int(result["home_goals"]),
                                int(result["away_goals"]),
                            ),
                        )
                        self._persist_match_details(match_id, result, roster_by_player_id, team_ids)

    def _roster_index(self, state: dict, team_ids: Dict[str, int]) -> Dict[str, Tuple[int, dict]]:
        index: Dict[str, Tuple[int, dict]] = {}
        for division_key in ("premier_teams", "second_teams"):
            for team_data in state.get(division_key, []):
                team_id = team_ids[team_data["name"]]
                for player in team_data["roster"]:
                    index[player["player_id"]] = (team_id, player)
        return index

    def _persist_match_details(
        self,
        match_id: str,
        result: dict,
        roster_by_player_id: Dict[str, Tuple[int, dict]],
        team_ids: Dict[str, int],
    ) -> None:
        self._conn.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))
        for sequence_no, event_text in enumerate(result.get("key_events", [])):
            self._conn.execute(
                "INSERT INTO match_events (match_id, sequence_no, event_text) VALUES (?, ?, ?)",
                (match_id, sequence_no, event_text),
            )

        self._conn.execute("DELETE FROM player_match_stats WHERE match_id = ?", (match_id,))
        stats_by_player_id = {
            player_id: delta for player_id, delta in result.get("player_stats", {}).items()
        }
        # 比赛当时两队注册阵容全部写入 appeared=1 行（六项可为 0）。
        # roster_by_player_id 在本次持久化开始时构建，与比赛当周阵容一致。
        for side_name in (result["home_team"], result["away_team"]):
            team_id = team_ids[side_name]
            for player_id, (player_team_id, _player) in roster_by_player_id.items():
                if player_team_id != team_id:
                    continue
                delta = stats_by_player_id.get(player_id, {})
                values = {column: int(delta.get(column, 0)) for column in STAT_COLUMNS}
                self._conn.execute(
                    f"""
                    INSERT OR REPLACE INTO player_match_stats (
                        match_id, player_id, team_id, appeared, {', '.join(STAT_COLUMNS)}
                    ) VALUES (?, ?, ?, 1, {', '.join('?' for _ in STAT_COLUMNS)})
                    """,
                    (match_id, player_id, team_id, *[values[column] for column in STAT_COLUMNS]),
                )

    def _persist_settlements(self, state: dict, season_id: int) -> None:
        self._conn.execute("DELETE FROM player_settlements WHERE season_id = ?", (season_id,))
        cache = state.get("settlement_cache", {})
        for stage in ("winter", "final"):
            for player_key, item in cache.get(stage, {}).items():
                self._conn.execute(
                    """
                    INSERT INTO player_settlements (season_id, stage, player_key, season_rating, market_value)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (season_id, stage, player_key, item.get("season_rating"), item.get("market_value")),
                )

    def _persist_pending(self, state: dict) -> None:
        self._conn.execute("DELETE FROM pending_actions")
        for item in state.get("pending_ability_review", []):
            self._conn.execute(
                "INSERT INTO pending_actions (type, payload_json) VALUES ('ability_review', ?)",
                (json.dumps(item, ensure_ascii=False),),
            )
        for item in state.get("pending_transfer_review", []):
            self._conn.execute(
                "INSERT INTO pending_actions (type, payload_json) VALUES ('transfer_review', ?)",
                (json.dumps(item, ensure_ascii=False),),
            )
        pending_draft = state.get("pending_draft", {})
        if pending_draft:
            self._conn.execute(
                "INSERT INTO pending_actions (type, payload_json) VALUES ('draft', ?)",
                (json.dumps(pending_draft, ensure_ascii=False),),
            )

    def _persist_transfers(self, state: dict) -> None:
        self._conn.execute("DELETE FROM transfers")
        for row in state.get("transfer_history", []):
            self._conn.execute(
                """
                INSERT INTO transfers (
                    season_number, week_number, window, trade_id, team_a, team_b,
                    team_a_players_json, team_b_players_json, team_a_total_value, team_b_total_value,
                    value_gap, approved, status, recalculated, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(row["season_number"]),
                    int(row["week_number"]),
                    row["window"],
                    row.get("trade_id"),
                    row["team_a"],
                    row["team_b"],
                    json.dumps(row.get("team_a_players", []), ensure_ascii=False),
                    json.dumps(row.get("team_b_players", []), ensure_ascii=False),
                    float(row.get("team_a_total_value", 0.0)),
                    float(row.get("team_b_total_value", 0.0)),
                    float(row.get("value_gap", 0.0)),
                    1 if row.get("approved") else 0,
                    row.get("status", ""),
                    1 if row.get("recalculated") else 0,
                    row.get("reason", ""),
                ),
            )

    def _persist_draft_log(self, state: dict) -> None:
        season_number = int(state["season_number"])
        self._conn.execute("DELETE FROM drafts WHERE season_number = ?", (season_number,))
        last_draft = state.get("last_draft")
        if last_draft:
            self._conn.execute(
                "INSERT INTO drafts (season_number, log_json) VALUES (?, ?)",
                (season_number, json.dumps(last_draft, ensure_ascii=False)),
            )

    def _persist_history(self, state: dict) -> None:
        """归档按赛季号 upsert；内容未变化的归档不重写。"""
        for archive in state.get("history", []):
            season_number = int(archive["season_number"])
            payload = json.dumps(archive, ensure_ascii=False)
            existing = self._conn.execute(
                """
                SELECT sa.archive_json FROM season_archives AS sa
                JOIN seasons AS s ON s.season_id = sa.season_id
                WHERE s.season_number = ?
                """,
                (season_number,),
            ).fetchone()
            if existing is not None and existing["archive_json"] == payload:
                continue
            season_id = self._require_season_id(season_number)
            self._conn.execute(
                """
                INSERT INTO season_archives (season_id, archive_json) VALUES (?, ?)
                ON CONFLICT(season_id) DO UPDATE SET archive_json = excluded.archive_json
                """,
                (season_id, payload),
            )
            self._persist_awards(season_id, archive)

    def _persist_awards(self, season_id: int, archive: dict) -> None:
        self._conn.execute("DELETE FROM awards WHERE season_id = ?", (season_id,))
        season_awards = archive.get("season_awards", {})
        for item in season_awards.get("top20", []):
            self._conn.execute(
                """
                INSERT OR REPLACE INTO awards (season_id, competition, award_type, rank, player_key, player_label, team_name, score)
                VALUES (?, NULL, 'top20', ?, ?, ?, ?, ?)
                """,
                (season_id, item.get("rank"), item.get("player_id"), item.get("label"), item.get("team_name"), item.get("score")),
            )
        for competition, values in season_awards.get("competitions", {}).items():
            for award_type in ("top_scorer", "assist_leader", "mvp"):
                item = values.get(award_type)
                if not item:
                    continue
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO awards (season_id, competition, award_type, rank, player_key, player_label, team_name, score)
                    VALUES (?, ?, ?, NULL, ?, ?, ?, ?)
                    """,
                    (
                        season_id,
                        competition,
                        award_type,
                        item.get("player_id"),
                        item.get("label"),
                        item.get("team_name"),
                        item.get("score"),
                    ),
                )
