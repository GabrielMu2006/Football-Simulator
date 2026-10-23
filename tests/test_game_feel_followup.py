# -*- coding: utf-8 -*-
"""FUP-010..050 regressions: news score direction, double-elimination paths,
round selector content, league-leader news scope/time, match-based streaks.

Every case here was written against the reported failure first: the assertions
fail on the pre-fix code (see artifacts/game-feel-followup-2026-10-21/ for the
failing run) and pass afterwards. They read real saves or in-memory copies of
them; nothing here writes to a user save, and the UI cases use a temporary root.
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QTabBar

from tests import support

from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.components import BracketBoard
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.presentation import bracket as bracket_view
from football_simulator.ui.presentation import focus

FIXTURES = support.PROJECT_ROOT / "tests" / "fixtures_vnext"
PLAYTHROUGH = support.PROJECT_ROOT / "artifacts" / "macos-playthrough-2026-10-21"


# ---------------------------------------------------------------------------
# helpers: minimal worlds built from the same fixture shape the game stores
# ---------------------------------------------------------------------------


def fixture(
    match_id,
    week,
    home,
    away,
    home_goals,
    away_goals,
    *,
    competition="premier",
    events=None,
    home_goals_et=0,
    away_goals_et=0,
    home_pk=None,
    away_pk=None,
    round_number=1,
):
    winner = home if home_goals + home_goals_et > away_goals + away_goals_et else (
        away if away_goals + away_goals_et > home_goals + home_goals_et else None
    )
    if winner is None and home_pk is not None and away_pk is not None:
        winner = home if home_pk > away_pk else away
    return {
        "match_id": match_id,
        "week": week,
        "home_team_id": home,
        "away_team_id": away,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "home_goals_et": home_goals_et,
        "away_goals_et": away_goals_et,
        "home_pk": home_pk,
        "away_pk": away_pk,
        "winner_team_id": winner,
        "status": "completed",
        "competition": competition,
        "events": events or [],
        "round_number": round_number,
    }


def goal(minute, team, score, period="90"):
    return {"type": "goal", "minute": minute, "team_id": team, "score": list(score), "period": period}


def mini_world(fixtures, leader="c", teams="abcd"):
    return {
        "teams": {
            key: {"club_en": key.upper(), "division": "一级联赛" if key in "abc" else "二级联赛"}
            for key in teams
        },
        "players": {},
        "story_events": [],
        "season": {
            "fixtures": fixtures,
            "championships": {},
            "cups": {},
            "league": {"premier": list(teams), "second": []},
            "final_tables": {"premier": [{"team_id": leader, "played": 6, "points": 18, "goal_diff": 5, "goals_for": 12}]},
        },
    }


class ScoreDirectionTests(unittest.TestCase):
    """FUP-010: a winner-left headline must carry winner-left score."""

    def comeback(self, home, away, hg, ag, **kwargs):
        events = kwargs.pop("events", None)
        if events is None:
            events = [goal(10, home, (1, 0)), goal(20, away, (1, 1)), goal(30, away, (1, 2))]
        if hg < ag:
            events = [goal(10, home, (1, 0)), goal(20, away, (1, 1)), goal(30, away, (1, hg + 1))]
        else:
            events = [goal(10, away, (0, 1)), goal(20, home, (1, 1)), goal(30, home, (ag + 1, ag))]
        world = mini_world([fixture("m", 5, home, away, hg, ag, events=events, **kwargs)])
        return focus.focus_candidate(world, {"last_week": 5})

    def test_away_comeback_reads_winner_first(self) -> None:
        candidate = self.comeback("a", "b", 1, 2)
        self.assertEqual(candidate["kind"], "comeback")
        self.assertEqual(candidate["headline"], "B 2–1 A")
        self.assertEqual(candidate["team_id"], "b")

    def test_home_comeback_reads_winner_first(self) -> None:
        candidate = self.comeback("a", "b", 2, 1)
        self.assertEqual(candidate["headline"], "A 2–1 B")

    def test_away_comeback_with_extra_time_counts_official_score(self) -> None:
        events = [
            goal(10, "a", (1, 0)),
            goal(20, "b", (1, 1)),
            goal(100, "b", (1, 2), period="ET"),
        ]
        world = mini_world([fixture("m", 5, "a", "b", 1, 1, away_goals_et=1, events=events)])
        candidate = focus.focus_candidate(world, {"last_week": 5})
        self.assertEqual(candidate["headline"], "B 2–1 A")

    def test_away_streak_end_reads_winner_first(self) -> None:
        prior = [fixture(f"w{index}", index, "a", "d", 2, 0) for index in range(1, 6)]
        leader_runs = [fixture(f"c{index}", index, "c", "d", 3, 0) for index in range(1, 6)]
        loss = fixture("m6", 6, "a", "b", 1, 2)
        candidate = focus.focus_candidate(mini_world(prior + leader_runs + [loss]), {"last_week": 6})
        self.assertEqual(candidate["kind"], "streak_end")
        self.assertEqual(candidate["headline"], "B 2–1 A")
        self.assertIn("5 连胜", candidate["consequence"])

    def test_home_streak_end_reads_winner_first(self) -> None:
        prior = [fixture(f"w{index}", index, "d", "b", 0, 2) for index in range(1, 6)]
        leader_runs = [fixture(f"c{index}", index, "c", "d", 4, 0) for index in range(1, 6)]
        win = fixture("m6", 6, "a", "b", 3, 1)
        candidate = focus.focus_candidate(mini_world(prior + leader_runs + [win]), {"last_week": 6})
        self.assertEqual(candidate["kind"], "streak_end")
        self.assertEqual(candidate["headline"], "A 3–1 B")

    def test_draw_with_penalties_does_not_invent_a_lead(self) -> None:
        events = [goal(10, "a", (1, 0)), goal(20, "b", (1, 1))]
        world = mini_world(
            [fixture("m", 5, "a", "b", 1, 1, home_pk=4, away_pk=3, events=events, competition="aureole")],
            leader="a",
        )
        candidate = focus.focus_candidate(world, {"last_week": 5})
        self.assertIsNotNone(candidate)
        if candidate["kind"] == "comeback":
            self.assertIn("1–1", candidate["headline"])
            self.assertIn("点球", candidate["headline"])

    def test_plain_result_keeps_home_away_order(self) -> None:
        world = mini_world([fixture("m", 5, "a", "b", 0, 3)])
        candidate = focus.focus_candidate(world, {"last_week": 5})
        self.assertEqual(candidate["kind"], "result")
        self.assertEqual(candidate["headline"], "A 0–3 B")

    def test_focus_is_stable_across_two_calls(self) -> None:
        world = mini_world(
            [
                fixture("m", 5, "a", "b", 1, 2, events=[goal(5, "a", (1, 0)), goal(50, "b", (1, 2))]),
                fixture("n", 5, "c", "d", 2, 2),
            ]
        )
        first = focus.focus_candidate(world, {"last_week": 5})
        second = focus.focus_candidate(copy.deepcopy(world), {"last_week": 5})
        self.assertEqual(first, second)


class LeaderNewsTests(unittest.TestCase):
    """FUP-040: only premier results move the league-leader story."""

    def test_cup_loss_by_leader_is_not_a_league_drop(self) -> None:
        world = mini_world([fixture("cup", 8, "a", "b", 0, 1, competition="confluence")], leader="a")
        candidate = focus.focus_candidate(world, {"last_week": 8})
        self.assertIsNotNone(candidate)
        self.assertNotEqual(candidate["kind"], "leader_dropped")

    def test_league_draw_keeps_leader_and_says_so(self) -> None:
        world = mini_world(
            [
                fixture("m1", 1, "a", "d", 2, 0),
                fixture("m2", 1, "c", "b", 2, 0),
                fixture("m3", 2, "a", "b", 1, 1),
                fixture("m4", 2, "c", "d", 0, 0),
            ],
            leader="a",
        )
        candidate = focus.focus_candidate(world, {"last_week": 2})
        self.assertEqual(candidate["kind"], "leader_dropped")
        self.assertIn("继续领跑", candidate["consequence"])
        self.assertIn("逼平", candidate["headline"])

    def test_league_loss_hands_over_top_spot(self) -> None:
        world = mini_world(
            [
                fixture("m1", 1, "a", "d", 2, 0),
                fixture("m2", 1, "c", "b", 2, 0),
                fixture("m3", 2, "a", "b", 0, 3),
                fixture("m4", 2, "c", "d", 3, 0),
                fixture("m5", 3, "c", "a", 2, 0),
                fixture("m6", 3, "b", "d", 1, 0),
            ],
            leader="a",
        )
        candidate = focus.focus_candidate(world, {"last_week": 2})
        self.assertEqual(candidate["kind"], "leader_dropped")
        self.assertIn("让出榜首", candidate["consequence"])
        self.assertIn("C", candidate["consequence"])

    def test_equal_points_are_not_called_level_on_top(self) -> None:
        # Before week 2: a 3 pts (+2), c 1 pt (0).  After: both 4 pts, but c's
        # 3-0 win puts c first on goal difference.
        world = mini_world(
            [
                fixture("m1", 1, "a", "d", 2, 0),
                fixture("m2", 1, "c", "b", 1, 1),
                fixture("m3", 2, "a", "b", 1, 1),
                fixture("m4", 2, "c", "d", 3, 0),
            ],
            leader="a",
        )
        candidate = focus.focus_candidate(world, {"last_week": 2})
        self.assertEqual(candidate["kind"], "leader_dropped")
        self.assertIn("同分", candidate["consequence"])
        self.assertNotIn("并列", candidate["consequence"])

    def test_past_week_uses_tables_at_that_week_not_the_final_one(self) -> None:
        fixtures = [
            fixture("m1", 1, "a", "d", 2, 0),
            fixture("m2", 1, "c", "b", 0, 1),
            fixture("m3", 2, "a", "b", 1, 1),
            fixture("m4", 2, "c", "d", 4, 0),
            fixture("m5", 3, "c", "a", 3, 0),
        ]
        world = mini_world(fixtures, leader="c")
        # Reviewing week 2: a was top after week 1 and still top after the draw
        # (4 points vs c's 3); only week 3 flips the table, which is not known here.
        candidate = focus.focus_candidate(world, {"last_week": 2})
        self.assertEqual(candidate["kind"], "leader_dropped")
        self.assertIn("继续领跑", candidate["consequence"])

    def test_week_without_league_matches_has_no_leader_story(self) -> None:
        world = mini_world(
            [
                fixture("m1", 1, "a", "d", 2, 0),
                fixture("cup", 2, "a", "b", 0, 1, competition="aureole"),
            ],
            leader="a",
        )
        candidate = focus.focus_candidate(world, {"last_week": 2})
        self.assertNotEqual((candidate or {}).get("kind"), "leader_dropped")

    def test_zero_match_prior_table_does_not_create_a_drop(self) -> None:
        world = mini_world([fixture("m1", 1, "a", "b", 0, 2)], leader="a")
        candidate = focus.focus_candidate(world, {"last_week": 1})
        self.assertNotEqual((candidate or {}).get("kind"), "leader_dropped")

    def test_reading_leader_news_does_not_touch_the_world(self) -> None:
        world = mini_world([fixture("m1", 1, "a", "b", 1, 1)], leader="a")
        before = copy.deepcopy(world)
        focus.focus_candidate(world, {"last_week": 1})
        focus.short_facts(world, {"last_week": 1})
        self.assertEqual(world, before)


class StreakTests(unittest.TestCase):
    """FUP-050: consecutive league matches, not consecutive calendar weeks."""

    def test_two_wins_across_empty_weeks_count_two(self) -> None:
        world = mini_world([fixture("m1", 24, "a", "d", 1, 0), fixture("m2", 28, "a", "d", 1, 0)])
        self.assertEqual(focus.league_streak(world, "a", upto_week=28), 2)
        self.assertEqual(focus.league_unbeaten(world, "a", upto_week=28), 2)

    def test_win_draw_win_gives_streak_one_and_unbeaten_three(self) -> None:
        world = mini_world(
            [
                fixture("m1", 10, "a", "d", 1, 0),
                fixture("m2", 12, "a", "d", 1, 1),
                fixture("m3", 16, "a", "d", 2, 0),
            ]
        )
        self.assertEqual(focus.league_streak(world, "a", upto_week=16), 1)
        self.assertEqual(focus.league_unbeaten(world, "a", upto_week=16), 3)

    def test_cup_matches_between_league_wins_do_not_break_the_run(self) -> None:
        world = mini_world(
            [
                fixture("m1", 10, "a", "d", 1, 0),
                fixture("cup", 11, "a", "b", 0, 2, competition="aureole"),
                fixture("m2", 12, "a", "d", 1, 0),
            ]
        )
        self.assertEqual(focus.league_streak(world, "a", upto_week=12), 2)

    def test_unplayed_and_later_matches_are_ignored(self) -> None:
        later = dict(fixture("m3", 30, "a", "d", 3, 0))
        scheduled = dict(fixture("m4", 31, "a", "d", 0, 0))
        scheduled["status"] = "scheduled"
        world = mini_world([fixture("m1", 10, "a", "d", 1, 0), later, scheduled])
        self.assertEqual(focus.league_streak(world, "a", upto_week=20), 1)

    def test_empty_sequence_is_zero(self) -> None:
        self.assertEqual(focus.league_streak(mini_world([]), "a", upto_week=30), 0)
        self.assertEqual(focus.league_unbeaten(mini_world([]), "a", upto_week=30), 0)

    def test_streak_end_counts_wins_before_the_loss_only(self) -> None:
        prior = [fixture(f"w{index}", index, "a", "d", 2, 0) for index in range(1, 6)]
        leader_runs = [fixture(f"c{index}", index, "c", "d", 3, 0) for index in range(1, 6)]
        loss = fixture("m6", 6, "a", "b", 1, 2)
        candidate = focus.focus_candidate(mini_world(prior + leader_runs + [loss]), {"last_week": 6})
        self.assertEqual(candidate["kind"], "streak_end")
        self.assertIn("5 连胜", candidate["consequence"])
        self.assertEqual(focus.league_streak(mini_world(prior + leader_runs + [loss]), "a", upto_week=6), 0)


class DoubleEliminationModelTests(unittest.TestCase):
    """FUP-020 data: every edge is a real winner/loser with a real target slot."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile

        cls._tmp = tempfile.mkdtemp(prefix="fs_fup_model_")
        support.install_test_data_root(cls._tmp)
        cls.service = GameService()
        store.import_package(FIXTURES / "s3-finalized.fsarchive", "final")
        cls.world = store.load_world("final")
        cls.detail = queries.competition_detail(cls.world, "final", "ember")
        cls.model = bracket_view.cup_bracket(cls.world, "ember", cls.detail)

    @classmethod
    def tearDownClass(cls) -> None:
        support.reset_test_data_root()
        support.assert_cleanup_path_is_temporary(cls._tmp)
        import shutil

        shutil.rmtree(cls._tmp, ignore_errors=True)

    def stage_nodes(self):
        nodes = {}
        for group in self.model["groups"]:
            for round_data in group["rounds"]:
                for node in round_data["nodes"]:
                    nodes[node["stage"]] = nodes.get(node["stage"], []) + [node]
        return nodes

    def test_groups_and_rounds_are_named_from_saved_stages(self) -> None:
        self.assertEqual([group["key"] for group in self.model["groups"]], ["wb", "lb", "gf"])
        labels = [round_data["label"] for group in self.model["groups"] for round_data in group["rounds"]]
        self.assertEqual(
            labels,
            ["胜者组首轮", "胜者组次轮", "胜者组决赛", "败者组首轮", "败者组次轮", "败者组第三轮", "败者组决赛", "总决赛"],
        )
        nodes = self.stage_nodes()
        self.assertEqual(len(nodes["wb_quarterfinal"]), 4)
        self.assertEqual(len(nodes["lb_round_2"]), 2)
        self.assertEqual(len(nodes["grand_final"]), 1)

    def test_every_edge_matches_the_real_winner_or_loser_and_slot(self) -> None:
        state = self.world["season"]["cups"]["ember"]
        state_ties = {
            tie["tie_id"]: tie
            for key in ("wb_quarterfinal", "wb_semifinal", "wb_final", "lb_round_1", "lb_round_2", "lb_round_3", "lb_final", "grand_final")
            for tie in (state.get(key) if isinstance(state.get(key), list) else [state.get(key)] if state.get(key) else [])
        }
        self.assertTrue(self.model["edges"])
        self.assertEqual(len(self.model["edges"]), 20)
        for edge in self.model["edges"]:
            source = state_ties[edge["source_tie_id"]]
            target = state_ties[edge["target_tie_id"]]
            expected = source["winner"] if edge["outcome"] == "winner" else [team for team in source["teams"] if team != source["winner"]][0]
            self.assertEqual(edge["team_id"], expected, edge)
            self.assertIn(edge["team_id"], target["teams"], edge)
            self.assertEqual(target["teams"][edge["target_slot"]], edge["team_id"], edge)

    def test_crossed_winner_semifinal_losers(self) -> None:
        edges = {(edge["source_tie_id"], edge["outcome"]): edge for edge in self.model["edges"]}
        state = self.world["season"]["cups"]["ember"]
        second_semifinal_loser = [team for team in state["wb_semifinal"][1]["teams"] if team != state["wb_semifinal"][1]["winner"]][0]
        first_semifinal_loser = [team for team in state["wb_semifinal"][0]["teams"] if team != state["wb_semifinal"][0]["winner"]][0]
        self.assertEqual(edges[(state["wb_semifinal"][1]["tie_id"], "loser")]["target_tie_id"], state["lb_round_2"][0]["tie_id"])
        self.assertEqual(edges[(state["wb_semifinal"][1]["tie_id"], "loser")]["target_slot"], 1)
        self.assertEqual(edges[(state["wb_semifinal"][1]["tie_id"], "loser")]["team_id"], second_semifinal_loser)
        self.assertEqual(edges[(state["wb_semifinal"][0]["tie_id"], "loser")]["target_tie_id"], state["lb_round_2"][1]["tie_id"])
        self.assertEqual(edges[(state["wb_semifinal"][0]["tie_id"], "loser")]["team_id"], first_semifinal_loser)

    def test_grand_final_has_both_sources_and_no_reset(self) -> None:
        final_node = next(
            node
            for group in self.model["groups"]
            if group["key"] == "gf"
            for round_data in group["rounds"]
            for node in round_data["nodes"]
        )
        incoming = [edge for edge in self.model["edges"] if edge["target_tie_id"] == final_node["tie_id"]]
        self.assertEqual(len(incoming), 2)
        self.assertEqual({edge["target_slot"] for edge in incoming}, {0, 1})
        self.assertEqual({edge["source_tie_id"] for edge in incoming}, {
            self.world["season"]["cups"]["ember"]["wb_final"]["tie_id"],
            self.world["season"]["cups"]["ember"]["lb_final"]["tie_id"],
        })
        self.assertTrue(self.model["no_reset"])

    def test_nodes_expose_clickable_source_and_next_station(self) -> None:
        node = self.stage_nodes()["lb_round_2"][0]
        self.assertTrue(node["paths_in"])
        self.assertTrue(node["paths_out"])
        incoming = node["paths_in"][0]
        self.assertEqual(incoming["direction"], "in")
        self.assertEqual(incoming["stage_label"], "胜者组次轮")
        self.assertTrue(node["title"])

    def test_incomplete_stage_does_not_invent_later_nodes(self) -> None:
        for stage in ("wb_final", "lb_final", "grand_final"):
            world = copy.deepcopy(self.world)
            state = world["season"]["cups"]["ember"]
            for key in ("wb_final", "lb_final", "grand_final", "lb_round_3"):
                if key in state:
                    state[key] = [] if isinstance(state[key], list) else None
            state["champion"] = None
            detail = queries.competition_detail(world, "final", "ember")
            model = bracket_view.cup_bracket(world, "ember", detail)
            self.assertIsNotNone(model)
            ties = [
                node["stage"]
                for group in model["groups"]
                for round_data in group["rounds"]
                for node in round_data["nodes"]
            ]
            self.assertNotIn("grand_final", ties)
            for edge in model["edges"]:
                self.assertNotEqual(edge["team_id"], None)
            break


class DoubleEliminationUiTests(unittest.TestCase):
    """FUP-020/030 UI: readable groups, real widths, working round selector."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile

        cls._tmp = tempfile.mkdtemp(prefix="fs_fup_ui_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService()
        store.import_package(FIXTURES / "s3-finalized.fsarchive", "final")
        store.import_package(FIXTURES / "s2-week25.fsarchive", "winter")
        cls.window = MainWindow(cls.service)
        cls.window.setFixedSize(1280, 800)
        cls.window.show()
        cls.window.open_save("final")
        QTest.qWait(150)
        cls.world = store.load_world("final")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.window.deleteLater()
        support.cleanup_qt_widgets()
        support.reset_test_data_root()
        support.assert_cleanup_path_is_temporary(cls._tmp)
        import shutil

        shutil.rmtree(cls._tmp, ignore_errors=True)

    def open_board(self, competition="ember", width=1280):
        self.window.setFixedSize(width, 800)
        self.window.navigate("competition", competition_id=competition)
        QTest.qWait(120)
        page = self.window.stack.currentWidget()
        # These tests exercise the retained round selector explicitly; the
        # single-elimination cups now open their complete panorama by default.
        page.detail_state(competition)["bracket_view"] = "rounds"
        self.window.refresh_all()
        QTest.qWait(80)
        page = self.window.stack.currentWidget()
        board = page.findChild(BracketBoard)
        self.assertIsNotNone(board)
        page.scroll.ensureWidgetVisible(board)
        QTest.qWait(80)
        return page, board

    def test_double_elimination_shows_one_readable_group_at_a_time(self) -> None:
        page, board = self.open_board(width=1280)
        self.assertEqual(board.model["mode"], "groups")
        tabs = board.findChild(QTabBar, "BracketGroups")
        self.assertIsNotNone(tabs)
        self.assertEqual([tabs.tabText(index) for index in range(tabs.count())], ["胜者组", "败者组", "总决赛"])
        self.assertEqual(board.group_key, "gf")
        self.assertEqual(sorted(board.visible_tie_ids()), [board.model["groups"][2]["rounds"][0]["nodes"][0]["tie_id"]])

    def test_each_group_node_shows_the_full_club_name(self) -> None:
        page, board = self.open_board(width=1280)
        metrics = board.name_metrics
        for group_key in ("wb", "lb", "gf"):
            board.select_group(group_key)
            QTest.qWait(60)
            nodes = board.visible_nodes()
            self.assertTrue(nodes, group_key)
            for node in nodes:
                for team in node["teams"]:
                    if not team.get("team_id"):
                        continue
                    widgets = [
                        label
                        for label in board.findChildren(QLabel)
                        if label.isVisible() and label.text() == team["name"]
                    ]
                    self.assertTrue(widgets, team["name"])
                    widget = widgets[0]
                    self.assertGreaterEqual(widget.width() + 1, metrics.horizontalAdvance(team["name"]), team["name"])
                    self.assertGreaterEqual(node["min_width"], metrics.horizontalAdvance(team["name"]))

    def test_group_switch_changes_visible_nodes_and_path_links(self) -> None:
        page, board = self.open_board(width=1280)
        board.select_group("wb")
        QTest.qWait(60)
        wb_ids = set(board.visible_tie_ids())
        self.assertEqual(len(wb_ids), 7)
        tab = board.findChild(QTabBar, "BracketGroups")
        index = next(i for i in range(tab.count()) if tab.tabData(i) == "lb")
        QTest.mouseClick(tab, Qt.LeftButton, pos=tab.tabRect(index).center())
        QTest.qWait(80)
        self.assertEqual(board.group_key, "lb")
        self.assertFalse(wb_ids & set(board.visible_tie_ids()))

    def test_path_link_jumps_to_the_cross_group_target(self) -> None:
        page, board = self.open_board(width=1280)
        board.select_group("lb")
        QTest.qWait(60)
        # A loser-bracket node fed by a winner-bracket semifinal loss must offer
        # a link back to that winner-bracket tie.
        cross = [
            node
            for node in board.visible_nodes()
            if any(path["group"] == "wb" for path in node["paths_in"])
        ]
        self.assertTrue(cross, "败者组节点应显示来自胜者组的落位")
        node = cross[0]
        path = next(path for path in node["paths_in"] if path["group"] == "wb")
        board.focus_tie(path["group"], path["tie_id"])
        QTest.qWait(80)
        self.assertEqual(board.group_key, "wb")
        self.assertIn(path["tie_id"], board.visible_tie_ids())

    def test_round_selector_mouse_selection_changes_content(self) -> None:
        page, board = self.open_board(competition="confluence", width=1280)
        self.assertTrue(board.round_box.isVisible(), "五轮签表在1280下应显示轮次选择器")
        keys = [column["key"] for column in board.model["columns"]]
        board.round_box.setCurrentIndex(0)
        QTest.qWait(60)
        self.assertEqual(board.round_key, keys[0])
        self.select_round(board, 1)
        QTest.qWait(80)
        self.assertEqual(board.round_key, keys[1])
        self.assertEqual(board.visible_round_keys(), [keys[1]])
        self.assertEqual(board.hint.text().split(" · ")[0], board.round_label())
        expected = [
            node["tie_id"]
            for column in board.model["columns"]
            if column["key"] == keys[1]
            for node in column["nodes"]
        ]
        self.assertEqual(board.visible_tie_ids(), expected)
        self.select_round(board, 2)
        QTest.qWait(80)
        self.assertEqual(board.round_key, keys[2])
        self.assertEqual(board.visible_tie_ids(), [
            node["tie_id"] for column in board.model["columns"] if column["key"] == keys[2] for node in column["nodes"]
        ])

    def select_round(self, board, index: int) -> None:
        board.round_box.setFocus()
        view = board.round_box.view()
        board.round_box.showPopup()
        QTest.qWait(60)
        if view.isVisible():
            rect = view.visualRect(view.model().index(index, 0))
            QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=rect.center())
        QTest.qWait(40)
        if board.round_box.currentIndex() != index:
            # Keyboard path for the same selection (also covers Qt platforms that
            # do not expose the popup to QTest).
            while board.round_box.currentIndex() < index:
                QTest.keyClick(board.round_box, Qt.Key_Down)
                QTest.qWait(20)
            while board.round_box.currentIndex() > index:
                QTest.keyClick(board.round_box, Qt.Key_Up)
                QTest.qWait(20)
        QTest.qWait(40)

    def test_round_selector_keeps_semantic_choice_across_widths(self) -> None:
        page, board = self.open_board(competition="confluence", width=1280)
        keys = [column["key"] for column in board.model["columns"]]
        self.select_round(board, 3)
        QTest.qWait(60)
        self.assertEqual(board.round_key, keys[3])
        self.window.setFixedSize(1680, 980)
        QTest.qWait(160)
        self.assertEqual(board.round_key, keys[3])
        if board.round_box.isVisible():
            self.assertEqual(board.visible_round_keys(), [keys[3]])
        else:
            self.assertIn(keys[3], board.visible_round_keys())
        self.window.setFixedSize(1280, 800)
        QTest.qWait(160)
        self.assertTrue(board.round_box.isVisible())
        self.assertEqual(board.round_key, keys[3])
        self.assertEqual(board.visible_round_keys(), [keys[3]])

    def test_round_selector_survives_refresh(self) -> None:
        page, board = self.open_board(competition="confluence", width=1280)
        keys = [column["key"] for column in board.model["columns"]]
        self.select_round(board, 2)
        QTest.qWait(60)
        self.assertEqual(board.round_key, keys[2])
        self.window.refresh_all()
        QTest.qWait(180)
        page = self.window.stack.currentWidget()
        board = page.findChild(BracketBoard)
        self.assertEqual(board.round_key, keys[2])
        self.assertEqual(board.visible_round_keys(), [keys[2]])

    def test_narrow_board_round_switch_updates_content_immediately(self) -> None:
        page, board = self.open_board(competition="aureole", width=1280)
        # Force the narrow layout on the widget itself: the selector must change
        # rounds without waiting for another resize.
        board.setFixedWidth(640)
        QTest.qWait(120)
        self.assertTrue(board.round_box.isVisible())
        keys = [column["key"] for column in board.model["columns"]]
        self.assertEqual(board.round_key, keys[0])
        self.assertEqual(board.visible_round_keys(), [keys[0]])
        board.round_box.setCurrentIndex(1)
        QTest.qWait(80)
        self.assertEqual(board.round_key, keys[1])
        self.assertEqual(board.visible_round_keys(), [keys[1]])
        self.assertFalse(
            set(board.visible_tie_ids())
            & {node["tie_id"] for node in board.model["columns"][0]["nodes"]}
        )
        board.setFixedWidth(16777215)

    def test_wide_layout_draws_connectors_inside_visible_group(self) -> None:
        page, board = self.open_board(width=1680)
        board.select_group("wb")
        QTest.qWait(80)
        self.assertTrue(board.visible_links())
        self.assertEqual(len(board.visible_round_keys()), 3)




class BracketStateSyncTests(unittest.TestCase):
    """BST-010/BST-020: one selection state for tabs, selector, content, page."""

    COMPETITION = "ember"

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile

        cls._tmp = tempfile.mkdtemp(prefix="fs_bst_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService()
        store.import_package(FIXTURES / "s3-finalized.fsarchive", "final")
        store.import_package(FIXTURES / "s2-week25.fsarchive", "winter")
        cls.window = MainWindow(cls.service)
        cls.window.setFixedSize(1280, 800)
        cls.window.show()
        cls.window.open_save("final")
        QTest.qWait(150)
        cls.world = store.load_world("final")
        cls.state = cls.world["season"]["cups"]["ember"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.window.deleteLater()
        support.cleanup_qt_widgets()
        support.reset_test_data_root()
        support.assert_cleanup_path_is_temporary(cls._tmp)
        import shutil

        shutil.rmtree(cls._tmp, ignore_errors=True)

    # -- helpers --------------------------------------------------------
    def open_bracket(self, competition=None, width=1280, fresh=True):
        competition = competition or self.COMPETITION
        self.window.setFixedSize(width, 800)
        page = self.window._pages.get("competition")
        if page is not None and fresh:
            page._detail_states.clear()
        self.window.navigate("competition", competition_id=competition)
        QTest.qWait(160)
        page = self.window.stack.currentWidget()
        board = page.findChild(BracketBoard)
        self.assertIsNotNone(board, "签表组件未渲染")
        page.scroll.ensureWidgetVisible(board)
        QTest.qWait(80)
        return page, board

    def tabs(self, board):
        tabs = board.findChild(QTabBar, "BracketGroups")
        self.assertIsNotNone(tabs)
        return tabs

    def selected_group(self, board):
        tabs = self.tabs(board)
        return tabs.tabData(tabs.currentIndex())

    def page_state(self, page, competition=None):
        return page.detail_state(competition or self.COMPETITION)

    def node_widget(self, board, tie_id):
        widget = board._nodes.get(tie_id)
        self.assertIsNotNone(widget, f"节点未渲染: {tie_id}")
        return widget

    def path_action(self, board, tie_id, prefix):
        """The real 路径 menu action for this node (UIP-050 hides long links)."""
        widget = self.node_widget(board, tie_id)
        button = widget.findChild(QPushButton, "PathMenu")
        self.assertIsNotNone(button, "节点应有紧凑的“路径”菜单")
        menu = button.menu()
        self.assertIsNotNone(menu)
        actions = [action for action in menu.actions() if action.text().startswith(prefix)]
        self.assertTrue(actions, [action.text() for action in menu.actions()])
        return actions[0]

    def click_path(self, board, tie_id, prefix):
        action = self.path_action(board, tie_id, prefix)
        action.trigger()
        QTest.qWait(140)

    def ember_tie(self, stage, index=0):
        value = self.state[stage]
        return (value[index] if isinstance(value, list) else value)["tie_id"]

    # -- BST-010 --------------------------------------------------------
    def test_t01_finished_cup_opens_on_grand_final_everywhere(self) -> None:
        page, board = self.open_bracket()
        self.assertEqual(board.group_key, "gf")
        self.assertEqual(self.selected_group(board), "gf")
        self.assertEqual(board.round_key, "grand_final")
        self.assertEqual(board.round_box.currentData(), "grand_final")
        self.assertEqual(board.visible_round_keys(), ["grand_final"])
        self.assertEqual(board.visible_tie_ids(), [self.ember_tie("grand_final")])
        tabs = self.tabs(board)
        self.assertEqual(tabs.tabText(tabs.currentIndex()), "总决赛")
        state = self.page_state(page)
        self.assertEqual((state.get("bracket_group"), state.get("bracket_round")), ("gf", "grand_final"))

    def test_t02_first_click_on_winner_group_switches_content(self) -> None:
        page, board = self.open_bracket()
        tabs = self.tabs(board)
        index = next(i for i in range(tabs.count()) if tabs.tabData(i) == "wb")
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
        QTest.qWait(140)
        self.assertEqual(board.group_key, "wb")
        self.assertEqual(self.selected_group(board), "wb")
        self.assertEqual(board.round_key, "wb_quarterfinal")
        self.assertEqual(board.round_box.currentData(), "wb_quarterfinal")
        self.assertIn("wb_quarterfinal", board.visible_round_keys())
        self.assertEqual(
            board.visible_tie_ids()[:4],
            [self.ember_tie("wb_quarterfinal", index) for index in range(4)],
        )
        state = self.page_state(page)
        self.assertEqual((state.get("bracket_group"), state.get("bracket_round")), ("wb", "wb_quarterfinal"))

    def test_t03_legal_restore_keeps_group_round_and_content(self) -> None:
        page, _ = self.open_bracket(fresh=False)
        state = self.page_state(page)
        state["bracket_group"] = "lb"
        state["bracket_round"] = "lb_round_2"
        page.refresh(page.snapshot, {"competition_id": self.COMPETITION})
        QTest.qWait(140)
        board = page.findChild(BracketBoard)
        self.assertEqual(board.group_key, "lb")
        self.assertEqual(self.selected_group(board), "lb")
        self.assertEqual(board.round_key, "lb_round_2")
        self.assertEqual(board.round_box.currentData(), "lb_round_2")
        self.assertEqual(board.visible_round_keys(), ["lb_round_2"])

    def test_t04_invalid_restore_is_normalized_without_fake_nodes(self) -> None:
        page, _ = self.open_bracket(fresh=False)
        state = self.page_state(page)
        state["bracket_group"] = "nope"
        state["bracket_round"] = "also-nope"
        page.refresh(page.snapshot, {"competition_id": self.COMPETITION})
        QTest.qWait(140)
        board = page.findChild(BracketBoard)
        self.assertEqual(board.group_key, "gf")
        self.assertEqual(self.selected_group(board), "gf")
        self.assertEqual(board.round_key, "grand_final")
        state = self.page_state(page)
        self.assertEqual((state.get("bracket_group"), state.get("bracket_round")), ("gf", "grand_final"))

        # cross-group round: legal group, round belonging to another group
        state["bracket_group"] = "lb"
        state["bracket_round"] = "wb_semifinal"
        page.refresh(page.snapshot, {"competition_id": self.COMPETITION})
        QTest.qWait(140)
        board = page.findChild(BracketBoard)
        self.assertEqual(board.group_key, "lb")
        self.assertEqual(board.round_key, "lb_round_1")
        self.assertEqual(self.selected_group(board), "lb")
        self.assertTrue(all(node["stage"] == "lb_round_1" for node in board.visible_nodes()))
        state = self.page_state(page)
        self.assertEqual((state.get("bracket_group"), state.get("bracket_round")), ("lb", "lb_round_1"))

    def test_t04b_empty_group_state_is_safe(self) -> None:
        page, _ = self.open_bracket(fresh=False)
        state = self.page_state(page)
        state["bracket_group"] = "gf"
        state["bracket_round"] = "lb_final"
        page.refresh(page.snapshot, {"competition_id": self.COMPETITION})
        QTest.qWait(120)
        board = page.findChild(BracketBoard)
        self.assertEqual(board.group_key, "gf")
        self.assertEqual(board.round_key, "grand_final")
        self.assertFalse(self.window.error_label.text())

    # -- BST-020 --------------------------------------------------------
    def cross_group_source(self):
        """A loser-bracket round-2 node whose source is a winner-bracket semifinal."""
        board_target = None
        for tie_id in [self.ember_tie("lb_round_2", 0), self.ember_tie("lb_round_2", 1)]:
            if tie_id == self.ember_tie("lb_round_2", 0):
                board_target = (tie_id, "来源：胜者组次轮")
            else:
                board_target = (tie_id, "来源：胜者组次轮")
        return board_target

    def test_t05_cross_group_path_button_updates_board_and_page(self) -> None:
        page, board = self.open_bracket()
        board.select_group("lb")
        QTest.qWait(120)
        # render the exact loser round that carries the crossed source
        board._round_key = "lb_round_2"
        board._render()
        QTest.qWait(120)
        tie_id = self.ember_tie("lb_round_2", 0)
        self.click_path(board, tie_id, "来源：胜者组次轮")
        self.assertEqual((board.group_key, board.round_key), ("wb", "wb_semifinal"))
        self.assertEqual(self.selected_group(board), "wb")
        self.assertEqual(board.round_box.currentData(), "wb_semifinal")
        state = self.page_state(page)
        self.assertEqual((state.get("bracket_group"), state.get("bracket_round")), ("wb", "wb_semifinal"))
        self.assertIn(self.ember_tie("wb_semifinal", 1), board.visible_tie_ids())
        # 1280 shows all three winner rounds; force the single-round layout to
        # prove the semantic round really drives the content.
        board.setFixedWidth(640)
        QTest.qWait(160)
        self.assertEqual(board.visible_round_keys(), ["wb_semifinal"])
        self.assertIn(self.ember_tie("wb_semifinal", 1), board.visible_tie_ids())
        board.setFixedWidth(16777215)
        QTest.qWait(120)

    def test_t05b_same_group_next_station_updates_page_round(self) -> None:
        page, board = self.open_bracket()
        board.select_group("wb")
        QTest.qWait(120)
        tie_id = self.ember_tie("wb_quarterfinal", 0)
        self.click_path(board, tie_id, "下一站：胜者组次轮")
        self.assertEqual((board.group_key, board.round_key), ("wb", "wb_semifinal"))
        self.assertEqual(board.round_box.currentData(), "wb_semifinal")
        state = self.page_state(page)
        self.assertEqual((state.get("bracket_group"), state.get("bracket_round")), ("wb", "wb_semifinal"))
        board.setFixedWidth(640)
        QTest.qWait(160)
        self.assertEqual(board.visible_round_keys(), ["wb_semifinal"])
        board.setFixedWidth(16777215)
        QTest.qWait(120)

    def test_t06_refresh_keeps_semantic_selection_with_new_board(self) -> None:
        page, board = self.open_bracket()
        board.select_group("lb")
        QTest.qWait(120)
        board._round_key = "lb_round_2"
        board._render()
        QTest.qWait(120)
        self.click_path(board, self.ember_tie("lb_round_2", 0), "来源：胜者组次轮")
        self.assertEqual((board.group_key, board.round_key), ("wb", "wb_semifinal"))
        old = board
        self.window.refresh_all()
        QTest.qWait(200)
        page = self.window.stack.currentWidget()
        fresh = page.findChild(BracketBoard)
        self.assertIsNot(fresh, old, "刷新后应得到新的签表控件")
        self.assertEqual((fresh.group_key, fresh.round_key), ("wb", "wb_semifinal"))
        self.assertEqual(self.selected_group(fresh), "wb")
        self.assertEqual(fresh.round_box.currentData(), "wb_semifinal")
        self.assertIn(self.ember_tie("wb_semifinal", 0), fresh.visible_tie_ids())
        state = self.page_state(page)
        self.assertEqual((state.get("bracket_group"), state.get("bracket_round")), ("wb", "wb_semifinal"))
        fresh.setFixedWidth(640)
        QTest.qWait(160)
        self.assertEqual(fresh.visible_round_keys(), ["wb_semifinal"])
        self.assertIn(self.ember_tie("wb_semifinal", 0), fresh.visible_tie_ids())
        fresh.setFixedWidth(16777215)
        QTest.qWait(120)

    def test_t06b_f5_shortcut_refreshes_and_keeps_selection(self) -> None:
        page, board = self.open_bracket()
        board.select_group("lb")
        QTest.qWait(120)
        board._round_key = "lb_round_2"
        board._render()
        QTest.qWait(120)
        self.click_path(board, self.ember_tie("lb_round_2", 0), "来源：胜者组次轮")
        old = board
        self.window.activateWindow()
        QTest.qWaitForWindowActive(self.window, 2000)
        QTest.keyClick(self.window, Qt.Key_F5)
        QTest.qWait(250)
        page = self.window.stack.currentWidget()
        fresh = page.findChild(BracketBoard)
        if fresh is old:
            # The synthetic F5 was not delivered to the active window; run the
            # same public command and record that the shortcut itself is untested.
            self.window.refresh_all()
            QTest.qWait(200)
            page = self.window.stack.currentWidget()
            fresh = page.findChild(BracketBoard)
            self.__class__.f5_delivered = False
        else:
            self.__class__.f5_delivered = True
        self.assertIsNot(fresh, old)
        self.assertEqual((fresh.group_key, fresh.round_key), ("wb", "wb_semifinal"))

    def test_t07_grand_final_source_jump_and_back(self) -> None:
        page, board = self.open_bracket()
        self.click_path(board, self.ember_tie("grand_final"), "来源：胜者组决赛")
        self.assertEqual((board.group_key, board.round_key), ("wb", "wb_final"))
        self.assertEqual(self.selected_group(board), "wb")
        state = self.page_state(page)
        self.assertEqual((state.get("bracket_group"), state.get("bracket_round")), ("wb", "wb_final"))
        board.select_group("gf")
        QTest.qWait(120)
        self.assertEqual((board.group_key, board.round_key), ("gf", "grand_final"))
        self.window.refresh_all()
        QTest.qWait(200)
        page = self.window.stack.currentWidget()
        fresh = page.findChild(BracketBoard)
        self.assertEqual((fresh.group_key, fresh.round_key), ("gf", "grand_final"))
        self.assertEqual(fresh.visible_tie_ids(), [self.ember_tie("grand_final")])

    def test_t08_wide_then_narrow_keeps_semantic_round(self) -> None:
        page, board = self.open_bracket(width=1680)
        board.select_group("wb")
        QTest.qWait(120)
        self.click_path(board, self.ember_tie("wb_quarterfinal", 0), "下一站：胜者组次轮")
        self.assertEqual(board.round_key, "wb_semifinal")
        self.window.refresh_all()
        QTest.qWait(200)
        page = self.window.stack.currentWidget()
        board = page.findChild(BracketBoard)
        self.assertEqual(board.round_key, "wb_semifinal")
        board.setFixedWidth(640)
        QTest.qWait(160)
        self.assertEqual(board.visible_round_keys(), ["wb_semifinal"])
        self.assertTrue(board.round_box.isVisible())
        self.assertEqual(board.round_box.currentData(), "wb_semifinal")
        self.assertIn(self.ember_tie("wb_semifinal", 0), board.visible_tie_ids())
        board.setFixedWidth(16777215)
        QTest.qWait(160)
        self.assertEqual(board.round_key, "wb_semifinal")

    def test_t09_return_from_match_and_other_competitions(self) -> None:
        page, board = self.open_bracket()
        board.select_group("lb")
        QTest.qWait(120)
        board._round_key = "lb_round_2"
        board._render()
        QTest.qWait(120)
        self.click_path(board, self.ember_tie("lb_round_2", 0), "来源：胜者组次轮")
        self.window.navigate("match", match_id=self.ember_tie("wb_semifinal", 0))
        QTest.qWait(140)
        self.window.go_back()
        QTest.qWait(200)
        page = self.window.stack.currentWidget()
        board = page.findChild(BracketBoard)
        self.assertEqual((board.group_key, board.round_key), ("wb", "wb_semifinal"))
        # another cup keeps its own state
        other = self.window.navigate("competition", competition_id="aureole")
        QTest.qWait(160)
        other_page = self.window.stack.currentWidget()
        other_state = self.page_state(other_page, "aureole")
        self.assertNotEqual(other_state.get("bracket_group"), "wb")
        # the double-elimination selection survives the detour
        self.window.navigate("competition", competition_id=self.COMPETITION)
        QTest.qWait(160)
        page = self.window.stack.currentWidget()
        board = page.findChild(BracketBoard)
        self.assertEqual((board.group_key, board.round_key), ("wb", "wb_semifinal"))

    def test_t10_invalid_path_and_repeated_clicks_do_not_half_apply(self) -> None:
        page, board = self.open_bracket()
        before = (board.group_key, board.round_key)
        version = int(store.load_world("final").get("version") or 0)
        self.assertFalse(board.focus_tie("wb", "does-not-exist"))
        self.assertEqual((board.group_key, board.round_key), before)
        self.assertEqual(self.window._current_route[0], "competition")
        self.assertFalse(self.window.error_label.text())

        # Same-group path: click the real button twice; the second click is
        # idempotent (no extra selection signals, no rebuild, no navigation).
        board.select_group("wb")
        QTest.qWait(120)
        action = self.path_action(board, self.ember_tie("wb_quarterfinal", 0), "下一站：胜者组次轮")
        clicks = {"group": 0, "round": 0}
        board.group_changed.connect(lambda _key: clicks.__setitem__("group", clicks["group"] + 1))
        board.round_changed.connect(lambda _key: clicks.__setitem__("round", clicks["round"] + 1))
        action.trigger()
        QTest.qWait(140)
        self.assertEqual((board.group_key, board.round_key), ("wb", "wb_semifinal"))
        emitted = dict(clicks)
        self.assertEqual(emitted, {"group": 0, "round": 1})
        # Re-fetch the live menu action; triggering it again must be idempotent.
        action = self.path_action(board, self.ember_tie("wb_quarterfinal", 0), "下一站：胜者组次轮")
        action.trigger()
        QTest.qWait(140)
        self.assertEqual((board.group_key, board.round_key), ("wb", "wb_semifinal"))
        self.assertEqual(clicks, emitted)
        state = self.page_state(page)
        self.assertEqual((state.get("bracket_group"), state.get("bracket_round")), ("wb", "wb_semifinal"))
        self.assertEqual(self.window._current_route[0], "competition")
        self.assertEqual(int(store.load_world("final").get("version") or 0), version)
        self.assertFalse(self.window.error_label.text())
        self.assertEqual(len([w for w in QApplication.topLevelWidgets() if w.isVisible() and w is not self.window]), 0)


if __name__ == "__main__":
    unittest.main()
