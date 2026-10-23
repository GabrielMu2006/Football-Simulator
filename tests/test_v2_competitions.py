"""六杯纯规则断言：瑞士配对、组外赛程、双败主场、Legacy 资格。"""

from __future__ import annotations

import random
import unittest

from football_simulator.game import competitions
from football_simulator.game.calendar import standings_from_matches
from football_simulator.game.world import _award_winners


def _fake_state(teams, opponents=None, records=None, seeds=None):
    return {
        "teams": teams,
        "opponents": {team: list(opponents.get(team, [])) if opponents else [] for team in teams},
        "records": records or {team: (0, 0) for team in teams},
        "seeds": seeds or {team: index + 1 for index, team in enumerate(teams)},
    }


class SwissPairingTests(unittest.TestCase):
    def test_six_team_group_uses_frozen_priority_table(self) -> None:
        teams = [f"t{i}" for i in range(6)]
        state = _fake_state(teams)
        pairs, reason = competitions._pair_six(teams, state, 4)
        self.assertIsNone(reason)
        self.assertEqual(
            pairs,
            [("t0", "t5"), ("t1", "t4"), ("t2", "t3")],
        )

    def test_six_team_group_avoids_rematches(self) -> None:
        teams = [f"t{i}" for i in range(6)]
        # 第一优先表会产生 1–6 重赛；应跳到无重赛的下一方案。
        state = _fake_state(teams, opponents={"t0": ["t5"]})
        pairs, reason = competitions._pair_six(teams, state, 4)
        self.assertIsNone(reason)
        self.assertNotIn(("t0", "t5"), pairs)

    def test_high_low_pairing_backtracks(self) -> None:
        teams = [f"t{i}" for i in range(8)]
        state = _fake_state(teams, opponents={"t0": ["t7"], "t1": ["t6"]})
        pairs, reason = competitions._pair_high_low(teams, state, 2)
        self.assertIsNone(reason)
        self.assertEqual(len(pairs), 4)
        self.assertNotIn(("t0", "t7"), pairs)
        self.assertNotIn(("t1", "t6"), pairs)
        self.assertEqual(sorted(team for pair in pairs for team in pair), sorted(teams))


class AscendantScheduleTests(unittest.TestCase):
    @staticmethod
    def tied_cross_group_fixture():
        d_group = [f"D{i}" for i in range(6)]
        n_group = [f"N{i}" for i in range(6)]
        scores = [(1, 1)] * 4 + [(0, 0)] * 2
        matches = []
        for index, pairs in enumerate(competitions._cross_group_schedule(d_group, n_group, random.Random(4))):
            for home, away, _ in pairs:
                team = home if home in d_group else away
                if team == "D0":
                    gf, ga = scores[index]
                elif team == "D1":
                    gf, ga = (2, 0) if index < 2 else (0, 1)
                elif team in ("D2", "D3", "D4"):
                    gf, ga = 1, 0
                else:
                    gf, ga = 0, 1
                hg, ag = (gf, ga) if team == home else (ga, gf)
                matches.append({"home_team_id": home, "away_team_id": away, "home_goals": hg, "away_goals": ag, "status": "completed", "competition": "ascendant", "stage": f"cross_group_{index + 1}"})
        return d_group, n_group, matches

    def test_cross_group_wins_break_tie_before_previous_rank(self) -> None:
        group, _, matches = self.tied_cross_group_fixture()
        rows = standings_from_matches(group, matches, previous_rank={tid: i for i, tid in enumerate(group)}, partial=True)
        a, b = (next(row for row in rows if row["team_id"] == tid) for tid in ("D0", "D1"))
        self.assertEqual([(r["played"], r["points"], r["goal_diff"], r["goals_for"]) for r in (a, b)], [(6, 6, 0, 4)] * 2)
        self.assertEqual((a["wins"], b["wins"]), (0, 2))
        self.assertEqual([row["team_id"] for row in rows][3:5], ["D1", "D0"])

    def test_cross_group_equal_wins_fall_back_to_previous_rank(self) -> None:
        group, _, matches = self.tied_cross_group_fixture()
        rows = standings_from_matches(group, matches, previous_rank={"D2": 8, "D3": 7, "D4": 6}, partial=True)
        self.assertEqual([row["team_id"] for row in rows][:3], ["D4", "D3", "D2"])

    def test_cross_group_schedule_has_36_unique_pairs_and_3_home_each(self) -> None:
        d_group = [f"D{i}" for i in range(6)]
        n_group = [f"N{i}" for i in range(6)]
        for seed in range(20):
            schedule = competitions._cross_group_schedule(d_group, n_group, random.Random(seed))
            self.assertEqual(len(schedule), 6)
            self.assertTrue(all(len(round_pairs) == 6 for round_pairs in schedule))
            pairs = set()
            home = {team: 0 for team in [*d_group, *n_group]}
            away = {team: 0 for team in [*d_group, *n_group]}
            for round_pairs in schedule:
                for home_team, away_team, group in round_pairs:
                    self.assertIn(home_team, d_group + n_group)
                    self.assertIn(away_team, d_group + n_group)
                    key = frozenset((home_team, away_team))
                    self.assertNotIn(key, pairs, "跨组对阵不能重复")
                    pairs.add(key)
                    home[home_team] += 1
                    away[away_team] += 1
                    self.assertTrue(
                        (home_team in d_group and away_team in n_group)
                        or (home_team in n_group and away_team in d_group)
                    )
            self.assertEqual(len(pairs), 36)
            for team in home:
                self.assertEqual(home[team], 3, team)
                self.assertEqual(away[team], 3, team)


class EmberHomeTests(unittest.TestCase):
    def test_unbeaten_team_first_then_later_loss_then_rank(self) -> None:
        rank = {"A": 1, "B": 2, "C": 3, "D": 4}
        state = {"loss_stage": {"A": None, "B": 2, "C": 1, "D": 3}}
        self.assertEqual(competitions._ember_home(state, rank, "A", "B")[0], "A")
        # 同为 1–1：更晚落入败者组的 B 主场。
        self.assertEqual(competitions._ember_home(state, rank, "B", "C")[0], "B")
        # 等价路径：看上季排名。
        state["loss_stage"]["C"] = 2
        self.assertEqual(competitions._ember_home(state, rank, "B", "C")[0], "B")
        state["loss_stage"]["B"] = 2
        state["loss_stage"]["C"] = 2
        self.assertEqual(competitions._ember_home(state, rank, "C", "B")[0], "B")


class ZenithQualificationTests(unittest.TestCase):
    def _world(self, champions, premier, second):
        return {
            "season": {
                "previous": {
                    "cup_champions": champions,
                    "premier_order": premier,
                    "second_order": second,
                }
            }
        }

    def test_multi_cup_winner_counts_once_and_second_champion_qualifies(self) -> None:
        premier = [f"P{i}" for i in range(20)]
        second = [f"S{i}" for i in range(20)]
        world = self._world(
            {
                "aureole": "P0",
                "confluence": "P0",
                "starpath": "P1",
                "ember": "P2",
                "ascendant": "P3",
                "zenith": "P4",
            },
            premier,
            second,
        )
        selected, sources = competitions.zenith_qualification(world)
        self.assertEqual(len(selected), 8)
        self.assertEqual(len(set(selected)), 8)
        self.assertIn("P0", selected)
        self.assertIn("S0", selected)
        self.assertGreaterEqual(len(sources["P0"]), 3)  # 两个杯赛冠军 + 一级联赛冠军
        flattened = [source for team in selected for source in sources.get(team, [])]
        self.assertIn("一级联赛排名递补", flattened)

    def test_first_edition_without_defending_champion_fills_place(self) -> None:
        premier = [f"P{i}" for i in range(20)]
        second = [f"S{i}" for i in range(20)]
        world = self._world(
            {"aureole": "P0", "confluence": "P1", "starpath": "P2", "ember": "P3", "ascendant": "P4"},
            premier,
            second,
        )
        selected, sources = competitions.zenith_qualification(world)
        self.assertEqual(len(selected), 8)
        self.assertNotIn("zenith", " ".join(" ".join(v) for v in sources.values()))
        self.assertIn("P5", selected)


class AwardSharingTests(unittest.TestCase):
    @staticmethod
    def _row(name, goals, assists, appearances, rating=7.0):
        return {
            "player_id": f"p-{name}",
            "label": name,
            "team_id": "t",
            "team_name": "T",
            "goals": goals,
            "assists": assists,
            "appearances": appearances,
            "rating": rating,
        }

    def test_identical_returns_share_award(self) -> None:
        rows = [self._row("A", 5, 2, 6), self._row("B", 5, 2, 6), self._row("C", 5, 1, 6)]
        winners = _award_winners(rows, lambda row: (-row["goals"], -row["assists"], row["appearances"]), value_field="goals")
        self.assertEqual([row["label"] for row in winners], ["A", "B"])

    def test_zero_production_leaves_award_empty(self) -> None:
        rows = [self._row("A", 0, 0, 3), self._row("B", 0, 0, 3)]
        winners = _award_winners(rows, lambda row: (-row["goals"], -row["assists"], row["appearances"]), value_field="goals", require_positive=True)
        self.assertEqual(winners, [])

    def test_mvp_ties_share_after_rating_and_appearances(self) -> None:
        rows = [self._row("A", 1, 1, 5, 8.0), self._row("B", 2, 2, 5, 8.0), self._row("C", 3, 3, 4, 8.0)]
        winners = _award_winners(rows, lambda row: (-row["rating"], -row["appearances"]), value_field="rating")
        self.assertEqual([row["label"] for row in winners], ["A", "B"])


if __name__ == "__main__":
    unittest.main()
