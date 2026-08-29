"""阶段 2 查询层测试：比赛目录 / 单场详情 / 邻场定位。

- 第 1 赛季赛前（仅初始化）：760 场两级联赛全部 scheduled；
- 第 1 赛季完整跑完：760 场联赛 + 6 场升级附加赛全部 completed；
- 已赛详情：22 行 appeared 球员行（含六项全 0 行）、事件完整不截断；
- 未赛详情：赛前页语义（比分 None、事件/球员行为空）；
- 邻场定位：同一 (season, competition) 上下文按 week+ordinal 相邻。
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import List, Tuple

from football_simulator import runtime as sim_runtime
from football_simulator import state as sim_state
from football_simulator.queries import base
from football_simulator.queries.match_queries import (
    get_match_detail,
    get_match_neighbors,
    list_matches,
)
from tests import support

MATCH_SAVE = "query_match"

# 第 1 赛季赛历：联赛在 1-24 周与 29-42 周（冬季休赛 25-28 周），
# 升级附加赛在 46-49 周。
SEASON_ONE_LEAGUE_WEEKS = tuple(list(range(1, 25)) + list(range(29, 43)))
WINTER_GAP_PREV_WEEK = 24  # 第 29 周的上一场联赛应在第 24 周


def list_order_ids(conn, season_number: int, competition=None) -> List[str]:
    """list_matches 的期望排序（week, category, round, ordinal, match_id）。"""
    clause = ""
    params: tuple = (season_number,)
    if competition is not None:
        clause = " AND m.competition = ?"
        params = (season_number, competition)
    return [
        row["match_id"]
        for row in conn.execute(
            f"""
            SELECT m.match_id AS match_id
            FROM matches AS m
            JOIN seasons AS s ON s.season_id = m.season_id
            WHERE s.season_number = ?{clause}
            ORDER BY m.week_number, m.category, m.round_number, m.ordinal, m.match_id
            """,
            params,
        )
    ]


def neighbor_sort_key(row) -> Tuple:
    """邻场上下文序：week + ordinal（确定性平局裁决）。"""
    return (row.week_number, row.ordinal, row.category, row.round_number, row.match_id)


class _ReadOnlyMatchSaveCase(unittest.TestCase):
    SAVE_NAME = MATCH_SAVE

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp_dir = tempfile.mkdtemp(prefix="fs_query_match_")
        sim_runtime.set_save_root_override(Path(cls._tmp_dir).resolve())
        sim_state.set_rng_provider(support.seeded_provider())

    @classmethod
    def tearDownClass(cls) -> None:
        sim_state.set_rng_provider(None)
        sim_runtime.set_save_root_override(None)
        shutil.rmtree(cls._tmp_dir, ignore_errors=True)

    @classmethod
    def open_conn(cls):
        return base.open_read_connection(cls.SAVE_NAME)

    @staticmethod
    def sql_ordered_match_ids(conn, season_number: int, competition=None) -> List[str]:
        """邻场上下文序（week, ordinal, category, round, match_id）的期望序列。"""
        clause = ""
        params = (season_number,)
        if competition is not None:
            clause = " AND m.competition = ?"
            params = (season_number, competition)
        return [
            row["match_id"]
            for row in conn.execute(
                f"""
                SELECT m.match_id AS match_id
                FROM matches AS m
                JOIN seasons AS s ON s.season_id = m.season_id
                WHERE s.season_number = ?{clause}
                ORDER BY m.week_number, m.ordinal, m.category, m.round_number, m.match_id
                """,
                params,
            )
        ]


class PreseasonMatchQueryTest(_ReadOnlyMatchSaveCase):
    """刚初始化（第 0 周）：全部联赛赛程已生成且未赛。"""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        support.create_save(cls.SAVE_NAME)

    def test_all_fixtures_scheduled_before_simulation(self) -> None:
        with self.open_conn() as conn:
            rows = list_matches(conn, 1)
            self.assertEqual(len(rows), 760)  # 40 队 × 38 轮 / 2
            self.assertTrue(all(row.status == "scheduled" for row in rows))
            self.assertTrue(all(row.home_goals is None and row.away_goals is None for row in rows))
            self.assertTrue(all(not row.is_completed for row in rows))
            self.assertEqual([row.match_id for row in rows], list_order_ids(conn, 1))
            # 赛历：只在 1-24 / 29-42 周有联赛比赛。
            self.assertEqual({row.week_number for row in rows}, set(SEASON_ONE_LEAGUE_WEEKS))
            competitions = {row.competition: row.category for row in rows}
            self.assertEqual(
                competitions, {"一级联赛": "premier", "次级联赛": "second"}
            )

    def test_preseason_filters(self) -> None:
        with self.open_conn() as conn:
            self.assertEqual(list_matches(conn, 1, status="completed"), [])
            self.assertEqual(len(list_matches(conn, 1, status="scheduled")), 760)
            self.assertEqual(list_matches(conn, 1, competition="优胜者杯"), [])
            week_one = list_matches(conn, 1, week_number=1)
            self.assertEqual(len(week_one), 20)
            self.assertTrue(all(row.week_number == 1 for row in week_one))
            team_id = base.load_team_refs(conn)[0].team_id
            by_team = list_matches(conn, 1, team_id=team_id)
            self.assertTrue(by_team)
            sql_count = conn.execute(
                """
                SELECT COUNT(*) AS n FROM matches
                WHERE season_id = ? AND (home_team_id = ? OR away_team_id = ?)
                """,
                (base.season_id_for(conn, 1), team_id, team_id),
            ).fetchone()["n"]
            self.assertEqual(len(by_team), sql_count)
            self.assertTrue(
                all(team_id in (row.home.team_id, row.away.team_id) for row in by_team)
            )

    def test_scheduled_detail_is_pregame_semantics(self) -> None:
        with self.open_conn() as conn:
            rows = list_matches(conn, 1)
            detail = get_match_detail(conn, rows[0].match_id)
            self.assertEqual(detail.match.status, "scheduled")
            self.assertIsNone(detail.match.home_goals)
            self.assertIsNone(detail.match.away_goals)
            self.assertIsNone(detail.match.score_display)
            self.assertEqual(detail.key_events, [])
            self.assertEqual(detail.player_lines, [])
            self.assertEqual(
                detail.match.category,
                base.CATEGORY_BY_COMPETITION[detail.match.competition],
            )

    def test_unknown_season_or_match_raise_key_error(self) -> None:
        with self.open_conn() as conn:
            with self.assertRaises(KeyError):
                list_matches(conn, 99)
            with self.assertRaises(KeyError):
                get_match_detail(conn, "m-no-such-match")
            with self.assertRaises(KeyError):
                get_match_neighbors(conn, "m-no-such-match")
            first_id = list_matches(conn, 1)[0].match_id
            with self.assertRaises(KeyError):
                get_match_neighbors(conn, first_id, competition="优胜者杯")


class CompletedSeasonMatchQueryTest(_ReadOnlyMatchSaveCase):
    """第 1 赛季完整跑完：所有比赛已完成（不可变）。"""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        support.create_save(cls.SAVE_NAME)
        support.run_season(cls.SAVE_NAME)

    def test_completed_season_counts_and_statuses(self) -> None:
        with self.open_conn() as conn:
            rows = list_matches(conn, 1)
            self.assertEqual(len(rows), 766)  # 380 premier + 380 second + 6 附加赛
            self.assertTrue(all(row.status == "completed" for row in rows))
            self.assertTrue(all(row.is_completed for row in rows))
            self.assertTrue(
                all(row.home_goals is not None and row.away_goals is not None for row in rows)
            )
            self.assertEqual(len(list_matches(conn, 1, status="completed")), 766)
            self.assertEqual(len(list_matches(conn, 1, status="scheduled")), 0)
            by_competition = {competition: 0 for competition in base.ALL_COMPETITIONS}
            for row in rows:
                by_competition[row.competition] += 1
            self.assertEqual(by_competition["一级联赛"], 380)
            self.assertEqual(by_competition["次级联赛"], 380)
            self.assertEqual(by_competition["升级附加赛"], 6)
            self.assertEqual(sum(by_competition.values()), len(rows))
            self.assertEqual([row.match_id for row in rows], list_order_ids(conn, 1))

    def test_completed_detail_lines_and_events(self) -> None:
        with self.open_conn() as conn:
            season_id = base.season_id_for(conn, 1)
            busiest = conn.execute(
                """
                SELECT m.match_id AS match_id FROM matches AS m
                WHERE m.season_id = ? AND m.status = 'completed'
                GROUP BY m.match_id
                ORDER BY (SELECT COUNT(*) FROM match_events e WHERE e.match_id = m.match_id) DESC,
                         m.match_id ASC
                LIMIT 1
                """,
                (season_id,),
            ).fetchone()
            detail = get_match_detail(conn, busiest["match_id"])
            match = detail.match
            self.assertEqual(match.status, "completed")
            self.assertIsInstance(match.home_goals, int)
            self.assertIsInstance(match.away_goals, int)

            # 22 行 appeared=1（主客各 11），六项全 0 的行也必须保留。
            self.assertEqual(len(detail.player_lines), 22)
            home_lines = [line for line in detail.player_lines if line.team.team_id == match.home.team_id]
            away_lines = [line for line in detail.player_lines if line.team.team_id == match.away.team_id]
            self.assertEqual(len(home_lines), 11)
            self.assertEqual(len(away_lines), 11)
            self.assertEqual(
                [0 if line.team.team_id == match.home.team_id else 1 for line in detail.player_lines],
                sorted(0 if line.team.team_id == match.home.team_id else 1 for line in detail.player_lines),
                "球员行应主队在前、客队在后",
            )
            self.assertTrue(
                all(line.team.team_id in (match.home.team_id, match.away.team_id) for line in detail.player_lines)
            )
            zero_lines = [
                line
                for line in detail.player_lines
                if line.goals == 0
                and line.assists == 0
                and line.chances_created == 0
                and line.successful_defenses == 0
                and line.successful_saves == 0
                and line.clean_sheets == 0
            ]
            self.assertTrue(zero_lines, "22 行中应存在六项全 0 的行")

            # 进球归属：引擎只对真实球员记射手（_record_stat 跳过默认球员），
            # 因此球员行进球之和 <= 比分；不得伪造缺失的射手归属。
            self.assertLessEqual(
                sum(line.goals for line in detail.player_lines),
                match.home_goals + match.away_goals,
            )
            self.assertGreaterEqual(sum(line.goals for line in detail.player_lines), 0)
            # 稳定 ID：真实球员 real::<slug>；主客 11 人中位置齐全（1/4/3/3）。
            for line in detail.player_lines:
                if line.player.is_real:
                    self.assertTrue(line.player.player_id.startswith("real::"))
            position_counts: dict = {}
            for line in detail.player_lines:
                position_counts[line.player.position] = position_counts.get(line.player.position, 0) + 1
            self.assertEqual(position_counts, {"GK": 2, "DF": 8, "MF": 6, "FW": 6})

            # 事件：完整、不截断、按 sequence_no 原始顺序。
            sql_events = [
                row["event_text"]
                for row in conn.execute(
                    "SELECT event_text FROM match_events WHERE match_id = ? ORDER BY sequence_no",
                    (busiest["match_id"],),
                )
            ]
            self.assertTrue(sql_events)
            self.assertEqual(detail.key_events, sql_events)
            self.assertTrue(all(isinstance(event, str) and event for event in detail.key_events))

    def test_neighbors_within_competition_context(self) -> None:
        with self.open_conn() as conn:
            expected_ids = self.sql_ordered_match_ids(conn, 1, competition="一级联赛")
            rows = list_matches(conn, 1, competition="一级联赛")
            by_id = {row.match_id: row for row in rows}
            picks = [
                expected_ids[0],
                next(m for m in expected_ids if by_id[m].week_number == 5),
                next(m for m in expected_ids if by_id[m].week_number == 29),
                expected_ids[-1],
            ]
            for match_id in picks:
                prev_id, next_id = get_match_neighbors(conn, match_id, competition="一级联赛")
                index = expected_ids.index(match_id)
                expected_prev = expected_ids[index - 1] if index > 0 else None
                expected_next = expected_ids[index + 1] if index + 1 < len(expected_ids) else None
                self.assertEqual((prev_id, next_id), (expected_prev, expected_next), match_id)
                if prev_id is not None:
                    self.assertEqual(by_id[prev_id].competition, "一级联赛")
                    self.assertLessEqual(by_id[prev_id].week_number, by_id[match_id].week_number)
                if next_id is not None:
                    self.assertEqual(by_id[next_id].competition, "一级联赛")
                    self.assertGreaterEqual(by_id[next_id].week_number, by_id[match_id].week_number)
            # 冬季休赛：第 29 周的上一场联赛在检测上应与第 24 周相邻。
            week29_id = next(m for m in expected_ids if by_id[m].week_number == 29)
            prev_id, _ = get_match_neighbors(conn, week29_id, competition="一级联赛")
            self.assertEqual(by_id[prev_id].week_number, WINTER_GAP_PREV_WEEK)
            # 首场无上一场、末场无下一场。
            self.assertEqual(get_match_neighbors(conn, expected_ids[0], competition="一级联赛")[0], None)
            self.assertEqual(get_match_neighbors(conn, expected_ids[-1], competition="一级联赛")[1], None)

    def test_neighbors_whole_season_context(self) -> None:
        with self.open_conn() as conn:
            expected_ids = self.sql_ordered_match_ids(conn, 1)
            rows = {row.match_id: row for row in list_matches(conn, 1)}
            # 无 competition 过滤：上下文为同赛季全部比赛序。
            for match_id in (expected_ids[0], expected_ids[len(expected_ids) // 2], expected_ids[-1]):
                prev_id, next_id = get_match_neighbors(conn, match_id)
                index = expected_ids.index(match_id)
                expected_prev = expected_ids[index - 1] if index > 0 else None
                expected_next = expected_ids[index + 1] if index + 1 < len(expected_ids) else None
                self.assertEqual((prev_id, next_id), (expected_prev, expected_next), match_id)
            # 相邻性：prev/next 的 week 序相邻（week 相等也允许，同周按 ordinal）。
            middle_id = expected_ids[len(expected_ids) // 2]
            prev_id, next_id = get_match_neighbors(conn, middle_id)
            self.assertLessEqual(rows[prev_id].week_number, rows[middle_id].week_number)
            self.assertGreaterEqual(rows[next_id].week_number, rows[middle_id].week_number)
            # 附加赛末场在其自身赛事上下文中无下一场。
            playoff_ids = self.sql_ordered_match_ids(conn, 1, competition="升级附加赛")
            prev_id, next_id = get_match_neighbors(conn, playoff_ids[-1], competition="升级附加赛")
            self.assertIsNone(next_id)
            self.assertIsNotNone(prev_id)


if __name__ == "__main__":
    unittest.main()
