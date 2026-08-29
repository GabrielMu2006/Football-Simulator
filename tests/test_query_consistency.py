"""查询层跨域一致性测试（阶段 2 集成验收）。

校验 C1/C2 两个查询域产出的数据相互一致（实施方案 §10 阶段 2 任务 6
"校验聚合不变量和跨队 tenure"，§12.1 的查询层不变量）：
- 球员赛季各赛事分段之和 == 赛季总计（六项 + 出场）；
- 生涯总计 == 各赛季之和；
- 赛季中转会球员出现多队分段且归属按比赛当时球队；
- 比赛详情与球员比赛日志互相印证；
- 球队积分榜与赛事积分榜一致；
- 首页榜单/赛程与对应域查询一致；
- 历史冠军与积分榜一致。
"""

from __future__ import annotations

import random
import shutil
import tempfile
import unittest
from pathlib import Path

from football_simulator import runtime as sim_runtime
from football_simulator import state as sim_state
from football_simulator.queries import base
from football_simulator.queries.competition_queries import get_competition_profile
from football_simulator.queries.dashboard_queries import get_dashboard
from football_simulator.queries.history_queries import list_season_summaries
from football_simulator.queries.match_queries import get_match_detail, get_match_neighbors, list_matches
from football_simulator.queries.player_queries import get_player_career, get_player_season_profile, list_players
from football_simulator.queries.team_queries import get_team_season_profile, list_teams

from tests.support import TEST_SEED, create_save, run_season, run_weeks, seeded_provider

_SAVE_NAME = "consistency"
_TMP_DIR = Path(tempfile.mkdtemp(prefix="qcons_"))
_FIXTURE_STATE = {"built": False}

STAT_FIELDS = ("goals", "assists", "chances_created", "successful_defenses", "successful_saves", "clean_sheets")


def _build_fixture() -> None:
    if _FIXTURE_STATE["built"]:
        return
    sim_runtime.set_save_root_override(_TMP_DIR)
    sim_state.set_rng_provider(seeded_provider(TEST_SEED))
    try:
        create_save(_SAVE_NAME)
        run_season(_SAVE_NAME)          # 赛季 1 完整（含冬窗/夏窗转会）
        create_save(_SAVE_NAME)         # 进入赛季 2
        run_weeks(_SAVE_NAME, 6)        # 赛季 2 前 6 周
    finally:
        sim_state.set_rng_provider(None)
        sim_runtime.set_save_root_override(None)
    _FIXTURE_STATE["built"] = True


def _release_overrides() -> None:
    sim_state.set_rng_provider(None)
    sim_runtime.set_save_root_override(None)


import atexit

atexit.register(lambda: shutil.rmtree(_TMP_DIR, ignore_errors=True))


class QueryConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _build_fixture()

    def setUp(self) -> None:
        sim_runtime.set_save_root_override(_TMP_DIR)
        sim_state.set_rng_provider(seeded_provider(TEST_SEED))
        self.addCleanup(_release_overrides)
        self._conn_ctx = base.open_read_connection(_SAVE_NAME)
        self.conn = self._conn_ctx.__enter__()
        self.addCleanup(self._conn_ctx.__exit__, None, None, None)

    def _season_profiles(self, season_number: int, sample: int = 6):
        rows = list_players(self.conn, season_number)
        real_rows = [row for row in rows if row.is_real]
        self.assertTrue(real_rows)
        picked = real_rows[:sample]
        return [(row, get_player_season_profile(self.conn, row.player_id, season_number)) for row in picked]

    def test_competition_splits_sum_to_season_totals(self) -> None:
        for row, profile in self._season_profiles(1):
            merged = {field: 0 for field in STAT_FIELDS}
            merged_appeared = 0
            for split in profile.competition_splits:
                merged_appeared += split.stats.appeared
                for field in STAT_FIELDS:
                    merged[field] += getattr(split.stats, field)
            self.assertEqual(
                merged_appeared,
                profile.season_totals.appeared,
                f"{profile.identity.display_name} 出场分段合计不一致",
            )
            for field in STAT_FIELDS:
                self.assertEqual(
                    merged[field],
                    getattr(profile.season_totals, field),
                    f"{profile.identity.display_name} {field} 分段合计不一致",
                )

    def test_transferred_player_has_multi_team_splits(self) -> None:
        rows = list_players(self.conn, 1)
        transferred = [row for row in rows if row.is_real and row.additional_teams]
        self.assertTrue(transferred, "赛季 1 的转会窗口下必须存在赛季中转会的球员")
        row = transferred[0]
        profile = get_player_season_profile(self.conn, row.player_id, 1)
        team_ids = {team.team_id for team in profile.season_teams}
        self.assertGreaterEqual(len(team_ids), 2)
        split_team_ids = {split.team.team_id for split in profile.competition_splits}
        self.assertEqual(split_team_ids, team_ids)
        # 比赛当时归属：该球员 match_log 的 team 必须属于其效力球队集合。
        for match_row in profile.match_log:
            self.assertIn(match_row.team.team_id, team_ids)

    def test_career_totals_equal_sum_of_seasons(self) -> None:
        rows = list_players(self.conn, 2)
        real_rows = [row for row in rows if row.is_real]
        self.assertTrue(real_rows)
        career = get_player_career(self.conn, real_rows[0].player_id)
        self.assertGreaterEqual(len(career.seasons), 2)
        merged = {field: 0 for field in STAT_FIELDS}
        merged_appeared = 0
        for season in career.seasons:
            merged_appeared += season.totals.appeared
            for field in STAT_FIELDS:
                merged[field] += getattr(season.totals, field)
        self.assertEqual(career.career_totals.appeared, merged_appeared)
        for field in STAT_FIELDS:
            self.assertEqual(getattr(career.career_totals, field), merged[field])

    def test_player_match_log_matches_match_detail(self) -> None:
        row, profile = self._season_profiles(1, sample=2)[0]
        self.assertTrue(profile.match_log)
        for match_row in profile.match_log[:6]:
            detail = get_match_detail(self.conn, match_row.match_id)
            self.assertTrue(detail.match.is_completed)
            self.assertEqual(detail.match.home_goals, match_row.home_goals)
            lines = [line for line in detail.player_lines if line.player.player_id == profile.identity.player_id]
            self.assertEqual(len(lines), 1, f"{match_row.match_id} 中该球员应有唯一统计行")
            line = lines[0]
            self.assertEqual(line.team.team_id, match_row.team.team_id)
            self.assertEqual(line.goals, match_row.stats.goals)
            self.assertEqual(line.assists, match_row.stats.assists)
            self.assertEqual(line.chances_created, match_row.stats.chances_created)
            self.assertEqual(line.successful_defenses, match_row.stats.successful_defenses)
            self.assertEqual(line.successful_saves, match_row.stats.successful_saves)
            self.assertEqual(line.clean_sheets, match_row.stats.clean_sheets)

    def test_match_neighbors_walk_the_season(self) -> None:
        matches = list_matches(self.conn, 1, competition="一级联赛", status="completed")
        self.assertGreater(len(matches), 10)
        first_id = matches[0].match_id
        prev_id, next_id = get_match_neighbors(self.conn, first_id, competition="一级联赛")
        self.assertIsNone(prev_id)
        self.assertEqual(next_id, matches[1].match_id)
        mid_id = matches[5].match_id
        prev_id, next_id = get_match_neighbors(self.conn, mid_id, competition="一级联赛")
        self.assertEqual(prev_id, matches[4].match_id)
        self.assertEqual(next_id, matches[6].match_id)

    def test_team_standings_match_competition_standings(self) -> None:
        team_rows = {row.team.team_id: row for row in list_teams(self.conn, 1, division="一级联赛")}
        profile = get_competition_profile(self.conn, "一级联赛", 1)
        self.assertIsNotNone(profile.standings)
        standing_by_id = {row.team_id: row for row in profile.standings}
        self.assertEqual(set(team_rows), set(standing_by_id))
        for team_id, directory_row in team_rows.items():
            standing = standing_by_id[team_id]
            self.assertEqual(standing.points, directory_row.points, f"{directory_row.team.display_name} 积分不一致")
            self.assertEqual(standing.played, directory_row.played)
            self.assertEqual(standing.rank, directory_row.rank)

    def test_team_fixtures_match_match_list(self) -> None:
        team_rows = list_teams(self.conn, 1, division="一级联赛")
        team = team_rows[0]
        profile = get_team_season_profile(self.conn, team.team.team_id, 1)
        direct = list_matches(self.conn, 1, team_id=team.team.team_id, status="completed")
        completed_fixtures = [f for f in profile.fixtures if f.is_completed]
        self.assertEqual(len(completed_fixtures), len(direct))
        direct_ids = {m.match_id for m in direct}
        for fixture in completed_fixtures:
            self.assertIn(fixture.match_id, direct_ids)

    def test_leaderboard_top_scorer_matches_player_profile(self) -> None:
        profile = get_competition_profile(self.conn, "一级联赛", 1)
        self.assertTrue(profile.leaderboards.top_scorers)
        leader = profile.leaderboards.top_scorers[0]
        player_profile = get_player_season_profile(self.conn, leader.player.player_id, 1)
        premier_splits = [s for s in player_profile.competition_splits if s.competition == "一级联赛"]
        merged = sum(split.stats.goals for split in premier_splits)
        self.assertGreater(leader.goals, 0)
        self.assertEqual(leader.goals, merged)

    def test_dashboard_matches_domain_queries(self) -> None:
        snapshot = get_dashboard(self.conn)
        self.assertEqual(snapshot.current_season, 2)
        self.assertEqual(snapshot.current_week, 6)

        all_season2 = list_matches(self.conn, 2)
        upcoming_ids = {m.match_id for m in all_season2 if not m.is_completed}
        for match in snapshot.upcoming_matches:
            self.assertIn(match.match_id, upcoming_ids)

        # 首页射手榜与赛事详情榜单一致。
        premier = get_competition_profile(self.conn, "一级联赛", 2)
        dashboard_premier = [
            l for l in snapshot.league_leaders if l.competition.competition_id == "一级联赛"
        ]
        self.assertTrue(dashboard_premier)
        dash_leader = dashboard_premier[0].top_scorers[0] if dashboard_premier[0].top_scorers else None
        comp_leader = premier.leaderboards.top_scorers[0] if premier.leaderboards.top_scorers else None
        if dash_leader is not None and comp_leader is not None:
            self.assertEqual(dash_leader.player.player_id, comp_leader.player.player_id)
            self.assertEqual(dash_leader.value, comp_leader.goals)

    def test_season_summary_champion_matches_standings(self) -> None:
        summaries = list_season_summaries(self.conn)
        season1 = [s for s in summaries if s.season_number == 1]
        self.assertEqual(len(season1), 1)
        profile = get_competition_profile(self.conn, "一级联赛", 1)
        self.assertIsNotNone(profile.standings)
        self.assertEqual(season1[0].premier_champion, profile.standings[0].team_name)


if __name__ == "__main__":
    unittest.main()
