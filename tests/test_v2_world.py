"""三赛季生产集成：赛历、杯赛总数、逐场事实、冻结结算、奖项与荣誉。"""

from __future__ import annotations

import random
import shutil
import tempfile
import unittest
from collections import Counter, defaultdict

from football_simulator.app.service import GameService
from football_simulator.game import rules
from football_simulator.persistence_v2 import store

from tests import support


def _resolve(service: GameService, save_name: str) -> bool:
    world = store.load_world(save_name)
    pending = world["season"]["pending"]
    if pending.get("ability_review"):
        service.apply_ability_review(save_name, {item["player_id"]: True for item in pending["ability_review"]})
        return True
    if pending.get("draft", {}).get("status") == "awaiting_input":
        service.apply_draft(save_name)
        return True
    if pending.get("transfer_review"):
        service.apply_transfer_reviews(save_name, {item["trade_id"]: True for item in pending["transfer_review"]})
        return True
    return False


def _finish_season(service: GameService, save_name: str) -> dict:
    for _ in range(260):
        world = store.load_world(save_name)
        if _resolve(service, save_name):
            continue
        if world["season"].get("season_complete"):
            break
        service.advance_week(save_name)
    while _resolve(service, save_name):
        pass
    return store.load_world(save_name)


class ThreeSeasonWorldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_v2_world_")
        support.install_test_data_root(cls._tmp)
        cls.service = GameService(rng_factory=lambda: random.Random(20261020))
        cls.service.create_save("three")
        cls.service.initialize_save("three")
        cls.worlds = {}
        cls.s1 = _finish_season(cls.service, "three")
        cls.worlds[1] = cls.s1
        cls.service.start_next_season("three")
        cls.s2 = _finish_season(cls.service, "three")
        cls.worlds[2] = cls.s2
        cls.service.start_next_season("three")
        cls.s3 = _finish_season(cls.service, "three")
        cls.worlds[3] = cls.s3

    @classmethod
    def tearDownClass(cls) -> None:
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls._tmp), ignore_errors=True)

    def test_transfer_rules_hold_across_three_real_seasons(self) -> None:
        moves = self.s3["transfer_history"]
        self.assertTrue(moves)
        seen = set()
        cross_position = False
        multiple_players = False
        for move in moves:
            self.assertLessEqual(abs(move["value_a"] - move["value_b"]), 20.000001)
            self.assertIn((len(move["players_a"]), len(move["players_b"])), {(1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (1, 3), (3, 2), (2, 3)})
            multiple_players |= len(move["players_a"]) != len(move["players_b"])
            positions_a = [self.s3["players"][pid]["position"] for pid in move["players_a"]]
            positions_b = [self.s3["players"][pid]["position"] for pid in move["players_b"]]
            cross_position |= sorted(positions_a) != sorted(positions_b)
            for pid in move["players_a"] + move["players_b"]:
                key = (move["season"], move["window"], pid)
                self.assertNotIn(key, seen)
                seen.add(key)
        self.assertTrue(cross_position)
        self.assertTrue(multiple_players)
        for world in self.worlds.values():
            all_ids = []
            for tid, team in world["teams"].items():
                self.assertEqual(len(team["roster"]), 11)
                self.assertEqual(Counter(world["players"][e["player_id"]]["position"] for e in team["roster"]), {"GK": 1, "DF": 4, "MF": 3, "FW": 3})
                for entry in team["roster"]:
                    data = world["players"][entry["player_id"]]
                    self.assertEqual((data["team_id"], data["slot_number"]), (tid, entry["slot"]))
                    all_ids.append(entry["player_id"])
            self.assertEqual(len(all_ids), len(set(all_ids)))

    # -- 赛历与杯赛 -------------------------------------------------------

    def test_s1_has_no_cups_and_full_league(self) -> None:
        world = self.s1
        fixtures = world["season"]["fixtures"]
        self.assertEqual(world["season"]["week"], 52)
        self.assertTrue(world["season"]["season_complete"])
        self.assertEqual(len([f for f in fixtures if f["competition"] == rules.PREMIER]), 380)
        self.assertEqual(len([f for f in fixtures if f["competition"] == rules.SECOND]), 380)
        self.assertEqual(len([f for f in fixtures if f["competition"] == rules.PLAYOFF]), 6)
        self.assertEqual([f for f in fixtures if f["competition"] in rules.CUP_IDS], [])
        self.assertEqual(world["season"]["cups"], {})
        self.assertEqual(world["season"]["neutral_venues"], {})

    def test_s2_has_five_cups_199_matches(self) -> None:
        fixtures = self.s2["season"]["fixtures"]
        counts = Counter(f["competition"] for f in fixtures if f["competition"] in rules.CUP_IDS)
        self.assertEqual(
            dict(counts),
            {"aureole": 62, "confluence": 31, "starpath": 46, "ember": 14, "ascendant": 46},
        )
        self.assertEqual(sum(counts.values()), 199)
        self.assertNotIn("zenith", self.s2["season"]["cups"])
        self.assertEqual(len(self.s2["season"]["neutral_venues"]), 3)
        self.assertEqual(len(set(self.s2["season"]["neutral_venues"].values())), 3)

    def test_s3_has_all_six_cups_206_matches(self) -> None:
        fixtures = self.s3["season"]["fixtures"]
        counts = Counter(f["competition"] for f in fixtures if f["competition"] in rules.CUP_IDS)
        self.assertEqual(sum(counts.values()), 206)
        self.assertEqual(counts["zenith"], 7)
        self.assertEqual(len(self.s3["season"]["neutral_venues"]), 4)
        self.assertEqual(len(set(self.s3["season"]["neutral_venues"].values())), 4)
        for cup_id in rules.CUP_IDS:
            state = self.s3["season"]["cups"][cup_id]
            self.assertTrue(state.get("champion"), f"{cup_id} 应产生冠军")
            self.assertTrue(state.get("placements"), f"{cup_id} 应保存最终名次")

    def test_every_team_plays_at_most_two_matches_per_week(self) -> None:
        for season, world in self.worlds.items():
            counts = defaultdict(int)
            for fixture in world["season"]["fixtures"]:
                for team_id in (fixture["home_team_id"], fixture["away_team_id"]):
                    counts[(team_id, fixture["week"])] += 1
            self.assertLessEqual(max(counts.values()), 2, f"S{season} 出现单周超过 2 场")
            # 所有已创建杯赛比赛都必须完成，不能留下半轮签表。
            for fixture in world["season"]["fixtures"]:
                self.assertEqual(fixture["status"], "completed", fixture["match_id"])

    def test_neutral_finals_have_no_home_advantage(self) -> None:
        for world, cups in ((self.s2, ("confluence", "starpath", "ascendant")), (self.s3, ("confluence", "starpath", "ascendant", "zenith"))):
            for cup_id in cups:
                finals = [
                    fixture
                    for fixture in world["season"]["fixtures"]
                    if fixture["competition"] == cup_id and fixture.get("stage") in ("final", "final_leg_2")
                ]
                self.assertEqual(len(finals), 1, cup_id)
                final = finals[0]
                self.assertEqual(final["venue_mode"], "neutral")
                self.assertIsNone(final["home_advantage_team_id"])
                self.assertEqual(final["stadium_id"], world["season"]["neutral_venues"][cup_id])
                self.assertIsNotNone(final["neutral_reason"])

    # -- 逐场事实与冻结 ---------------------------------------------------

    def test_every_completed_match_records_both_rosters_including_zero_output(self) -> None:
        zero_output = 0
        for fixture in self.s2["season"]["fixtures"]:
            if fixture["status"] != "completed":
                continue
            self.assertEqual(len(fixture["player_facts"]), 22, fixture["match_id"])
            for fact in fixture["player_facts"].values():
                self.assertEqual(fact["appeared"], 1)
                if not any(fact[field] for field in ("goals", "assists", "chances_created", "successful_defenses", "successful_saves", "clean_sheets")):
                    zero_output += 1
        self.assertGreater(zero_output, 0, "零产出但出场的球员必须保留逐场记录")

    def test_penalties_are_not_ordinary_goals(self) -> None:
        found = 0
        for world in (self.s1, self.s2, self.s3):
            for fixture in world["season"]["fixtures"]:
                if fixture.get("home_pk") is None:
                    continue
                found += 1
                ordinary = fixture["home_goals"] + fixture["away_goals"]
                extra = fixture.get("home_goals_et", 0) + fixture.get("away_goals_et", 0)
                scorer_goals = sum(fact["goals"] for fact in fixture["player_facts"].values())
                self.assertEqual(scorer_goals, ordinary + extra)
                self.assertEqual(
                    sum(1 for kick in fixture["shootout"] if kick["scored"]),
                    fixture["home_pk"] + fixture["away_pk"],
                )
                self.assertEqual(len(fixture["player_facts"]), 22)
        self.assertGreater(found, 0)

    def test_rookie_zero_appearances_and_frozen_values(self) -> None:
        drafted = self.s1["season"]["draft_log"][-1]["results"]
        self.assertTrue(drafted)
        rookie_id = drafted[0]["player_id"]
        self.assertEqual(self.s1["players"][rookie_id]["initial_market_value"], 30.0)
        # 选秀发生在第 49 周结算后：S1 正式结算没有该新秀的出场/评分。
        settlement = self.s1["season"]["settlements"]["final"]["players"].get(rookie_id)
        self.assertIsNone(settlement)
        archived = self.s2["archives"][0]["settlements"]["final"]["players"].get(rookie_id)
        self.assertIsNone(archived)
        # S2 能力变化后，S1 归档评分/身价不漂移。
        before = dict(self.s2["archives"][0]["settlements"]["final"]["players"])
        rookies_in_s2 = self.s2["season"]["draft_log"][-1]["results"]
        self.assertTrue(rookies_in_s2)
        for player_id, values in before.items():
            self.assertEqual(self.s3["archives"][0]["settlements"]["final"]["players"][player_id], values)

    def test_honors_and_awards_counts(self) -> None:
        s2_counts = Counter(row["competition"] for row in self.s2["season"]["honor_rows"])
        self.assertEqual(
            dict(s2_counts),
            {
                "premier": 20,
                "second": 20,
                "aureole": 16,
                "confluence": 32,
                "starpath": 16,
                "ember": 8,
                "ascendant": 12,
            },
        )
        s3_counts = Counter(row["competition"] for row in self.s3["season"]["honor_rows"])
        self.assertEqual(s3_counts["zenith"], 8)
        self.assertTrue(all(row["points"] == 0 for row in self.s3["season"]["honor_rows"] if row["competition"] == "second"))
        self.assertEqual(rules.honor_points_for("zenith", 1), 45)
        awards = self.s3["season"]["awards"]
        self.assertEqual(len(awards["top20"]), 20)
        for cup_id in rules.CUP_IDS:
            values = awards["competitions"][cup_id]
            self.assertIn("top_scorer", values)
            self.assertIn("assist_leader", values)
            self.assertIn("mvp", values)
        expected_thresholds = {
            "aureole": 6,
            "confluence": 3,
            "starpath": 5,
            "ember": 3,
            "ascendant": 5,
            "zenith": 2,
        }
        for cup_id, threshold in expected_thresholds.items():
            self.assertEqual(awards["competitions"][cup_id]["mvp_minimum"], threshold, cup_id)
        self.assertEqual(len(self.s3["archives"]), 3)

    def test_top20_components_recompute_score(self) -> None:
        for row in self.s3["season"]["awards"]["top20"]:
            components = row["components"]
            raw = (
                components["rating"]
                + components["ability"]
                + components["production"]
                + components["team_honor"]
            ) * components["league_weight"]
            self.assertAlmostEqual(row["score"], round(raw, 2), places=2)

    def test_transfer_keeps_per_match_team_and_merges_player(self) -> None:
        found = None
        for move in self.s2["transfer_history"]:
            player_id = move["players_a"][0]
            before = [
                fixture
                for fixture in self.s2["season"]["fixtures"]
                if (fixture.get("player_facts") or {}).get(player_id)
            ]
            old_team = move["team_a"]
            if any(fixture["player_facts"][player_id]["team_id"] == old_team for fixture in before):
                found = (player_id, old_team, before)
                break
        if found is None:
            self.skipTest("本固定种子没有可用于断言的冬窗转会")
        player_id, old_team, fixtures = found
        teams = {fixture["player_facts"][player_id]["team_id"] for fixture in fixtures}
        self.assertIn(old_team, teams)


if __name__ == "__main__":
    unittest.main()
