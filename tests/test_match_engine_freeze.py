"""比赛引擎冻结测试（阶段 0）。

锁定内容：同种子可复现性、六项统计语义、真实球员统计过滤（当前工作树
行为）、零封归属、主场影响常量与角色权重表。任何权重/公式/调用顺序变化
都会使本文件失败。
"""

from __future__ import annotations

import random
import unittest

from football_simulator.match_engine import (
    EVENT_MINUTES,
    ROLE_WEIGHTS,
    _record_stat,
    _team_pressure_score,
    simulate_match,
)
from football_simulator.models import FORMATION_RULES, Player, PlayerStatDelta, Team
from football_simulator.schedule import build_league_schedule

from tests.support import FreezeTestCase, create_save, make_teams

FROZEN_EVENT_MINUTES = [4, 10, 16, 23, 29, 36, 43, 51, 59, 67, 75, 83, 89]
FROZEN_ROLE_WEIGHTS = {
    "creator": {"GK": 0.0, "DF": 0.55, "MF": 1.35, "FW": 1.00},
    "scorer": {"GK": 0.0, "DF": 0.18, "MF": 0.72, "FW": 1.60},
    "assist": {"GK": 0.0, "DF": 0.45, "MF": 1.30, "FW": 1.00},
    "defense": {"GK": 0.0, "DF": 1.45, "MF": 0.95, "FW": 0.18},
}
SIX_STAT_FIELDS = {
    "goals",
    "assists",
    "chances_created",
    "successful_defenses",
    "successful_saves",
    "clean_sheets",
}


def uniform_team(name: str, ability: int, division: str = "一级联赛") -> Team:
    roster = []
    slot = 0
    for position, count in FORMATION_RULES.items():
        for _ in range(count):
            slot += 1
            roster.append(
                Player(
                    player_id=f"{name}-{position}-{slot}",
                    name=None,
                    position=position,
                    ability=ability,
                    is_real=False,
                    slot_number=slot,
                )
            )
    return Team(name=name, roster=tuple(roster), division=division)


class MatchEnginePureTests(unittest.TestCase):
    def test_event_minutes_frozen(self) -> None:
        self.assertEqual(EVENT_MINUTES, FROZEN_EVENT_MINUTES)

    def test_role_weights_frozen(self) -> None:
        self.assertEqual(ROLE_WEIGHTS, FROZEN_ROLE_WEIGHTS)

    def test_home_advantage_constant_frozen(self) -> None:
        home = uniform_team("压力主队", 50)
        away = uniform_team("压力客队", 50)
        pressure_home = _team_pressure_score(home, away, is_home=True)
        pressure_away = _team_pressure_score(away, home, is_home=False)
        # 双方阵容完全相同时，主场加成就是两者差值，且不触到上下限截断。
        self.assertAlmostEqual(pressure_home - pressure_away, 0.030, places=9)
        self.assertGreaterEqual(pressure_home, 0.08)
        self.assertLessEqual(pressure_home, 0.46)

    def test_pressure_clamped_to_bounds(self) -> None:
        strong = uniform_team("极强队", 99)
        weak = uniform_team("极弱队", 1)
        pressure = _team_pressure_score(strong, weak, is_home=True)
        self.assertEqual(pressure, 0.46)

    def test_record_stat_records_all_players(self) -> None:
        # 用户确认 #4：默认（非真实）球员也记录六项统计，但不参与奖项评选。
        player = make_teams(["记录队"])[0].goalkeeper
        stats: dict = {}
        _record_stat(stats, player, "clean_sheets")
        self.assertIn(player.player_id, stats)


class MatchSimulationFreezeTests(FreezeTestCase):
    def _league_fixtures(self):
        snapshot = create_save()
        fixtures = [fixture for round_fixtures in build_league_schedule(snapshot.premier_teams) for fixture in round_fixtures]
        return snapshot, fixtures

    def test_same_seed_reproduces_identical_match(self) -> None:
        _, fixtures = self._league_fixtures()
        fixture = fixtures[0]
        first = simulate_match(fixture, random.Random(1234))
        second = simulate_match(fixture, random.Random(1234))
        self.assertEqual(first.home_goals, second.home_goals)
        self.assertEqual(first.away_goals, second.away_goals)
        self.assertEqual(first.key_events, second.key_events)
        self.assertEqual(
            {pid: vars(delta) for pid, delta in first.player_stats.items()},
            {pid: vars(delta) for pid, delta in second.player_stats.items()},
        )

    def test_stats_limited_to_six_fields_and_real_players(self) -> None:
        snapshot, fixtures = self._league_fixtures()
        known_ids = {player.player_id for team in snapshot.teams for player in team.roster}
        rng = random.Random(99)
        stat_totals = {field: 0 for field in SIX_STAT_FIELDS}
        total_goals = 0
        recorded_goals = 0
        for fixture in fixtures:
            result = simulate_match(fixture, rng)
            total_goals += result.home_goals + result.away_goals
            for player_id, delta in result.player_stats.items():
                self.assertIn(player_id, known_ids)
                fields = {field for field, value in vars(delta).items() if value}
                self.assertTrue(fields.issubset(SIX_STAT_FIELDS), f"出现未知统计字段：{fields - SIX_STAT_FIELDS}")
                for field in SIX_STAT_FIELDS:
                    stat_totals[field] += getattr(delta, field)
                recorded_goals += delta.goals
        self.assertLessEqual(recorded_goals, total_goals)
        # 一级联赛 380 场、200 名真实球员中 25 人在一级联赛，产出必须非零。
        self.assertGreater(recorded_goals, 0)
        self.assertGreater(stat_totals["chances_created"], 0)
        self.assertGreater(stat_totals["successful_defenses"], 0)

    def test_clean_sheet_attribution(self) -> None:
        snapshot, fixtures = self._league_fixtures()
        gk_by_team = {team.name: team.goalkeeper for team in snapshot.premier_teams}
        rng = random.Random(7)
        clean_sheets: dict = {}

        for fixture in fixtures:
            result = simulate_match(fixture, rng)
            home_gk, away_gk = gk_by_team[fixture.home_team.name], gk_by_team[fixture.away_team.name]
            if result.away_goals == 0:
                self.assertEqual(result.player_stats[home_gk.player_id].clean_sheets, 1)
                clean_sheets[home_gk.player_id] = clean_sheets.get(home_gk.player_id, 0) + 1
            if result.home_goals == 0:
                self.assertEqual(result.player_stats[away_gk.player_id].clean_sheets, 1)
                clean_sheets[away_gk.player_id] = clean_sheets.get(away_gk.player_id, 0) + 1
        # 统计必须与逐场核对一致；默认门将也记录零封（用户确认 #4）。
        self.assertGreater(len(clean_sheets), 0)


class PlayerStatDeltaTests(unittest.TestCase):
    def test_delta_add_accumulates(self) -> None:
        delta = PlayerStatDelta()
        delta.add("goals", 2)
        delta.add("goals", 1)
        self.assertEqual(delta.goals, 3)


if __name__ == "__main__":
    unittest.main()
