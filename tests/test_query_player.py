"""阶段 2 查询层测试：球员目录 / 赛季档案 / 生涯。

统计口径验收（与旧快照有意不同，见 queries/__init__.py）：
- 任一球员任一赛季，各 (赛事, 球队) 分段之和 == 该赛季总计（六项统计 + 出场）；
- match_log 行数 == 该口径出场数，且逐行与 player_match_stats 原始行一致；
- 赛季内转会球员出现两个球队分段，历史比赛归属按比赛当时球队；
- awards/player_settlements 的 legacy 键（real::<显示名> / 纯显示名）被收敛为
  稳定 slug ID（real::<slug>）；
- 默认球员不伪造身价/结算/奖项。

测试共用同一存档（只读），在 setUpClass 中构建一次；随机源固定为
tests.support.TEST_SEED，所有断言确定。
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List, Tuple

from football_simulator import runtime as sim_runtime
from football_simulator import state as sim_state
from football_simulator.queries import base
from football_simulator.queries import player_queries
from football_simulator.queries.player_queries import (
    PlayerDirectoryRow,
    PlayerSeasonProfile,
    get_player_career,
    get_player_season_profile,
    list_players,
)

from tests import support

SEASON_ONE_SAVE = "query_player"
TRANSFER_SAVE = "query_player_transfer"

#: PlayerStatLine 的七个计数字段（出场 + 六项统计）。
COUNT_FIELDS = ("appeared",) + support.STAT_FIELDS


def stat_line_values(line) -> Tuple[int, ...]:
    return tuple(getattr(line, field) for field in COUNT_FIELDS)


class _ReadOnlySaveCase(unittest.TestCase):
    """类级共享存档：构建一次，全部用例只读访问（open_read_connection）。"""

    SAVE_NAME = ""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp_dir = tempfile.mkdtemp(prefix="fs_query_player_")
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
    def season_id(conn, season_number: int) -> int:
        return base.season_id_for(conn, season_number)

    @staticmethod
    def sql_player_season_rows(conn, player_id: str, season_number: int) -> Dict[str, Tuple]:
        """player_match_stats 原始行：match_id -> (team_id, 六项)。交叉验证用。"""
        rows = conn.execute(
            """
            SELECT pms.match_id AS match_id,
                   pms.team_id AS team_id,
                   pms.goals AS goals,
                   pms.assists AS assists,
                   pms.chances_created AS chances_created,
                   pms.successful_defenses AS successful_defenses,
                   pms.successful_saves AS successful_saves,
                   pms.clean_sheets AS clean_sheets
            FROM player_match_stats AS pms
            JOIN matches AS m ON m.match_id = pms.match_id
            JOIN seasons AS s ON s.season_id = m.season_id
            WHERE s.season_number = ? AND pms.player_id = ? AND pms.appeared = 1
            """,
            (season_number, player_id),
        ).fetchall()
        return {
            row["match_id"]: (
                int(row["team_id"]),
                tuple(int(row[field]) for field in support.STAT_FIELDS),
            )
            for row in rows
        }

    @staticmethod
    def sql_season_totals(conn, player_id: str, season_number: int) -> Tuple[int, ...]:
        row = conn.execute(
            """
            SELECT COUNT(*) AS appeared,
                   SUM(pms.goals) AS goals,
                   SUM(pms.assists) AS assists,
                   SUM(pms.chances_created) AS chances_created,
                   SUM(pms.successful_defenses) AS successful_defenses,
                   SUM(pms.successful_saves) AS successful_saves,
                   SUM(pms.clean_sheets) AS clean_sheets
            FROM player_match_stats AS pms
            JOIN matches AS m ON m.match_id = pms.match_id
            JOIN seasons AS s ON s.season_id = m.season_id
            WHERE s.season_number = ? AND pms.player_id = ? AND pms.appeared = 1
            """,
            (season_number, player_id),
        ).fetchone()
        return tuple(int(row[field]) for field in COUNT_FIELDS)

    def assert_splits_sum_to_totals(self, profile: PlayerSeasonProfile) -> None:
        """验收不变量：各 (赛事, 球队) 分段之和 == 赛季总计（七项逐字段）。"""
        split_sum = [0] * len(COUNT_FIELDS)
        for split in profile.competition_splits:
            for index, value in enumerate(stat_line_values(split.stats)):
                split_sum[index] += value
        self.assertEqual(
            tuple(split_sum),
            stat_line_values(profile.season_totals),
            f"{profile.identity.player_id} 的 (赛事,球队) 分段之和应等于赛季总计",
        )


class PlayerSeasonOneQueryTest(_ReadOnlySaveCase):
    """第 1 赛季完整跑完后的只读查询（无杯赛赛季）。"""

    SAVE_NAME = SEASON_ONE_SAVE

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        support.create_save(cls.SAVE_NAME)
        support.run_season(cls.SAVE_NAME)

    def _directory(self, conn) -> List[PlayerDirectoryRow]:
        return list_players(conn, 1)

    def _real_rows(self, conn) -> List[PlayerDirectoryRow]:
        return sorted((row for row in self._directory(conn) if row.is_real), key=lambda row: row.player_id)

    def test_directory_covers_season_participants_and_is_deterministic(self) -> None:
        with self.open_conn() as conn:
            rows = self._directory(conn)
            self.assertTrue(rows)
            # 已完结赛季目录只收录该赛季出场过的球员，且全部有出场。
            expected = conn.execute(
                """
                SELECT COUNT(*) AS n FROM players p
                WHERE EXISTS (
                    SELECT 1 FROM player_match_stats pms
                    JOIN matches m ON m.match_id = pms.match_id
                    WHERE pms.player_id = p.player_id AND pms.appeared = 1
                      AND m.season_id = ?
                )
                """,
                (self.season_id(conn, 1),),
            ).fetchone()["n"]
            self.assertEqual(len(rows), expected)
            self.assertTrue(all(row.appeared > 0 for row in rows))
            real_ids = {row.player_id for row in rows if row.is_real}
            sql_real_ids = {
                row["player_id"]
                for row in conn.execute(
                    """
                    SELECT DISTINCT pms.player_id AS player_id
                    FROM player_match_stats AS pms
                    JOIN players AS p ON p.player_id = pms.player_id
                    JOIN matches AS m ON m.match_id = pms.match_id
                    WHERE m.season_id = ? AND pms.appeared = 1 AND p.is_real = 1
                    """,
                    (self.season_id(conn, 1),),
                )
            }
            self.assertEqual(real_ids, sql_real_ids)
            # 稳定 ID 规则：真实球员 real::<slug>，默认球员 default:<team>:<slot>。
            for row in rows[:50]:
                if row.is_real:
                    self.assertTrue(row.player_id.startswith("real::"))
                else:
                    self.assertTrue(row.player_id.startswith("default:"))
            # 同样输入两次调用结果完全一致（确定性）。
            self.assertEqual(rows, self._directory(conn))

    def test_competition_splits_sum_to_season_totals(self) -> None:
        with self.open_conn() as conn:
            real_rows = self._real_rows(conn)
            picks = [row.player_id for row in real_rows[:3]]
            goalkeeper = next((row for row in real_rows if row.position == "GK"), None)
            if goalkeeper is not None:
                picks.append(goalkeeper.player_id)
            for player_id in picks:
                profile = get_player_season_profile(conn, player_id, 1)
                # 赛季总计与独立 SQL 聚合一致。
                self.assertEqual(
                    stat_line_values(profile.season_totals),
                    self.sql_season_totals(conn, player_id, 1),
                    player_id,
                )
                # 分段之和 == 赛季总计（七项逐字段）。
                self.assert_splits_sum_to_totals(profile)
                # 分段评分在合理范围。
                for split in profile.competition_splits:
                    self.assertGreaterEqual(split.rating, 0.0)
                    self.assertLessEqual(split.rating, 10.0)
                    self.assertIn(split.competition, base.ALL_COMPETITIONS)
                # match_log：行数 == 出场数，按 (week, ordinal) 序，match_id
                # 存在于 matches 表，且逐行与 player_match_stats 原始行一致。
                self.assertEqual(len(profile.match_log), profile.season_totals.appeared, player_id)
                weeks = [row.week_number for row in profile.match_log]
                self.assertEqual(weeks, sorted(weeks), player_id)
                sql_rows = self.sql_player_season_rows(conn, player_id, 1)
                sql_match_ids = {
                    row["match_id"]
                    for row in conn.execute(
                        "SELECT match_id FROM matches WHERE season_id = ?",
                        (self.season_id(conn, 1),),
                    )
                }
                for match_row in profile.match_log:
                    self.assertIn(match_row.match_id, sql_match_ids)
                    self.assertIn(match_row.match_id, sql_rows)
                    sql_team_id, sql_stats = sql_rows[match_row.match_id]
                    self.assertEqual(match_row.team.team_id, sql_team_id)
                    self.assertEqual(stat_line_values(match_row.stats), (1,) + sql_stats)
                    # 主/客归属与 is_home、opponent 一致。
                    other = self._other_team_id(conn, match_row.match_id, sql_team_id)
                    self.assertEqual(match_row.opponent.team_id, other)
                    home_away = conn.execute(
                        "SELECT home_team_id, away_team_id FROM matches WHERE match_id = ?",
                        (match_row.match_id,),
                    ).fetchone()
                    expected_is_home = int(home_away["home_team_id"]) == sql_team_id
                    self.assertEqual(match_row.is_home, expected_is_home)

    @staticmethod
    def _other_team_id(conn, match_id: str, team_id: int) -> int:
        row = conn.execute(
            "SELECT home_team_id, away_team_id FROM matches WHERE match_id = ?",
            (match_id,),
        ).fetchone()
        home_team_id, away_team_id = int(row["home_team_id"]), int(row["away_team_id"])
        self_opponent = away_team_id if home_team_id == team_id else home_team_id
        return self_opponent

    def test_awards_legacy_player_key_converted_to_stable_slug(self) -> None:
        with self.open_conn() as conn:
            top20 = conn.execute(
                """
                SELECT player_key, rank, score FROM awards
                WHERE award_type = 'top20' AND season_id = ? AND rank IS NOT NULL
                ORDER BY rank LIMIT 1
                """,
                (self.season_id(conn, 1),),
            ).fetchone()
            self.assertIsNotNone(top20)
            raw_key = top20["player_key"]
            self.assertTrue(raw_key.startswith("real::"), f"legacy 键应为 real::<显示名>：{raw_key}")
            display_name = raw_key[len("real::"):]
            stable_id = base.canonical_player_id_for_name(display_name)
            self.assertNotEqual(stable_id, raw_key, "legacy 键（显示名）与稳定 slug ID 应不同")
            profile = get_player_season_profile(conn, stable_id, 1)
            self.assertEqual(profile.identity.player_id, stable_id)
            self.assertEqual(profile.identity.display_name, display_name)
            self.assertIsNotNone(profile.awards.top20)
            self.assertEqual(profile.awards.top20.rank, int(top20["rank"]))
            self.assertEqual(profile.awards.top20.score, float(top20["score"]))

            scorer = conn.execute(
                """
                SELECT player_key, competition, award_type, score FROM awards
                WHERE award_type = 'top_scorer' AND season_id = ? AND competition IS NOT NULL
                ORDER BY competition LIMIT 1
                """,
                (self.season_id(conn, 1),),
            ).fetchone()
            self.assertIsNotNone(scorer)
            scorer_id = base.canonical_player_id_for_name(scorer["player_key"][len("real::"):])
            scorer_profile = get_player_season_profile(conn, scorer_id, 1)
            matching = [
                award
                for award in scorer_profile.awards.competitions
                if award.award_type == "top_scorer" and award.competition == scorer["competition"]
            ]
            self.assertEqual(len(matching), 1)
            expected_score = None if scorer["score"] is None else float(scorer["score"])
            self.assertEqual(matching[0].score, expected_score)

    def test_trend_contains_winter_and_final_settlements(self) -> None:
        with self.open_conn() as conn:
            final_row = conn.execute(
                """
                SELECT player_key, season_rating, market_value FROM player_settlements
                WHERE season_id = ? AND stage = 'final'
                ORDER BY player_key LIMIT 1
                """,
                (self.season_id(conn, 1),),
            ).fetchone()
            self.assertIsNotNone(final_row)
            player_id = base.canonical_player_id_for_name(final_row["player_key"])
            profile = get_player_season_profile(conn, player_id, 1)
            stages = {(point.season_number, point.stage) for point in profile.trend}
            self.assertIn((1, "winter"), stages)
            self.assertIn((1, "final"), stages)
            winter = next(p for p in profile.trend if (p.season_number, p.stage) == (1, "winter"))
            final = next(p for p in profile.trend if (p.season_number, p.stage) == (1, "final"))
            self.assertEqual(winter.week_number, 24)
            self.assertEqual(final.week_number, 49)
            self.assertEqual(final.rating, float(final_row["season_rating"]))
            self.assertEqual(final.market_value, float(final_row["market_value"]))
            # 排序：(season, stage) 且 winter 在 final 之前。
            keys = [(p.season_number, 0 if p.stage == "winter" else 1) for p in profile.trend]
            self.assertEqual(keys, sorted(keys))
            # 目录行身价 = 该赛季最近一次（final）结算身价。
            directory_row = next(row for row in self._directory(conn) if row.player_id == player_id)
            self.assertEqual(directory_row.market_value, float(final_row["market_value"]))

    def test_default_player_has_no_fabricated_settlements(self) -> None:
        with self.open_conn() as conn:
            default_row = conn.execute(
                """
                SELECT p.team_id AS team_id, p.slot_number AS slot_number, p.position AS position
                FROM players AS p
                WHERE p.is_real = 0 AND EXISTS (
                    SELECT 1 FROM player_match_stats pms
                    JOIN matches m ON m.match_id = pms.match_id
                    WHERE pms.player_id = p.player_id AND pms.appeared = 1 AND m.season_id = ?
                )
                ORDER BY p.team_id, p.slot_number, p.position LIMIT 1
                """,
                (self.season_id(conn, 1),),
            ).fetchone()
            self.assertIsNotNone(default_row)
            default_id = base.default_player_id(int(default_row["team_id"]), int(default_row["slot_number"]))
            profile = get_player_season_profile(conn, default_id, 1)
            self.assertFalse(profile.identity.is_real)
            self.assertEqual(profile.identity.player_id, default_id)
            self.assertEqual(profile.trend, [], "默认球员不应有任何结算轨迹点")
            self.assertIsNone(profile.awards.top20)
            self.assertEqual(profile.awards.competitions, [])
            self.assertTrue(profile.season_totals.appeared > 0)
            # 目录行不伪造身价。
            directory_row = next(row for row in self._directory(conn) if row.player_id == default_id)
            self.assertIsNone(directory_row.market_value)
            self.assertFalse(directory_row.is_real)

    def test_directory_filters(self) -> None:
        with self.open_conn() as conn:
            all_rows = self._directory(conn)
            real_row = next(row for row in all_rows if row.is_real)

            # search：完整显示名精确命中一人；大小写不敏感。
            by_name = list_players(conn, 1, search=real_row.display_name)
            self.assertTrue(by_name)
            self.assertTrue(all(real_row.display_name.lower() in row.display_name.lower() for row in by_name))
            self.assertIn(real_row.player_id, {row.player_id for row in by_name})
            self.assertEqual(
                {row.player_id for row in by_name},
                {row.player_id for row in list_players(conn, 1, search=real_row.display_name.lower())},
            )
            self.assertEqual(list_players(conn, 1, search="绝不存在的球员名字"), [])

            # position：只返回该位置球员。
            goalkeepers = list_players(conn, 1, position="GK")
            self.assertTrue(goalkeepers)
            self.assertTrue(all(row.position == "GK" for row in goalkeepers))
            sql_gk = conn.execute(
                """
                SELECT COUNT(*) AS n FROM players p
                WHERE p.position = 'GK' AND EXISTS (
                    SELECT 1 FROM player_match_stats pms
                    JOIN matches m ON m.match_id = pms.match_id
                    WHERE pms.player_id = p.player_id AND pms.appeared = 1 AND m.season_id = ?
                )
                """,
                (self.season_id(conn, 1),),
            ).fetchone()["n"]
            self.assertEqual(len(goalkeepers), sql_gk)

            # team_id：只返回所属球队匹配的球员，且都能在未过滤目录中找到。
            team_id = real_row.team.team_id
            by_team = list_players(conn, 1, team_id=team_id)
            self.assertTrue(by_team)
            self.assertTrue(all(row.team.team_id == team_id for row in by_team))
            self.assertIn(real_row.player_id, {row.player_id for row in by_team})
            self.assertLess(len(by_team), len(all_rows))

            # competition：只返回在该赛事出场过的球员，统计只计该赛事。
            playoff_rows = list_players(conn, 1, competition="升级附加赛")
            # SQL 侧按稳定 ID 规则换算（默认球员 DB ID → default:<team>:<slot>）。
            sql_stable_ids = set()
            for row in conn.execute(
                """
                SELECT DISTINCT p.player_id AS player_id, p.team_id AS team_id,
                       p.slot_number AS slot_number, p.is_real AS is_real
                FROM player_match_stats AS pms
                JOIN matches AS m ON m.match_id = pms.match_id
                JOIN players AS p ON p.player_id = pms.player_id
                WHERE m.season_id = ? AND m.competition = '升级附加赛' AND pms.appeared = 1
                """,
                (self.season_id(conn, 1),),
            ):
                if row["is_real"]:
                    sql_stable_ids.add(row["player_id"])
                else:
                    sql_stable_ids.add(base.default_player_id(int(row["team_id"]), int(row["slot_number"])))
            self.assertTrue(playoff_rows)
            self.assertTrue(all(row.appeared > 0 for row in playoff_rows))
            self.assertEqual({row.player_id for row in playoff_rows}, sql_stable_ids)
            # 附加赛出场数应不超过总出场数（过滤口径生效）。
            for row in playoff_rows:
                unfiltered = next(item for item in all_rows if item.player_id == row.player_id)
                self.assertLessEqual(row.appeared, unfiltered.appeared)
            # 第 1 赛季没有杯赛。
            self.assertEqual(list_players(conn, 1, competition="优胜者杯"), [])

    def test_unknown_player_or_season_raise_key_error(self) -> None:
        with self.open_conn() as conn:
            with self.assertRaises(KeyError):
                get_player_season_profile(conn, "real::no-such-player", 1)
            with self.assertRaises(KeyError):
                get_player_career(conn, "default:999:9")
            real_id = next(row.player_id for row in self._directory(conn) if row.is_real)
            with self.assertRaises(KeyError):
                get_player_season_profile(conn, real_id, 99)


class PlayerTransferQueryTest(_ReadOnlySaveCase):
    """第 2 赛季推进 30 周（含冬窗转会）后的只读查询。"""

    SAVE_NAME = TRANSFER_SAVE

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        support.create_save(cls.SAVE_NAME)
        support.run_season(cls.SAVE_NAME)
        # 开启第 2 赛季并推进 30 周：冬窗（25-27 周）转会已发生，
        # 第 29-30 周比赛让转会球员为新东家出场。
        support.create_save(cls.SAVE_NAME)
        support.run_weeks(cls.SAVE_NAME, 30)

    @classmethod
    def _transfer_candidate(cls, conn):
        return conn.execute(
            """
            SELECT DISTINCT p.player_id AS player_id, p.team_id AS current_team,
                   pms.team_id AS old_team
            FROM players p
            JOIN player_match_stats pms ON pms.player_id = p.player_id
            JOIN matches m ON m.match_id = pms.match_id
            JOIN seasons s ON s.season_id = m.season_id
            WHERE s.season_number = 2 AND p.is_real = 1 AND pms.team_id != p.team_id
              AND EXISTS (
                    SELECT 1 FROM player_match_stats p2
                    JOIN matches m2 ON m2.match_id = p2.match_id
                    JOIN seasons s2 ON s2.season_id = m2.season_id
                    WHERE p2.player_id = p.player_id AND s2.season_number = 2
                      AND p2.team_id = p.team_id
              )
            ORDER BY p.player_id LIMIT 1
            """
        ).fetchone()

    def test_transfer_player_has_two_team_segments(self) -> None:
        with self.open_conn() as conn:
            candidate = self._transfer_candidate(conn)
            self.assertIsNotNone(candidate, "种子场景应产生赛季中转会的真实球员")
            player_id = candidate["player_id"]
            old_team_id = int(candidate["old_team"])
            new_team_id = int(candidate["current_team"])

            profile = get_player_season_profile(conn, player_id, 2)
            # 赛季总计 == 两段（或多段）之和。
            split_sum = [0] * len(COUNT_FIELDS)
            for split in profile.competition_splits:
                for index, value in enumerate(stat_line_values(split.stats)):
                    split_sum[index] += value
            self.assertEqual(tuple(split_sum), stat_line_values(profile.season_totals))
            # 同一联赛赛事出现两个球队分段（旧队与新队）。
            league_split_teams = [
                split.team.team_id
                for split in profile.competition_splits
                if split.competition in base.LEAGUE_COMPETITIONS
            ]
            self.assertIn(old_team_id, league_split_teams)
            self.assertIn(new_team_id, league_split_teams)
            self.assertEqual(sorted(set(league_split_teams)), sorted([old_team_id, new_team_id]))
            # season_teams 按首次出场排序：旧队在前、新队在后。
            self.assertEqual([team.team_id for team in profile.season_teams], [old_team_id, new_team_id])
            self.assertEqual(profile.current_team.team_id, new_team_id)

            # match_log：历史比赛按比赛当时球队归属（逐行对照 pms 原始行）。
            sql_rows = self.sql_player_season_rows(conn, player_id, 2)
            self.assertEqual(len(profile.match_log), len(sql_rows))
            for match_row in profile.match_log:
                sql_team_id, sql_stats = sql_rows[match_row.match_id]
                self.assertEqual(match_row.team.team_id, sql_team_id)
                self.assertEqual(stat_line_values(match_row.stats), (1,) + sql_stats)
                if match_row.week_number <= 24:
                    self.assertEqual(match_row.team.team_id, old_team_id)
                elif match_row.week_number >= 29:
                    self.assertEqual(match_row.team.team_id, new_team_id)

            # 目录行：所属球队取出场最多的队（旧队），新队进入 additional_teams。
            directory_rows = list_players(conn, 2)
            row = next(item for item in directory_rows if item.player_id == player_id)
            self.assertEqual(row.team.team_id, old_team_id)
            self.assertIn(new_team_id, [team.team_id for team in row.additional_teams])
            self.assertEqual(row.appeared, profile.season_totals.appeared)
            # active 赛季目录包含完整注册阵容。
            roster_size = conn.execute("SELECT COUNT(*) AS n FROM players").fetchone()["n"]
            self.assertEqual(len(directory_rows), roster_size)

    def test_career_aggregates_all_seasons(self) -> None:
        with self.open_conn() as conn:
            candidate = self._transfer_candidate(conn)
            player_id = candidate["player_id"]
            career = get_player_career(conn, player_id)
            self.assertEqual([season.season_number for season in career.seasons], [1, 2])

            profile_one = get_player_season_profile(conn, player_id, 1)
            profile_two = get_player_season_profile(conn, player_id, 2)
            self.assertEqual(stat_line_values(career.seasons[0].totals), stat_line_values(profile_one.season_totals))
            self.assertEqual(stat_line_values(career.seasons[1].totals), stat_line_values(profile_two.season_totals))

            totals = [0] * len(COUNT_FIELDS)
            for season in career.seasons:
                for index, value in enumerate(stat_line_values(season.totals)):
                    totals[index] += value
            self.assertEqual(tuple(totals), stat_line_values(career.career_totals))
            self.assertEqual(
                career.career_totals.appeared,
                self.sql_season_totals(conn, player_id, 1)[0] + self.sql_season_totals(conn, player_id, 2)[0],
            )

            # 赛季末评分/身价来自 final 结算，与趋势一致。
            trend_final_one = next(
                point for point in profile_one.trend if (point.season_number, point.stage) == (1, "final")
            )
            self.assertEqual(career.seasons[0].season_rating, trend_final_one.rating)
            self.assertEqual(career.seasons[0].market_value, trend_final_one.market_value)

            # Top20 第 1 名球员的生涯摘要包含奖项标签。
            top1 = conn.execute(
                """
                SELECT player_key FROM awards
                WHERE award_type = 'top20' AND season_id = ? AND rank = 1
                """,
                (self.season_id(conn, 1),),
            ).fetchone()
            self.assertIsNotNone(top1)
            top1_id = base.canonical_player_id_for_name(top1["player_key"][len("real::"):])
            top1_career = get_player_career(conn, top1_id)
            self.assertIn("Top20 第 1 名", top1_career.seasons[0].award_labels)

    def test_default_player_career_has_no_fabricated_ratings(self) -> None:
        with self.open_conn() as conn:
            default_row = conn.execute(
                """
                SELECT p.team_id AS team_id, p.slot_number AS slot_number
                FROM players AS p
                WHERE p.is_real = 0 AND EXISTS (
                    SELECT 1 FROM player_match_stats pms
                    JOIN matches m ON m.match_id = pms.match_id
                    WHERE pms.player_id = p.player_id AND pms.appeared = 1
                )
                ORDER BY p.team_id, p.slot_number, p.position LIMIT 1
                """
            ).fetchone()
            self.assertIsNotNone(default_row)
            default_id = base.default_player_id(int(default_row["team_id"]), int(default_row["slot_number"]))
            career = get_player_career(conn, default_id)
            self.assertTrue(career.seasons)
            for season in career.seasons:
                self.assertIsNone(season.season_rating, "默认球员不应有结算评分")
                self.assertIsNone(season.market_value, "默认球员不应有结算身价")
                self.assertEqual(season.award_labels, [])
            self.assertGreater(career.career_totals.appeared, 0)


if __name__ == "__main__":
    unittest.main()
