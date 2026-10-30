"""V0.3.1 candidate, scoring and market-value contracts."""
import unittest
from copy import deepcopy
from unittest.mock import patch

from football_simulator.domain.formulas import calculate_market_value, calculate_player_rating, calculate_player_rating_v031
from football_simulator.game import awards_v031, rules, world as game_world


def _player(name, team, position="FW", real=True):
    return {"name": name, "position": position, "team_id": team, "ability": 75, "is_real": real, "slot_number": 1}


def _fact(team, goals=0, defenses=0):
    return {"appeared": True, "team_id": team, "goals": goals, "assists": 0, "chances_created": 1,
            "successful_defenses": defenses, "successful_saves": 0, "clean_sheets": 0}


class AwardsV031Tests(unittest.TestCase):
    def setUp(self):
        self.world = {"season_number": 1, "awards_rule_version": "0.3.1", "edition_archives": {},
            "teams": {t: {"club_en": t} for t in ("A", "B", "C")},
            "players": {"a": _player("A", "A"), "b": _player("B", "B", "DF"),
                        "left": _player("Left", "C"), "third": _player("Third", "C"),
                        "default": _player("Default", "A", real=False)},
            "season": {"fixtures": [{"competition": rules.PREMIER, "status": "completed", "home_team_id": "A", "away_team_id": "B",
                "player_facts": {"a": _fact("A", 1), "b": _fact("B", defenses=3), "left": _fact("A", 8),
                                 "third": _fact("C", 4), "default": _fact("A", 12)}}],
                "final_tables": {"premier": [{"team_id": "A"}, {"team_id": "B"}]}, "cups": {}, "honor_rows": []}}
        self.world["season"]["award_team_snapshots"] = {
            rules.PREMIER: {pid: player["team_id"] for pid, player in self.world["players"].items()}
        }

    def _awards(self):
        with patch.object(awards_v031.editions, "finished", return_value=True):
            return awards_v031.competition_awards(self.world, [rules.PREMIER])[rules.PREMIER]

    def test_finalist_registration_real_players_and_mutual_exclusion(self):
        awards = self._awards()
        mvp = awards["mvp"]
        self.assertEqual(len(mvp), 1)
        self.assertIn(mvp[0]["player_id"], ("a", "b"))
        evp = {r["player_id"] for r in awards["evp"]}
        self.assertNotIn(mvp[0]["player_id"], evp)
        self.assertIn("left", evp)
        self.assertIn("third", evp)
        self.assertNotIn("default", evp)
        self.assertEqual(awards["evp_slots"], 8)

    def test_mvp_vacancy_keeps_other_team_evp(self):
        self.world["players"]["a"]["is_real"] = False
        self.world["players"]["b"]["is_real"] = False
        awards = self._awards()
        self.assertEqual(awards["mvp"], [])
        self.assertEqual({r["player_id"] for r in awards["evp"]}, {"left", "third"})

    def test_no_second_or_playoff_mvp_evp(self):
        self.world["season"]["fixtures"][0]["competition"] = rules.SECOND
        with patch.object(awards_v031.editions, "finished", return_value=True):
            result = awards_v031.competition_awards(self.world, [rules.SECOND, rules.PLAYOFF])
        self.assertEqual(result[rules.SECOND]["mvp"], [])
        self.assertEqual(result[rules.SECOND]["evp"], [])
        self.assertNotIn(rules.PLAYOFF, result)

    def test_market_value_keeps_old_rating(self):
        player = game_world._player_object(self.world, "b")
        stats = game_world._aggregate_player_facts(self.world, self.world["season"]["fixtures"])["b"]
        row = game_world._stats_namespace(player, stats)
        self.assertNotEqual(calculate_player_rating(row, 1), calculate_player_rating_v031(row, 1))
        expected = calculate_market_value(player, calculate_player_rating(row, 1))
        self.world["season"]["fixtures"][0]["week"] = 1
        self.world["season"]["settlements"] = {}
        game_world._compute_settlement(self.world, "winter", 1)
        self.assertEqual(self.world["season"]["settlements"]["winter"]["players"]["b"]["market_value"], expected)

    def test_transferred_player_counts_all_recorded_teams(self):
        self.world["players"]["left"]["team_id"] = "B"
        fixture = deepcopy(self.world["season"]["fixtures"][0])
        fixture["player_facts"] = {"left": _fact("B", 1)}
        self.world["season"]["fixtures"].append(fixture)
        awards = self._awards()
        winner = next(r for r in awards["mvp"] + awards["evp"] if r["player_id"] == "left")
        self.assertEqual(winner["appearances"], 2)
        self.assertEqual(winner["teams"], {"A": 1, "B": 1})
        self.assertEqual(winner["team_id"], "B")

    def test_annual_weights_and_only_actual_team_honors(self):
        self.world["season"]["honor_rows"] = [
            {"team_id": "A", "competition": rules.PREMIER, "points": 100},
            {"team_id": "C", "competition": rules.PREMIER, "points": 500},
        ]
        fixture_awards = {rules.PREMIER: {"mvp": [{"player_id": "a"}], "evp": [{"player_id": "b"}]}}
        with patch.object(awards_v031, "competition_awards", return_value=fixture_awards):
            rows = awards_v031.season_awards(self.world)["top20"]
        by_id = {r["player_id"]: r for r in rows}
        for row in rows:
            parts = row["components"]
            self.assertAlmostEqual(row["score"], 100 * (.35 * parts["performance"] + .55 * parts["awards"] + .10 * parts["team_honor"]))
        self.assertAlmostEqual(by_id["a"]["components"]["awards"], 1.0)
        self.assertAlmostEqual(by_id["b"]["components"]["awards"], 1 / 3)
        self.assertEqual(by_id["a"]["components"]["team_honor"], .2)
        self.assertEqual(by_id["b"]["components"]["team_honor"], 0)

    def test_annual_honor_uses_event_end_team_only_and_requires_appearance(self):
        fixture = deepcopy(self.world["season"]["fixtures"][0])
        fixture["player_facts"] = {"left": _fact("B", 1)}
        self.world["season"]["fixtures"].append(fixture)
        self.world["season"]["honor_rows"] = [
            {"team_id": "A", "competition": rules.PREMIER, "points": 100},
            {"team_id": "B", "competition": rules.PREMIER, "points": 200},
            {"team_id": "C", "competition": rules.PREMIER, "points": 300},
        ]
        self.world["edition_archives"]["1:premier"] = {"player_teams": {
            "a": "A", "b": "B", "third": "C", "left": "B"}}
        # The player later moved to C; the frozen event-end B wins over both
        # the earlier A appearance and the season-end C registration.
        with patch.object(awards_v031, "competition_awards", return_value={}):
            rows = {r["player_id"]: r for r in awards_v031.season_awards(self.world)["top20"]}
            self.assertAlmostEqual(rows["left"]["components"]["team_honor"], 2 / 3)
            self.world["edition_archives"]["1:premier"]["player_teams"]["left"] = "C"
            rows = {r["player_id"]: r for r in awards_v031.season_awards(self.world)["top20"]}
            self.assertEqual(rows["left"]["components"]["team_honor"], 0)

    def test_player_honors_preserve_multi_team_award_facts(self):
        from football_simulator.app import queries, records
        self.world["season"]["awards"] = {"competitions": {rules.PREMIER: {"mvp": [{
            "player_id": "left", "label": "Left", "team_id": "B", "value": 8.0,
            "teams": {"A": 24, "B": 14}}]}}}
        row = records._award_rows(self.world)[0]
        self.assertEqual(row["teams"], {"A": 24, "B": 14})
        self.assertEqual(records.award_team_history(self.world, row["teams"]),
                         "本届效力：A 24 场；B 14 场")
        self.assertEqual(records.award_team_history(self.world, {"A": 5}), "")
        self.assertEqual(queries._award_people(self.world["season"]["awards"]["competitions"][rules.PREMIER]["mvp"][0])["teams"], row["teams"])

    def test_compact_event_team_snapshot_stays_frozen_without_edition_archive(self):
        self.world.pop("edition_archives")
        self.world["season"]["award_team_snapshots"] = {}
        with patch.object(awards_v031.editions, "finished", side_effect=lambda w, c: c == rules.PREMIER):
            awards_v031.editions.capture_finished(self.world)
            self.world["players"]["left"]["team_id"] = "B"
            awards_v031.editions.capture_finished(self.world)
        self.assertEqual(self.world["season"]["award_team_snapshots"][rules.PREMIER]["left"], "C")


if __name__ == "__main__":
    unittest.main()
