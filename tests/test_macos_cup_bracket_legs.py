"""Complete redraw bracket and leg-by-leg cards; saved fixtures are the oracle."""
from __future__ import annotations

import shutil
import tempfile
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton

from tests import support
from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui.competition_boards import GraphPanel, RedrawCupPanorama
from football_simulator.ui.components import BracketNode
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.presentation import bracket


class CupBracketLegTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fs_cup_legs_")
        support.install_test_data_root(cls.tmp)
        cls.app = support.ensure_qapp()
        for save, filename in (("final", "s3-finalized"), ("winter", "s2-week25")):
            store.import_package(support.PROJECT_ROOT / "tests/fixtures_vnext" / f"{filename}.fsarchive", save)
        cls.service = GameService()
        cls.window = MainWindow(cls.service)
        cls.window.show()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.window.deleteLater()
        support.cleanup_qt_widgets()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls.tmp))

    def model(self, save, competition):
        world = store.load_world(save, readonly=True)
        detail = queries.competition_detail(world, save, competition)
        return world, bracket.cup_bracket(world, competition, detail)

    def test_destiny_all_five_rounds_and_31_real_matches(self):
        world, model = self.model("final", "confluence")
        self.assertEqual([column["key"] for column in model["columns"]], list(bracket.STAGE_SEQUENCE["confluence"]))
        view = RedrawCupPanorama(world, model)
        self.assertEqual(len(view._nodes), 31)
        self.assertEqual(len(view._headers), 5)
        fixtures = [f for f in world["season"]["fixtures"] if f["competition"] == "confluence"]
        self.assertEqual({node["match_id"] for node in view._nodes}, {f["match_id"] for f in fixtures})
        self.assertEqual(len(view._edges), 30)
        by_id = {node["id"]: node for node in view._nodes}
        for edge in view._edges:
            self.assertIn(edge["team_id"], [row["team_id"] for row in by_id[edge["to"]]["team_rows"]])
        panel = GraphPanel(view)
        panel.resize(980, 800)
        panel.show()
        panel.fit()
        self.assertGreaterEqual(panel.scroll.height(), view.minimumSize().height())
        panel.close()

    def test_destiny_undrawn_slots_have_no_opponents_or_match_links(self):
        world, model = self.model("winter", "confluence")
        view = RedrawCupPanorama(world, model)
        self.assertEqual(len(view._nodes), 31)
        pending = [node for node in view._nodes if node["pending"]]
        self.assertTrue(pending)
        for node in pending:
            self.assertIsNone(node["match_id"])
            self.assertTrue(all(row["team_id"] is None for row in node["team_rows"]))
            self.assertTrue(all(row["text"] == "待抽签" for row in node["rows"]))
        self.assertTrue(all(edge["to"] not in {node["id"] for node in pending} for edge in view._edges))
        view.deleteLater()

    def test_reign_leg_scores_and_aggregate_match_raw_fixtures(self):
        world, model = self.model("final", "aureole")
        fixtures = {f["match_id"]: f for f in world["season"]["fixtures"]}
        for column in model["columns"]:
            for node in column["nodes"]:
                self.assertEqual(len(node["legs"]), 2)
                totals = {tid: 0 for tid in node["team_ids"]}
                for leg in node["legs"]:
                    raw = fixtures[leg["match_id"]]
                    for side in ("home", "away"):
                        tid = raw[f"{side}_team_id"]
                        value = int(raw.get(f"{side}_goals") or 0) + int(raw.get(f"{side}_goals_et") or 0)
                        self.assertEqual(leg["scores"][tid], value)
                        totals[tid] += value
                self.assertEqual({team["team_id"]: team["score"] for team in node["teams"]}, totals)
                card = BracketNode(world, node)
                for cell in card.findChildren(QLabel, "BracketLegScore"):
                    self.assertEqual(cell.text(), str(node["legs"][cell.property("leg") - 1]["scores"][cell.property("teamId")]))
                self.assertEqual(card.findChild(QLabel, "BracketAggregateHeading").text(), "总比分")
                card.deleteLater()

    def test_partial_two_leg_does_not_fabricate_zeros_and_pk_is_separate(self):
        tie = {"tie_id": "example", "team_ids": ["a", "b"], "teams": ["A", "B"], "two_leg": True,
               "legs": [{"match_id": "one", "leg": 1, "home_team_id": "a", "away_team_id": "b", "status": "scheduled"},
                        {"match_id": "two", "leg": 2, "home_team_id": "b", "away_team_id": "a", "status": "scheduled"}]}
        node = bracket._node({}, tie)
        self.assertTrue(all(team["score"] is None for team in node["teams"]))
        tie["legs"][0].update(status="completed", home_goals=2, away_goals=1)
        node = bracket._node({}, tie)
        self.assertEqual([team["score"] for team in node["teams"]], [2, 1])
        self.assertEqual(node["legs"][1]["scores"], {})
        card = BracketNode({}, node)
        self.assertEqual(card.findChild(QLabel, "BracketAggregateHeading").text(), "暂合计")
        self.assertEqual([cell.text() for cell in card.findChildren(QLabel, "BracketLegScore")], ["2", "—", "1", "—"])
        card.deleteLater()
        tie["legs"][1].update(status="completed", home_goals=2, away_goals=1, home_goals_et=1, away_goals_et=1, home_pk=4, away_pk=5)
        node = bracket._node({}, tie)
        self.assertEqual([team["score"] for team in node["teams"]], [4, 4])
        self.assertEqual(node["status_text"], "点球 5–4")

    def test_leg_links_open_each_match_and_f5_preserves_bracket(self):
        self.window.open_save("final")
        self.window.navigate("competition", competition_id="aureole")
        page = self.window.stack.currentWidget()
        page.detail_state("aureole")["bracket_view"] = "rounds"
        page.set_detail_section("aureole:3", "overview")
        QTest.qWait(80)
        page = self.window.stack.currentWidget()
        cards = page.findChildren(BracketNode)
        self.assertTrue(cards)
        for number in (1, 2):
            page = self.window.stack.currentWidget()
            card = page.findChildren(BracketNode)[0]
            match_id = card.node["legs"][number - 1]["match_id"]
            button = card.findChild(QPushButton, f"BracketLeg{number}")
            page.scroll.ensureWidgetVisible(button)
            QTest.mouseClick(button, Qt.LeftButton)
            QTest.qWait(60)
            self.assertEqual(self.window._current_route, ("match", {"match_id": match_id}))
            self.window.go_back()
            QTest.qWait(60)
        QTest.keyClick(self.window, Qt.Key_F5)
        QTest.qWait(60)
        self.assertTrue(self.window.stack.currentWidget().findChildren(BracketNode))
