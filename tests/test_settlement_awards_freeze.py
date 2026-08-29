"""结算、评分、身价与奖项冻结测试（阶段 0）。

锁定内容：冬窗（24 周）/赛季末（49 周）结算缓存、评分与身价只作用于
真实球员、身价下限、Top20 结构，以及球员赛季行与结算缓存的对应关系。
"""

from __future__ import annotations

import unittest

from football_simulator.state import FINAL_SETTLEMENT_WEEK, WINTER_SETTLEMENT_WEEK

from tests.support import (
    FreezeTestCase,
    advance_week,
    create_save,
    load_snapshot,
    load_state_json,
    run_season,
)


class SettlementTests(FreezeTestCase):
    def test_settlement_weeks_constants(self) -> None:
        self.assertEqual(WINTER_SETTLEMENT_WEEK, 24)
        self.assertEqual(FINAL_SETTLEMENT_WEEK, 49)

    def test_settlement_caches_populated_at_week_24_and_49(self) -> None:
        save = "settle"
        create_save(save)
        for _ in range(24):
            advance_week(save)
        state_json = load_state_json(save)
        self.assertEqual(state_json["current_week"], 24)
        winter_cache = state_json["settlement_cache"].get("winter")
        self.assertTrue(winter_cache, "第 24 周后冬窗结算缓存必须已写入")
        self.assertGreater(len(winter_cache), 0)
        self.assertEqual(state_json["settlement_cache"].get("final"), {}, "赛季末结算缓存还不应写入")

        run_season(save)
        state_json = load_state_json(save)
        self.assertIn("final", state_json["settlement_cache"])
        self.assertTrue(state_json["settlement_cache"]["final"])

    def test_player_rows_settlement_semantics(self) -> None:
        save = "settle_rows"
        create_save(save)
        run_season(save)
        snap = load_snapshot(save)
        state_json = load_state_json(save)

        real_rows = [row for row in snap.player_stats if row.player.is_real]
        # 快照 player_stats 只包含真实球员注册行（默认球员无统计行）。
        self.assertTrue(real_rows)
        self.assertTrue(all(row.player.is_real for row in snap.player_stats))

        # 缓存只包含在第 49 周结算口径下拿到评分的球员（一级联赛口径，
        # 赛季 1 无杯赛时次级联赛球员不参与结算）。
        final_cache = state_json["settlement_cache"]["final"]
        winter_cache = state_json["settlement_cache"]["winter"]
        self.assertTrue(final_cache)
        premier_names = {team.name for team in snap.premier_teams}
        # 夏窗（50–52 周）转会的球员在赛季末按新球队的 ≤49 周比赛数现算评分，
        # 与第 49 周按旧队口径写入的缓存不同（现行为）；其余球员必须与缓存一致。
        summer_transferred = {
            player["name"]
            for row in state_json.get("transfer_history", [])
            if row["season_number"] == snap.season_number and row["week_number"] in (50, 51, 52)
            for side in ("team_a_players", "team_b_players")
            for player in row.get(side, [])
            if player.get("name")
        }
        for row in real_rows:
            name = row.player.name
            # 评分与身价必须同时存在或同时缺失。
            self.assertEqual(row.season_rating is None, row.market_value is None)
            if name in final_cache and name not in summer_transferred:
                item = final_cache[name]
                self.assertAlmostEqual(item["season_rating"], row.season_rating, places=2)
                self.assertAlmostEqual(item["market_value"], row.market_value, places=2)
                self.assertGreaterEqual(row.season_rating, 0.0)
                self.assertLessEqual(row.season_rating, 10.0)
                self.assertGreaterEqual(row.market_value, 8.0, "身价公式下限为 8.0")
            elif name in summer_transferred:
                self.assertIsNotNone(row.season_rating)
            elif row.team_name in premier_names:
                # 夏窗才转入一级联赛的球员：按 ≤49 周口径现算评分，
                # 不出现在第 49 周写入的缓存中。
                self.assertIsNotNone(row.season_rating)
                self.assertGreaterEqual(row.season_rating, 0.0)
                self.assertLessEqual(row.season_rating, 10.0)
            else:
                # 从未进入一级联赛结算口径的球员：无评分、无身价。
                self.assertIsNone(row.season_rating)
                self.assertIsNone(row.market_value)
        self.assertTrue(winter_cache, "冬窗结算缓存应包含第 24 周口径球员")

        # 团队身价总和在赛季末已启用。
        for row in snap.team_stats:
            self.assertIsNotNone(row.total_market_value)


class RatingFormulaFreezeTests(FreezeTestCase):
    def test_rating_and_market_value_formula_pinned(self) -> None:
        # 用受控输入直接锁定两个结算公式：任何系数变化都会让本用例失败。
        from football_simulator.models import POSITION_FORWARD, Player
        from football_simulator.state import _calculate_market_value, _calculate_player_rating

        forward = Player(
            player_id="real::test",
            name="测试前锋",
            position=POSITION_FORWARD,
            ability=70,
            is_real=True,
            slot_number=1,
        )
        stats = type(
            "Row",
            (),
            {
                "player": forward,
                "goals": 10,
                "assists": 5,
                "chances_created": 20,
                "successful_defenses": 4,
                "successful_saves": 0,
                "clean_sheets": 0,
            },
        )()
        # ability_bonus = (70-50)/10 = 2.0；每场 38 场口径。
        rating = _calculate_player_rating(stats, 38)
        expected = round(
            4.80
            + 4.60 * (10 / 38)
            + 2.40 * (5 / 38)
            + 0.32 * (20 / 38)
            + 0.12 * (4 / 38)
            + 0.10 * 2.0,
            2,
        )
        self.assertEqual(rating, expected)

        value = _calculate_market_value(forward, rating)
        performance_factor = 0.58 * rating + 0.42 * 7.0
        expected_value = round(
            max(
                8.0,
                (performance_factor ** 2.05) * 1.08
                + (max(0, 70 - 74) ** 1.45) * 0.55
                + (max(0.0, rating - 7.4) ** 2) * 9.0,
            ),
            2,
        )
        self.assertEqual(value, expected_value)

    def test_default_player_has_zero_market_value_in_formula(self) -> None:
        from football_simulator.models import POSITION_FORWARD, Player
        from football_simulator.state import _calculate_market_value

        default_player = Player(
            player_id="team-df-1",
            name=None,
            position=POSITION_FORWARD,
            ability=50,
            is_real=False,
            slot_number=1,
        )
        self.assertEqual(_calculate_market_value(default_player, 8.0), 0.0)


if __name__ == "__main__":
    unittest.main()
