"""球队查询测试（阶段 2，Agent C2）。

共享夹具：单个临时存档根目录 + 固定随机源。构建流程：
1. 初始化第 1 赛季，推进到冬窗（第 25 周）出现第一批转会待办时整批
   “玩家拒绝”（制造 transfers 表中的拒绝行），随后跑完整个赛季
   （后续窗口按默认批准）；
2. 初始化第 2 赛季并推进 8 周。

整组测试共享该存档（setUp 重新指向存档根目录，atexit 清理），耗时远低于
逐用例重建。
"""

from __future__ import annotations

import atexit
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List

from football_simulator import runtime as sim_runtime
from football_simulator import state as sim_state
from football_simulator.queries import base, team_queries
from football_simulator.state import apply_transfer_review_decisions

from tests.support import (
    advance_week,
    create_save,
    load_snapshot,
    run_season,
    run_weeks,
    seeded_provider,
)

SAVE_NAME = "query_team"
_WINTER_WINDOW_LIMIT = 30

_SHARED: Dict[str, Path] = {}


def _teardown_shared() -> None:
    root = _SHARED.get("root")
    sim_state.set_rng_provider(None)
    sim_runtime.set_save_root_override(None)
    if root is not None:
        shutil.rmtree(str(root), ignore_errors=True)


def _shared_save() -> str:
    if not _SHARED:
        root = Path(tempfile.mkdtemp(prefix="fs_query_team_")).resolve()
        sim_runtime.set_save_root_override(root)
        sim_state.set_rng_provider(seeded_provider())
        create_save(SAVE_NAME)
        # 推进到第一批转会待办（冬窗），整批拒绝，制造“玩家拒绝”历史行。
        snap = None
        for _ in range(_WINTER_WINDOW_LIMIT):
            snap = load_snapshot(SAVE_NAME)
            if snap.pending_transfer_review:
                break
            advance_week(SAVE_NAME)
        if snap is not None and snap.pending_transfer_review:
            decisions = {item["trade_id"]: False for item in snap.pending_transfer_review}
            apply_transfer_review_decisions(SAVE_NAME, decisions)
        run_season(SAVE_NAME)
        create_save(SAVE_NAME)
        run_weeks(SAVE_NAME, 8)
        _SHARED["root"] = root
        atexit.register(_teardown_shared)
    else:
        sim_runtime.set_save_root_override(_SHARED["root"])
        sim_state.set_rng_provider(seeded_provider())
    return SAVE_NAME


class TeamQueryTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.save_name = _shared_save()

    def _archive(self, conn, season_number: int) -> dict:
        row = conn.execute(
            """
            SELECT sa.archive_json FROM season_archives AS sa
            JOIN seasons AS s ON s.season_id = sa.season_id
            WHERE s.season_number = ?
            """,
            (season_number,),
        ).fetchone()
        self.assertIsNotNone(row, f"第 {season_number} 赛季应有归档")
        return json.loads(row["archive_json"])


class ListTeamsTests(TeamQueryTestBase):
    def test_directory_invariants_current_season(self) -> None:
        with base.open_read_connection(self.save_name) as conn:
            rows = team_queries.list_teams(conn, 2)
            self.assertEqual(len(rows), 40)
            premier = [row for row in rows if row.season_division == "一级联赛"]
            second = [row for row in rows if row.season_division == "次级联赛"]
            self.assertEqual(len(premier), 20)
            self.assertEqual(len(second), 20)

            draw_rows = conn.execute(
                """
                SELECT COUNT(*) AS total FROM matches
                WHERE season_id = (SELECT season_id FROM seasons WHERE season_number = 2)
                  AND category = 'premier' AND status = 'completed' AND home_goals = away_goals
                """
            ).fetchone()["total"]
            completed_premier = conn.execute(
                """
                SELECT COUNT(*) AS total FROM matches
                WHERE season_id = (SELECT season_id FROM seasons WHERE season_number = 2)
                  AND category = 'premier' AND status = 'completed'
                """
            ).fetchone()["total"]

            for group, category in ((premier, "premier"), (second, "second")):
                played_values = {row.played for row in group}
                self.assertEqual(len(played_values), 1, "同分区每队已赛场次必须一致")
                for row in group:
                    self.assertEqual(row.points, 3 * row.wins + row.draws)
                total_points = sum(row.points for row in group)
                if category == "premier":
                    self.assertEqual(
                        total_points,
                        3 * completed_premier - draw_rows,
                        "Σ积分 = 3×比赛场数 − 平局场数",
                    )
                ranks = sorted(row.rank for row in group)
                self.assertEqual(ranks, list(range(1, 21)))

    def test_directory_invariants_completed_season(self) -> None:
        with base.open_read_connection(self.save_name) as conn:
            rows = team_queries.list_teams(conn, 1)
            self.assertEqual(len(rows), 40)
            premier = [row for row in rows if row.season_division == "一级联赛"]
            second = [row for row in rows if row.season_division == "次级联赛"]
            self.assertEqual(len(premier), 20)
            self.assertEqual(len(second), 20)
            for group in (premier, second):
                for row in group:
                    self.assertEqual(row.played, 38)
                    self.assertEqual(row.points, 3 * row.wins + row.draws)
                    self.assertEqual(row.wins + row.draws + row.losses, 38)
                self.assertEqual(
                    sorted(row.rank for row in group),
                    list(range(1, 21)),
                )
            # 赛季 1 分区与归档一致；冠军行 = premier_order[0]。
            archive = self._archive(conn, 1)
            champion_row = min(premier, key=lambda row: row.rank or 10_000)
            self.assertEqual(champion_row.rank, 1)
            self.assertEqual(champion_row.team.display_name, archive["premier_order"][0])

    def test_filters(self) -> None:
        with base.open_read_connection(self.save_name) as conn:
            second_rows = team_queries.list_teams(conn, 2, division="次级联赛")
            self.assertEqual(len(second_rows), 20)
            self.assertTrue(all(row.season_division == "次级联赛" for row in second_rows))

            target = second_rows[0].team.display_name
            needle = target[:2]
            searched = team_queries.list_teams(conn, 2, search=needle)
            self.assertTrue(searched)
            self.assertTrue(all(needle in row.team.display_name for row in searched))
            self.assertIn(target, [row.team.display_name for row in searched])

            combined = team_queries.list_teams(conn, 2, division="一级联赛", search=needle)
            self.assertTrue(all(row.season_division == "一级联赛" for row in combined))

    def test_row_matches_manual_aggregation(self) -> None:
        with base.open_read_connection(self.save_name) as conn:
            rows = team_queries.list_teams(conn, 2, division="一级联赛")
            target = rows[len(rows) // 2]
            season_id = conn.execute(
                "SELECT season_id FROM seasons WHERE season_number = 2"
            ).fetchone()["season_id"]
            stats = conn.execute(
                """
                SELECT
                  SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS played,
                  SUM(CASE WHEN status = 'completed' AND home_goals > away_goals THEN 1 ELSE 0 END) AS wins,
                  SUM(CASE WHEN status = 'completed' AND home_goals = away_goals THEN 1 ELSE 0 END) AS draws,
                  SUM(CASE WHEN status = 'completed' AND home_goals < away_goals THEN 1 ELSE 0 END) AS losses,
                  SUM(CASE WHEN status = 'completed' THEN home_goals ELSE 0 END) AS gf,
                  SUM(CASE WHEN status = 'completed' THEN away_goals ELSE 0 END) AS ga
                FROM matches
                WHERE season_id = ? AND category = 'premier' AND home_team_id = ?
                """,
                (season_id, target.team.team_id),
            ).fetchone()
            away_stats = conn.execute(
                """
                SELECT
                  SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS played,
                  SUM(CASE WHEN status = 'completed' AND away_goals > home_goals THEN 1 ELSE 0 END) AS wins,
                  SUM(CASE WHEN status = 'completed' AND away_goals = home_goals THEN 1 ELSE 0 END) AS draws,
                  SUM(CASE WHEN status = 'completed' AND away_goals < home_goals THEN 1 ELSE 0 END) AS losses,
                  SUM(CASE WHEN status = 'completed' THEN away_goals ELSE 0 END) AS gf,
                  SUM(CASE WHEN status = 'completed' THEN home_goals ELSE 0 END) AS ga
                FROM matches
                WHERE season_id = ? AND category = 'premier' AND away_team_id = ?
                """,
                (season_id, target.team.team_id),
            ).fetchone()
            self.assertEqual(target.played, stats["played"] + away_stats["played"])
            self.assertEqual(target.wins, stats["wins"] + away_stats["wins"])
            self.assertEqual(target.draws, stats["draws"] + away_stats["draws"])
            self.assertEqual(target.losses, stats["losses"] + away_stats["losses"])
            self.assertEqual(target.goals_for, stats["gf"] + away_stats["gf"])
            self.assertEqual(target.goals_against, stats["ga"] + away_stats["ga"])
            self.assertEqual(target.points, 3 * target.wins + target.draws)

            # 排行确定性：同参数两次调用结果一致。
            again = team_queries.list_teams(conn, 2, division="一级联赛")
            self.assertEqual(again, rows)


class TeamProfileTests(TeamQueryTestBase):
    def _first_premier_team(self, conn, season_number: int):
        rows = team_queries.list_teams(conn, season_number, division="一级联赛")
        return rows[0]

    def test_roster_shape_and_stable_ids(self) -> None:
        with base.open_read_connection(self.save_name) as conn:
            target = self._first_premier_team(conn, 2)
            profile = team_queries.get_team_season_profile(conn, target.team.team_id, 2)

            self.assertEqual(profile.identity.team_id, target.team.team_id)
            self.assertEqual(len(profile.roster), 11)
            counts: Dict[str, int] = {}
            for line in profile.roster:
                counts[line.player.position] = counts.get(line.player.position, 0) + 1
            self.assertEqual(counts, {"GK": 1, "DF": 4, "MF": 3, "FW": 3})

            # 第 2 赛季第 8 周尚无结算（冬窗结算在第 24 周），身价回退到
            # 注册表初始值；初始值缺失时为 None（不伪造数值）。
            initial_values = {
                row["name"]: row["initial_market_value"]
                for row in conn.execute(
                    "SELECT name, initial_market_value FROM players WHERE is_real = 1 AND name IS NOT NULL"
                )
            }
            for line in profile.roster:
                if line.player.is_real:
                    self.assertTrue(line.player.player_id.startswith("real::"))
                    self.assertEqual(line.market_value, initial_values.get(line.player.display_name))
                else:
                    self.assertTrue(line.player.player_id.startswith(f"default:{target.team.team_id}:"))
                    self.assertIsNone(line.market_value)

    def test_market_value_uses_settlement_then_initial(self) -> None:
        with base.open_read_connection(self.save_name) as conn:
            target = self._first_premier_team(conn, 2)
            season1 = conn.execute("SELECT season_id FROM seasons WHERE season_number = 1").fetchone()["season_id"]
            final_values = {
                row["player_key"]: float(row["market_value"])
                for row in conn.execute(
                    "SELECT player_key, market_value FROM player_settlements WHERE season_id = ? AND stage = 'final'",
                    (season1,),
                )
            }
            winter_values = {
                row["player_key"]: float(row["market_value"])
                for row in conn.execute(
                    "SELECT player_key, market_value FROM player_settlements WHERE season_id = ? AND stage = 'winter'",
                    (season1,),
                )
            }
            initial_values = {
                row["name"]: (float(row["initial_market_value"]) if row["initial_market_value"] is not None else None)
                for row in conn.execute(
                    "SELECT name, initial_market_value FROM players WHERE is_real = 1 AND name IS NOT NULL"
                )
            }

            profile = team_queries.get_team_season_profile(conn, target.team.team_id, 1)
            for line in profile.roster:
                if not line.player.is_real:
                    self.assertIsNone(line.market_value)
                    continue
                expected = final_values.get(line.player.display_name)
                if expected is None:
                    expected = winter_values.get(line.player.display_name)
                if expected is None:
                    expected = initial_values.get(line.player.display_name)
                self.assertIsNotNone(expected, "第 1 赛季真实球员应有结算身价")
                self.assertEqual(line.market_value, expected)

    def test_fixtures_counts_and_status(self) -> None:
        with base.open_read_connection(self.save_name) as conn:
            target = self._first_premier_team(conn, 2)
            profile1 = team_queries.get_team_season_profile(conn, target.team.team_id, 1)
            league1 = [f for f in profile1.fixtures if f.competition == "一级联赛"]
            self.assertEqual(len(league1), 38)
            self.assertTrue(all(f.is_completed for f in league1))
            home = sum(1 for f in league1 if f.home.team_id == target.team.team_id)
            away = sum(1 for f in league1 if f.away.team_id == target.team.team_id)
            self.assertEqual((home, away), (19, 19))
            self.assertTrue(all(f.season_number == 1 for f in profile1.fixtures))

            profile2 = team_queries.get_team_season_profile(conn, target.team.team_id, 2)
            league2 = [f for f in profile2.fixtures if f.competition == "一级联赛"]
            self.assertEqual(len(league2), 38)
            self.assertTrue(any(f.is_completed for f in league2))
            self.assertTrue(any(not f.is_completed for f in league2))
            for fixture in league2:
                if fixture.is_completed:
                    self.assertIsNotNone(fixture.home_goals)
                    self.assertIsNotNone(fixture.away_goals)
                else:
                    self.assertIsNone(fixture.home_goals)
                    self.assertIsNone(fixture.away_goals)
            # 第 2 赛季杯赛已激活，冠军杯小组赛应出现在赛程中。
            cup_fixtures = [f for f in profile2.fixtures if f.competition == "优胜者杯"]
            self.assertTrue(cup_fixtures)
            # 排序确定性：week_number 单调不减。
            weeks = [f.week_number for f in profile2.fixtures]
            self.assertEqual(weeks, sorted(weeks))

    def test_transfers_match_table(self) -> None:
        with base.open_read_connection(self.save_name) as conn:
            trade_rows = conn.execute(
                "SELECT * FROM transfers WHERE season_number = 1 ORDER BY transfer_row_id"
            ).fetchall()
            self.assertTrue(trade_rows, "第 1 赛季应存在转会记录")
            statuses = {row["status"] for row in trade_rows}
            self.assertIn("玩家拒绝", statuses, "夹具应包含被玩家拒绝的转会")

            team_ids = {row["name"]: int(row["team_id"]) for row in conn.execute("SELECT team_id, name FROM teams")}
            expected: Dict[str, Dict[str, List[tuple]]] = {}
            for row in trade_rows:
                players_a = json.loads(row["team_a_players_json"])
                players_b = json.loads(row["team_b_players_json"])
                for team_name, incoming, outgoing, counterpart in (
                    (row["team_a"], players_b, players_a, row["team_b"]),
                    (row["team_b"], players_a, players_b, row["team_a"]),
                ):
                    bucket = expected.setdefault(team_name, {"in": [], "out": []})
                    for item in incoming:
                        bucket["in"].append((row["trade_id"], item["name"], row["status"], row["week_number"], counterpart))
                    for item in outgoing:
                        bucket["out"].append((row["trade_id"], item["name"], row["status"], row["week_number"], counterpart))

            self.assertTrue(expected)
            for team_name, bucket in expected.items():
                profile = team_queries.get_team_season_profile(conn, team_ids[team_name], 1)
                got_in = sorted(
                    (line.trade_id, line.player.display_name, line.status, line.week_number, line.counterpart.display_name)
                    for line in profile.transfers_in
                )
                got_out = sorted(
                    (line.trade_id, line.player.display_name, line.status, line.week_number, line.counterpart.display_name)
                    for line in profile.transfers_out
                )
                self.assertEqual(got_in, sorted(bucket["in"]), f"{team_name} 转入与 transfers 表不一致")
                self.assertEqual(got_out, sorted(bucket["out"]), f"{team_name} 转出与 transfers 表不一致")
                for line in [*profile.transfers_in, *profile.transfers_out]:
                    self.assertTrue(line.player.is_real)
                    self.assertEqual(
                        line.player.player_id,
                        base.canonical_player_id_for_name(line.player.display_name),
                    )

    def test_honors_and_player_awards_separated(self) -> None:
        with base.open_read_connection(self.save_name) as conn:
            rows = team_queries.list_teams(conn, 1, division="一级联赛")
            champion = min(rows, key=lambda row: row.rank or 10_000)
            champion_profile = team_queries.get_team_season_profile(conn, champion.team.team_id, 1)

            # 球队荣誉：来自归档的球队成绩标签（带赛事前缀，如“联赛 第 1 名”）。
            self.assertIn("联赛 第 1 名", champion_profile.team_honors)
            for honor in champion_profile.team_honors:
                self.assertNotIn(honor, {"未参赛", "未知", ""})
                self.assertNotIn(honor, {"top20", "top_scorer", "assist_leader", "mvp"})

            # 非冠军球队的荣誉不应含“联赛 第 1 名”。
            runner_up = max(rows, key=lambda row: row.rank or 0)
            runner_profile = team_queries.get_team_season_profile(conn, runner_up.team.team_id, 1)
            self.assertNotIn("联赛 第 1 名", runner_profile.team_honors)

            # 球员个人奖项：来自 awards 表，按 team_name 匹配（冠军球队可能
            # 没有球员获奖，因此取 Top20 榜首球员所属球队做逐行核对）。
            season_id = conn.execute("SELECT season_id FROM seasons WHERE season_number = 1").fetchone()["season_id"]
            top1_row = conn.execute(
                "SELECT team_name, player_label FROM awards WHERE season_id = ? AND award_type = 'top20' AND rank = 1",
                (season_id,),
            ).fetchone()
            self.assertIsNotNone(top1_row)
            awarded_team_name = top1_row["team_name"]
            awarded_team_id = conn.execute(
                "SELECT team_id FROM teams WHERE name = ?", (awarded_team_name,)
            ).fetchone()["team_id"]

            award_rows = conn.execute(
                "SELECT award_type, competition, rank, player_label FROM awards WHERE season_id = ? AND team_name = ?",
                (season_id, awarded_team_name),
            ).fetchall()
            self.assertTrue(award_rows, "Top20 榜首所属球队应有球员奖项记录")
            awarded_profile = team_queries.get_team_season_profile(conn, awarded_team_id, 1)
            self.assertEqual(len(awarded_profile.player_awards), len(award_rows))
            for line in awarded_profile.player_awards:
                self.assertIn(line.award_type, {"top20", "top_scorer", "assist_leader", "mvp"})
                self.assertTrue(line.player.is_real)
                self.assertTrue(line.player.player_id.startswith("real::"))
                # 奖项与球队荣誉互不混入。
                self.assertNotIn(line.award_type, awarded_profile.team_honors)
                self.assertNotIn(line.player.display_name, awarded_profile.team_honors)

            table_keys = sorted(
                (row["award_type"], row["competition"], row["rank"], row["player_label"]) for row in award_rows
            )
            profile_keys = sorted(
                (line.award_type, line.competition, line.rank, line.player.display_name)
                for line in awarded_profile.player_awards
            )
            self.assertEqual(profile_keys, table_keys)
            self.assertIn(top1_row["player_label"], {line.player.display_name for line in awarded_profile.player_awards})

    def test_standings_row_consistency_with_directory(self) -> None:
        with base.open_read_connection(self.save_name) as conn:
            rows = team_queries.list_teams(conn, 2, division="一级联赛")
            target = rows[3]
            profile = team_queries.get_team_season_profile(conn, target.team.team_id, 2)
            line = profile.standings_row
            self.assertEqual(line.played, target.played)
            self.assertEqual(line.wins, target.wins)
            self.assertEqual(line.draws, target.draws)
            self.assertEqual(line.losses, target.losses)
            self.assertEqual(line.goals_for, target.goals_for)
            self.assertEqual(line.goals_against, target.goals_against)
            self.assertEqual(line.points, target.points)
            self.assertEqual(line.rank, target.rank)
            self.assertEqual(profile.season_division, "一级联赛")

    def test_unknown_team_or_season_raises(self) -> None:
        with base.open_read_connection(self.save_name) as conn:
            with self.assertRaises(KeyError):
                team_queries.get_team_season_profile(conn, 999999, 2)
            with self.assertRaises(KeyError):
                team_queries.get_team_season_profile(conn, 1, 99)
            with self.assertRaises(KeyError):
                team_queries.list_teams(conn, 99)


if __name__ == "__main__":
    unittest.main()
