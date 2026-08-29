"""赛程与赛历冻结测试（阶段 0）。

锁定内容：轮次结构、主客场分布、52 周日历的 kind/周次/杯赛事件映射、
结算周与休赛周常量。这些属于玩法规则，任何改动都必须先走提案流程。
"""

from __future__ import annotations

import unittest

from football_simulator.data import _build_default_roster
from football_simulator.models import Team
from football_simulator.schedule import (
    CUP_EVENT_LABELS,
    PROMOTION_PLAYOFF_WEEKS,
    SUMMER_BREAK_WEEKS,
    TOTAL_WEEKS,
    WEEK_CUP_EVENTS,
    WINTER_BREAK_WEEKS,
    build_league_schedule,
    build_week_calendar,
)
from football_simulator.state import FINAL_SETTLEMENT_WEEK, WINTER_SETTLEMENT_WEEK

from tests.support import make_teams

TEAM_NAMES = [f"测试队{i:02d}" for i in range(1, 21)]

# 冻结基线：以下字面值与当前实现一致，修改任何一项都等于修改玩法规则。
FROZEN_EVENT_MINUTES = [4, 10, 16, 23, 29, 36, 43, 51, 59, 67, 75, 83, 89]
FROZEN_WEEK_CUP_EVENTS = {
    3: ("winners_cup_group_1",),
    5: ("challenge_cup_r32",),
    7: ("winners_cup_group_2",),
    10: ("winners_cup_group_3",),
    11: ("challenge_cup_r16",),
    14: ("winners_cup_group_4",),
    17: ("challenge_cup_quarterfinal",),
    18: ("winners_cup_group_5",),
    22: ("winners_cup_group_6",),
    23: ("challenge_cup_semifinal",),
    24: ("super_cup_semifinal",),
    28: ("super_cup_final",),
    30: ("winners_cup_quarterfinal_leg_1",),
    32: ("winners_cup_quarterfinal_leg_2",),
    34: ("winners_cup_semifinal_leg_1",),
    36: ("winners_cup_semifinal_leg_2",),
    43: ("challenge_cup_final",),
    44: ("winners_cup_final_leg_1",),
    45: ("winners_cup_final_leg_2",),
}
FROZEN_PROMOTION_PLAYOFF_WEEKS = {
    46: ("promotion_playoff_semi_leg_1", "升级附加赛半决赛首回合"),
    47: ("promotion_playoff_semi_leg_2", "升级附加赛半决赛次回合"),
    48: ("promotion_playoff_final_leg_1", "升级附加赛决赛首回合"),
    49: ("promotion_playoff_final_leg_2", "升级附加赛决赛次回合"),
}


class LeagueScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.teams = make_teams(TEAM_NAMES)

    def test_round_count_and_match_count(self) -> None:
        rounds = build_league_schedule(self.teams)
        self.assertEqual(len(rounds), 38)
        for index, fixtures in enumerate(rounds, start=1):
            self.assertEqual(len(fixtures), 10, f"第 {index} 轮场次异常")
            for fixture in fixtures:
                self.assertEqual(fixture.round_number, index)
                self.assertEqual(fixture.competition, "一级联赛")

    def test_each_pair_twice_home_and_away(self) -> None:
        rounds = build_league_schedule(self.teams)
        pairings: dict = {}
        home_counts: dict = {}
        for fixtures in rounds:
            for fixture in fixtures:
                key = tuple(sorted((fixture.home_team.name, fixture.away_team.name)))
                self.assertNotEqual(fixture.home_team.name, fixture.away_team.name)
                pairings[key] = pairings.get(key, 0) + 1
                home_counts[fixture.home_team.name] = home_counts.get(fixture.home_team.name, 0) + 1
        self.assertEqual(sum(pairings.values()), 380)
        self.assertTrue(all(count == 2 for count in pairings.values()))
        self.assertEqual(len(pairings), 190)
        for team in TEAM_NAMES:
            self.assertEqual(home_counts.get(team, 0), 19, f"{team} 主场场次异常")

    def test_second_half_mirrors_first_half(self) -> None:
        rounds = build_league_schedule(self.teams)
        for first_index in range(19):
            first = rounds[first_index]
            mirror = rounds[first_index + 19]
            first_pairs = {(f.home_team.name, f.away_team.name) for f in first}
            mirror_pairs = {(f.away_team.name, f.home_team.name) for f in mirror}
            self.assertEqual(first_pairs, mirror_pairs, f"第 {first_index + 20} 轮未镜像第 {first_index + 1} 轮")

    def test_even_team_requirement(self) -> None:
        with self.assertRaises(ValueError):
            build_league_schedule(make_teams(TEAM_NAMES[:9]))


class WeekCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.teams = make_teams(TEAM_NAMES)
        self.weeks = build_week_calendar(build_league_schedule(self.teams))
        self.week_by_number = {week.week_number: week for week in self.weeks}

    def test_total_weeks(self) -> None:
        self.assertEqual(len(self.weeks), TOTAL_WEEKS)
        self.assertEqual([week.week_number for week in self.weeks], list(range(1, 53)))

    def test_break_weeks(self) -> None:
        for week_number in WINTER_BREAK_WEEKS:
            week = self.week_by_number[week_number]
            self.assertEqual(week.kind, "winter_break")
            self.assertEqual(week.label, "冬窗休赛期")
        for week_number in SUMMER_BREAK_WEEKS:
            week = self.week_by_number[week_number]
            self.assertEqual(week.kind, "summer_break")
            self.assertEqual(week.label, "夏窗休赛期")

    def test_promotion_playoff_weeks(self) -> None:
        for week_number, (stage_key, stage_label) in PROMOTION_PLAYOFF_WEEKS.items():
            week = self.week_by_number[week_number]
            self.assertEqual(week.kind, "promotion_playoff")
            self.assertEqual(week.label, stage_label)
            self.assertEqual(week.promotion_playoff_stage, stage_key)

    def test_league_round_mapping(self) -> None:
        # 第 1-24 周对应第 1-24 轮；夏窗后第 29-42 周对应第 25-38 轮。
        for week_number in range(1, 25):
            week = self.week_by_number[week_number]
            self.assertEqual(week.kind, "league_week")
            self.assertEqual(week.premier_round_numbers, (week_number,))
            self.assertEqual(week.second_round_numbers, (week_number,))
        for week_number in range(29, 43):
            week = self.week_by_number[week_number]
            self.assertEqual(week.kind, "league_week")
            self.assertEqual(week.premier_round_numbers, (week_number - 4,))
            self.assertEqual(week.second_round_numbers, (week_number - 4,))

    def test_cup_events_attached_to_weeks(self) -> None:
        for week_number, events in WEEK_CUP_EVENTS.items():
            week = self.week_by_number[week_number]
            self.assertEqual(week.cup_events, events)
            if week.kind == "league_week":
                self.assertTrue(week.label.startswith("联赛周"))

    def test_no_open_weeks_in_current_calendar(self) -> None:
        # 当前 52 周全部被联赛/杯赛/附加赛/休赛覆盖，不存在无比赛周。
        open_weeks = [week.week_number for week in self.weeks if week.kind == "open_week"]
        self.assertEqual(open_weeks, [])

    def test_settlement_weeks_constant(self) -> None:
        self.assertEqual(WINTER_SETTLEMENT_WEEK, 24)
        self.assertEqual(FINAL_SETTLEMENT_WEEK, 49)


class FrozenConstantTablesTests(unittest.TestCase):
    def test_week_cup_events_table(self) -> None:
        self.assertEqual(WEEK_CUP_EVENTS, FROZEN_WEEK_CUP_EVENTS)

    def test_promotion_playoff_weeks_table(self) -> None:
        self.assertEqual(PROMOTION_PLAYOFF_WEEKS, FROZEN_PROMOTION_PLAYOFF_WEEKS)

    def test_cup_event_labels_cover_all_events(self) -> None:
        for events in WEEK_CUP_EVENTS.values():
            for event_key in events:
                self.assertIn(event_key, CUP_EVENT_LABELS)


if __name__ == "__main__":
    unittest.main()
