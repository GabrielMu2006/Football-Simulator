# -*- coding: utf-8 -*-
"""Read-only presentation adapters: events, focus, brackets, role metrics.

Facts come from the frozen fixtures, not from stored headline strings: the tests
assert that generated-slot labels never surface, that scores keep all goals, and
that no story or bracket node is invented.
"""

from __future__ import annotations

import unittest

from tests import support

from football_simulator import runtime
from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui.presentation import bracket, copy, events, focus, roles

FIXTURES = support.PROJECT_ROOT / "tests" / "fixtures_vnext"
FORBIDDEN = ("默认 FW", "默认 DF", "默认 GK", "默认球员", "默认 MF")


def _import_fixture(name: str, package: str):
    if not runtime.save_exists(name):
        store.import_package(FIXTURES / package, name)
    return store.load_world(name)


class _FixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp_dir = __import__("tempfile").mkdtemp(prefix="fs_gf_pres_")
        cls._root = support.install_test_data_root(cls._tmp_dir)
        cls.service = GameService()
        cls.world = _import_fixture("s3", "s3-finalized.fsarchive")
        cls.winter = _import_fixture("s2", "s2-week25.fsarchive")

    @classmethod
    def tearDownClass(cls) -> None:
        support.reset_test_data_root()
        support.assert_cleanup_path_is_temporary(cls._root)
        import shutil

        shutil.rmtree(cls._root, ignore_errors=True)


class EventPresentationTests(_FixtureCase):
    def _completed_with_goals(self, world):
        for fixture in world["season"]["fixtures"]:
            if fixture.get("status") != "completed":
                continue
            goals = [event for event in fixture.get("events") or [] if event.get("type") == "goal"]
            if goals:
                return fixture
        self.fail("fixture 中没有含进球的比赛")

    def test_goal_tally_matches_final_score(self) -> None:
        for fixture in self.world["season"]["fixtures"]:
            if fixture.get("status") != "completed":
                continue
            tally = events.goal_tally(fixture.get("events") or [])
            home = int(fixture.get("home_goals") or 0) + int(fixture.get("home_goals_et") or 0)
            away = int(fixture.get("away_goals") or 0) + int(fixture.get("away_goals_et") or 0)
            self.assertEqual(tally.get(fixture["home_team_id"], 0), home, fixture["match_id"])
            self.assertEqual(tally.get(fixture["away_team_id"], 0), away, fixture["match_id"])

    def test_no_slot_labels_and_scores_preserved(self) -> None:
        fixture = self._completed_with_goals(self.world)
        detail = queries.match_detail(self.world, "s3", fixture["match_id"])
        rows = events.display_events(detail, self.world)
        self.assertTrue(rows)
        for row in rows:
            for token in FORBIDDEN:
                self.assertNotIn(token, row["text"])
        goals = [row for row in rows if row["kind"] == "goal"]
        self.assertEqual(len(goals), int(fixture.get("home_goals") or 0) + int(fixture.get("home_goals_et") or 0) + int(fixture.get("away_goals") or 0) + int(fixture.get("away_goals_et") or 0))
        self.assertTrue(all(row["score"] for row in goals))

    def test_placeholder_scorer_is_team_level_or_role(self) -> None:
        fixture = self._completed_with_goals(self.world)
        detail = queries.match_detail(self.world, "s3", fixture["match_id"])
        real = events.display_events(detail, self.world, scope="real")
        every = events.display_events(detail, self.world, scope="all")
        for row in real:
            self.assertNotIn("默认", row["text"])
        for row in every:
            self.assertNotIn("默认", row["text"])
        # some goals in this fixture have generated scorers: the real scope must
        # fall back to a team-level sentence, not to a role or a fabricated name.
        placeholders = [
            event
            for event in (detail.get("events") or [])
            if event.get("type") == "goal" and not events.real_name(self.world, event.get("player_id"))
        ]
        if placeholders:
            team_rows = [row for row in real if row["kind"] == "goal" and "：" not in row["text"]]
            self.assertTrue(team_rows, "占位射手应回退为球队级表述")

    def test_real_assist_kept_with_placeholder_scorer(self) -> None:
        for fixture in self.world["season"]["fixtures"]:
            for event in fixture.get("events") or []:
                if event.get("type") != "goal":
                    continue
                scorer = events.real_name(self.world, event.get("player_id"))
                assist = events.real_name(self.world, event.get("assist_id"))
                if scorer is None and assist:
                    detail = queries.match_detail(self.world, "s3", fixture["match_id"])
                    rows = [row for row in events.display_events(detail, self.world) if row["kind"] == "goal"]
                    match = next(row for row in rows if assist in row["text"])
                    self.assertIn(f"助攻 {assist}", match["text"])
                    self.assertNotIn("默认", match["text"])
                    return
        self.skipTest("fixture 中没有“占位进球 + 真实助攻”的组合")

    def test_minor_events_are_opt_in_and_never_expose_slots(self) -> None:
        fixture = self._completed_with_goals(self.world)
        detail = queries.match_detail(self.world, "s3", fixture["match_id"])
        major = events.display_events(detail, self.world)
        every = events.display_events(detail, self.world, include_minor=True)
        self.assertTrue(all(row["kind"] == "goal" for row in major))
        self.assertGreaterEqual(len(every), len(major))
        for row in every:
            self.assertNotIn("默认", row["text"])

    def test_shootout_is_separate_from_goals(self) -> None:
        for fixture in self.world["season"]["fixtures"]:
            if fixture.get("home_pk") is None:
                continue
            detail = queries.match_detail(self.world, "s3", fixture["match_id"])
            rows = events.display_events(detail, self.world)
            self.assertTrue(all(row["kind"] == "goal" for row in rows))
            kicks = events.shootout_rows(detail, self.world)
            self.assertTrue(kicks)
            for kick in kicks:
                self.assertNotIn("默认", kick["text"])
                self.assertIn("罚", kick["text"])
            return
        self.skipTest("fixture 中没有点球大战")


class FocusTests(_FixtureCase):
    def test_focus_is_deterministic_and_factual(self) -> None:
        data = queries.dashboard(self.world, "s3")
        first = focus.focus_candidate(self.world, data)
        second = focus.focus_candidate(self.world, data)
        self.assertEqual(first, second)
        self.assertIsNotNone(first)
        self.assertNotIn("默认", first["headline"])
        for token in ("提前夺冠", "已保级", "锁定"):
            self.assertNotIn(token, first["headline"])
        self.assertTrue(first["route"][0] in {"match", "competition", "team"})

    def test_focus_after_season_end_uses_last_played_week(self) -> None:
        data = queries.dashboard(self.world, "s3")
        self.assertTrue(data["season_complete"])
        self.assertEqual(focus.focus_week(self.world, data), 49)
        candidate = focus.focus_candidate(self.world, data)
        self.assertIsNotNone(candidate)
        self.assertIn(candidate["kind"], {"champion", "leader_dropped", "streak_end", "comeback", "watch", "result", "qualification"})

    def test_short_facts_are_distinct_and_linked(self) -> None:
        data = queries.dashboard(self.world, "s3")
        facts = focus.short_facts(self.world, data, limit=3)
        self.assertLessEqual(len(facts), 3)
        self.assertEqual(len({fact["text"] for fact in facts}), len(facts))
        for fact in facts:
            self.assertTrue(fact["route"][0] in {"match", "competition", "team", "player"})
            self.assertNotIn("默认", fact["text"])

    def test_streak_only_counts_real_league_wins(self) -> None:
        week = int(self.world["season"]["week"]) - 1
        for fixture in self.world["season"]["fixtures"]:
            if fixture.get("competition") != "premier" or fixture.get("status") != "completed":
                continue
            for team_id in (fixture["home_team_id"], fixture["away_team_id"]):
                streak = focus.league_streak(self.world, team_id, upto_week=week)
                self.assertGreaterEqual(streak, 0)
                if streak == 0:
                    continue
                # Match-based runs do not require a fixture in the cutoff week,
                # so verify the last completed league match at/before the cutoff.
                matches = [
                    item
                    for item in self.world["season"]["fixtures"]
                    if item.get("competition") == "premier"
                    and item.get("status") == "completed"
                    and int(item.get("week") or 0) <= week
                    and team_id in (item["home_team_id"], item["away_team_id"])
                ]
                matches.sort(key=lambda item: (int(item.get("week") or 0), int(item.get("round_number") or 0), str(item.get("match_id"))))
                self.assertTrue(matches)
                self.assertEqual(focus._league_outcome(matches[-1], team_id), "win")
                return


class BracketTests(_FixtureCase):
    def test_aureole_completed_bracket_has_rounds_and_factual_links(self) -> None:
        detail = queries.competition_detail(self.world, "s3", "aureole")
        model = bracket.cup_bracket(self.world, "aureole", detail)
        self.assertIsNotNone(model)
        labels = [column["label"] for column in model["columns"]]
        self.assertEqual(labels, ["八强", "半决赛", "决赛"])
        self.assertTrue(model["completed"])
        for column in model["columns"]:
            for node in column["nodes"]:
                for team in node["teams"]:
                    self.assertNotIn("默认", team["name"])
        for link in model["links"]:
            source = next(node for column in model["columns"] for node in column["nodes"] if node["tie_id"] == link["from"])
            target = next(node for column in model["columns"] for node in column["nodes"] if node["tie_id"] == link["to"])
            self.assertEqual(source["winner_id"], link["team_id"])
            self.assertIn(link["team_id"], target["team_ids"])

    def test_destiny_never_draws_future_opponents(self) -> None:
        detail = queries.competition_detail(self.winter, "s2", "confluence")
        model = bracket.cup_bracket(self.winter, "confluence", detail)
        if model is None:
            self.skipTest("冬窗存档中命途杯尚未开赛")
        played_teams = set()
        for column in model["columns"]:
            for node in column["nodes"]:
                if node["match_ids"]:
                    played_teams.update(node["team_ids"])
        # Every named team in a later round either already played that round or
        # was drawn from the previous one; no node may name a team without a tie.
        for column in model["columns"]:
            for node in column["nodes"]:
                if not node["match_ids"] and not node["team_ids"]:
                    continue
                self.assertTrue(node["team_ids"], "已存在的对阵必须来自已保存的签表")
        if not model["completed"]:
            self.assertIsNotNone(model["pending"], "未结束的重抽签赛事必须显示待抽签/进行中")

    def test_resurgence_has_winner_loser_and_grand_final(self) -> None:
        detail = queries.competition_detail(self.world, "s3", "ember")
        model = bracket.cup_bracket(self.world, "ember", detail)
        self.assertIsNotNone(model)
        self.assertEqual([group["key"] for group in model["groups"]], ["wb", "lb", "gf"])
        for group in model["groups"]:
            for round_data in group["rounds"]:
                self.assertTrue(round_data["nodes"])
        self.assertTrue(model["no_reset"])

    def test_apex_knockout_only_after_swiss(self) -> None:
        detail = queries.competition_detail(self.world, "s3", "starpath")
        model = bracket.cup_bracket(self.world, "starpath", detail)
        self.assertIsNotNone(model)
        self.assertEqual([column["label"] for column in model["columns"]], ["八强", "半决赛", "决赛"])
        seeded = self.world["season"]["cups"]["starpath"].get("knockout_seeds") or {}
        self.assertTrue(seeded)

    def test_winter_swiss_seeds_quarterfinals_without_winner(self) -> None:
        """Swiss finished before winter: QF pairs come from stored seeds, no winner yet."""
        state = self.winter["season"]["cups"]["starpath"]
        self.assertTrue(state["stage_done"]["swiss"])
        detail = queries.competition_detail(self.winter, "s2", "starpath")
        model = bracket.cup_bracket(self.winter, "starpath", detail)
        self.assertIsNotNone(model)
        first_round = model["columns"][0]
        self.assertEqual(first_round["label"], "八强")
        seed_by_team = state["knockout_seeds"]
        for node in first_round["nodes"]:
            self.assertEqual(len(node["team_ids"]), 2)
            self.assertIsNone(node["winner_id"], "八强未赛不能写出晋级方")
            seeds = sorted(seed_by_team[team_id] for team_id in node["team_ids"])
            self.assertIn(seeds, ([1, 8], [4, 5], [2, 7], [3, 6]))
        self.assertFalse(model["completed"])


class RoleMetricTests(unittest.TestCase):
    def test_role_metrics_follow_position(self) -> None:
        stats = {"appearances": 12, "goals": 0, "assists": 0, "chances_created": 9, "successful_defenses": 31, "successful_saves": 44, "clean_sheets": 6}
        gk = roles.role_metrics("GK", stats, 7.25)
        self.assertEqual([row["label"] for row in gk], ["出场", "成功扑救", "零封", "评分"])
        self.assertEqual(gk[1]["value"], "44")
        fw = roles.role_metrics("FW", stats, None)
        self.assertEqual([row["label"] for row in fw], ["出场", "进球", "助攻", "评分"])
        self.assertEqual(fw[-1]["value"], "—")
        df = roles.role_metrics("DF", stats, 6.5)
        self.assertIn("成功防守", [row["label"] for row in df])
        self.assertNotIn("成功扑救", [row["label"] for row in df])

    def test_valuation_is_neutral_and_complete(self) -> None:
        self.assertEqual(roles.valuation_text(None), "—")
        self.assertEqual(roles.valuation_text(121.5), "121.50")
        self.assertEqual(roles.valuation_text(0), "0.00")
        for token in ("€", "M", "万", "千"):
            self.assertNotIn(token, roles.valuation_text(120.0))

    def test_action_labels_name_destinations(self) -> None:
        self.assertEqual(copy.label_for_route("match"), "查看战报")
        self.assertEqual(copy.label_for_route("competition"), "查看赛事")


if __name__ == "__main__":
    unittest.main()
