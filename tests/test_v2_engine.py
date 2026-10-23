"""稳定身份与比赛引擎：90 分钟冻结、中立场、加时、点球、两回合。"""

from __future__ import annotations

import random
import unittest

from football_simulator.game.config import build_teams_and_players, load_master_config, team_objects
from football_simulator.game.engine import MatchEngine, PENALTY_SUCCESS_PROBABILITY
from football_simulator.game.models import team_id_for
from football_simulator.match_engine import simulate_match
from football_simulator.models import Fixture, Player, Team

from tests.support import FreezeTestCase


def _old_team(team):
    return Team(
        name=team.name,
        roster=tuple(
            Player(
                player_id=player.player_id,
                name=player.name,
                position=player.position,
                ability=player.ability,
                is_real=player.is_real,
                slot_number=player.slot_number,
                initial_market_value=player.initial_market_value,
            )
            for player in team.roster
        ),
        division="一级联赛",
    )


class IdentityTests(FreezeTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_master_config()

    def test_440_slots_have_unique_stable_ids(self) -> None:
        teams, players, draft_pool, _ = build_teams_and_players(self.config, random.Random(11))
        self.assertEqual(len(teams), 40)
        self.assertEqual(len(players), 440)
        self.assertEqual(len({entry["player_id"] for team in teams.values() for entry in team["roster"]}), 440)
        for team in teams.values():
            gk = next(entry for entry in team["roster"] if entry["position"] == "GK")
            fw = [entry for entry in team["roster"] if entry["position"] == "FW"]
            self.assertEqual(len(fw), 3)
            self.assertNotEqual(gk["player_id"], fw[0]["player_id"])
            if not players[gk["player_id"]].get("is_real"):
                self.assertIn(":GK:1", gk["player_id"])
            if not players[fw[0]["player_id"]].get("is_real"):
                self.assertIn(":FW:1", fw[0]["player_id"])
        self.assertEqual(len(draft_pool), 200)

    def test_team_ids_are_name_independent_and_stable(self) -> None:
        self.assertEqual(team_id_for("Kingsmeadow Rovers"), "team-kingsmeadow-rovers")
        self.assertEqual(team_id_for("Kingsmeadow Rovers"), team_id_for("Kingsmeadow  Rovers"))
        teams, players, _, _ = build_teams_and_players(self.config, random.Random(1))
        # 改名只影响显示，不影响 ID/球场绑定/阵容。
        team_id, team = next(iter(teams.items()))
        original = dict(team)
        team["name_zh"] = "改过的名字"
        self.assertEqual(team["team_id"], team_id)
        self.assertEqual(team["stadium_id"], original["stadium_id"])
        self.assertEqual(team["roster"], original["roster"])


class EngineFreezeTests(FreezeTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_master_config()
        cls.teams, cls.players, _, _ = build_teams_and_players(cls.config, random.Random(2026))
        cls.objects = team_objects(cls.teams, cls.players)
        cls.ids = list(cls.objects)

    def _spec(self, home, away, *, venue="home", advantage=None, need_winner=False, tie_id=None):
        return {
            "match_id": "test-match",
            "season_number": 1,
            "competition": "premier",
            "stage": "league",
            "week": 1,
            "round": 1,
            "ordinal": 0,
            "stadium_id": self.objects[home].stadium_id,
            "venue_mode": venue,
            "home_advantage_team_id": home if venue == "home" else None,
            "tie_id": tie_id,
        }

    def test_normal_90_minutes_matches_frozen_engine(self) -> None:
        home, away = self.ids[0], self.ids[1]
        for seed in (1, 2, 3):
            old = simulate_match(Fixture(1, _old_team(self.objects[home]), _old_team(self.objects[away])), random.Random(seed))
            new = MatchEngine(random.Random(seed)).play(self._spec(home, away), self.objects[home], self.objects[away])
            self.assertEqual((old.home_goals, old.away_goals), (new.home_goals, new.away_goals))
            old_active = {
                pid: (delta.goals, delta.assists, delta.chances_created, delta.successful_defenses, delta.successful_saves, delta.clean_sheets)
                for pid, delta in old.player_stats.items()
            }
            new_active = {
                pid: (
                    fact["goals"], fact["assists"], fact["chances_created"],
                    fact["successful_defenses"], fact["successful_saves"], fact["clean_sheets"],
                )
                for pid, fact in new.player_facts.items()
                if any(fact[field] for field in ("goals", "assists", "chances_created", "successful_defenses", "successful_saves", "clean_sheets"))
            }
            self.assertEqual(old_active, new_active)

    def test_neutral_has_no_home_advantage_even_at_own_stadium(self) -> None:
        home, away = self.ids[0], self.ids[1]
        match = MatchEngine(random.Random(5)).play(
            self._spec(home, away, venue="neutral"),
            self.objects[home],
            self.objects[away],
        )
        self.assertEqual(match.venue_mode, "neutral")
        self.assertIsNone(match.home_advantage_team_id)
        self.assertIsNone(match.home_pk)  # 联赛式无决胜要求

    def test_extra_time_counts_and_penalties_are_separate(self) -> None:
        home, away = self.ids[0], self.ids[1]
        et_seen = None
        pk_seen = None
        for seed in range(1, 800):
            engine = MatchEngine(random.Random(seed))
            match = engine.play(self._spec(home, away), self.objects[home], self.objects[away], need_winner=True)
            if match.extra_time_played and et_seen is None:
                et_seen = match
            if match.home_pk is not None:
                pk_seen = match
                break
        self.assertIsNotNone(et_seen, "受控随机源中应出现需要加时的平局")
        self.assertEqual(
            et_seen.home_goals + et_seen.away_goals + et_seen.home_goals_et + et_seen.away_goals_et,
            sum(fact["goals"] for fact in et_seen.player_facts.values()),
        )
        self.assertIsNotNone(pk_seen, "受控随机源中应出现点球大战")
        # 点球不计普通进球/助攻/扑救；整场仍只记一次出场。
        self.assertEqual(sum(fact["goals"] for fact in pk_seen.player_facts.values()), pk_seen.home_goals + pk_seen.home_goals_et + pk_seen.away_goals + pk_seen.away_goals_et)
        self.assertEqual(len(pk_seen.player_facts), 22)
        self.assertTrue(all(fact["appeared"] == 1 for fact in pk_seen.player_facts.values()))
        self.assertGreaterEqual(sum(1 for kick in pk_seen.shootout if kick["scored"]), 1)
        self.assertIn(pk_seen.decided_by, ("pk",))
        self.assertNotEqual(pk_seen.home_pk, pk_seen.away_pk)

    def test_two_leg_extra_time_only_when_aggregate_level(self) -> None:
        home, away = self.ids[0], self.ids[1]
        # 先找一个首回合比分，次回合用聚合比分触发加时。
        first = MatchEngine(random.Random(7)).play(self._spec(home, away), self.objects[home], self.objects[away])
        aggregate = (first.away_goals, first.home_goals)  # 次回合主客互换
        second = MatchEngine(random.Random(8)).play(
            self._spec(away, home, need_winner=True),
            self.objects[away],
            self.objects[home],
            need_winner=True,
            aggregate=aggregate,
        )
        if not second.extra_time_played:
            self.assertNotEqual(
                second.home_goals + aggregate[0],
                second.away_goals + aggregate[1],
            )
            self.assertIsNotNone(second.winner_team_id)
        else:
            self.assertIn(second.winner_team_id, (away, home))


if __name__ == "__main__":
    unittest.main()
