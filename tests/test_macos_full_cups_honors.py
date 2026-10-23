"""Saved-round coverage, unclipped graphs and readable player honor facts."""
import hashlib
import shutil
import tempfile
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QTabBar

from tests import support
from football_simulator.app import queries, records
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui.competition_boards import DoubleElimPanorama, GraphPanel, KnockoutPanorama, RedrawCupPanorama
from football_simulator.ui.components import DataTable
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.presentation import bracket
from football_simulator.ui.theme import apply_theme


class FullCupsHonorsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fs_full_cups_")
        support.install_test_data_root(cls.tmp)
        cls.app = support.ensure_qapp()
        apply_theme(cls.app)
        for save, fixture in (("final", "s3-finalized"), ("winter", "s2-week25")):
            store.import_package(support.PROJECT_ROOT / "tests/fixtures_vnext" / f"{fixture}.fsarchive", save)
        cls.window = MainWindow(GameService())
        cls.window.setFixedSize(1440, 900)
        cls.window.show()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.window.deleteLater()
        support.cleanup_qt_widgets()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls.tmp))

    def cup(self, save, cup):
        self.window.open_save(save)
        self.window.navigate("competition", competition_id=cup)
        page = self.window.stack.currentWidget()
        if cup == "ember":
            page.detail_state(cup)["bracket_view"] = "panorama"
            self.window.refresh_all()
        QTest.qWait(60)
        self.assertFalse(self.window.error_label.text(), self.window.error_label.text())
        return self.window.stack.currentWidget()

    def test_all_six_cups_keep_every_saved_round_and_final(self):
        for cup in ("aureole", "confluence", "starpath", "ember", "ascendant", "zenith"):
            page = self.cup("final", cup)
            world = self.window.world()
            model = bracket.cup_bracket(world, cup, queries.competition_detail(world, "final", cup))
            graph = next(panel.view for panel in page.findChildren(GraphPanel) if not panel.view._bucket_graph)
            columns = [rnd for group in model.get("groups") or [] for rnd in group["rounds"]] if cup == "ember" else model["columns"]
            expected = {node["tie_id"] for col in columns for node in col["nodes"]}
            self.assertEqual(set(graph.node_ids()), expected, cup)
            self.assertEqual(len(graph._headers), len(columns), cup)
            self.assertEqual(len(graph._edges), len(model.get("edges", model.get("links", []))), cup)
            self.assertTrue(model["champion_id"], cup)
            self.assertTrue(any(model["champion_id"] == team.get("team_id") for node in graph._nodes for team in node["team_rows"]), cup)

    def test_double_elimination_bottom_is_reachable_at_all_zoom_levels(self):
        page = self.cup("final", "ember")
        panel = next(p for p in page.findChildren(GraphPanel) if isinstance(p.view, DoubleElimPanorama))
        for scale in (.45, 1, 1.8):
            panel.view.apply_scale(scale)
            panel._sync_height()
            QTest.qWait(30)
            self.assertGreaterEqual(panel.scroll.height(), panel.view.height())
            self.assertEqual(panel.scroll.verticalScrollBar().maximum(), 0)
            bottom = max(node["y"] + node["h"] for node in panel.view._nodes) * panel.view.scale()
            self.assertLessEqual(bottom, panel.scroll.height())

    def test_generic_full_view_uses_saved_two_leg_scores_and_leg_links(self):
        page = self.cup("final", "aureole")
        graph = page.findChild(KnockoutPanorama)
        self.assertIsNotNone(graph)
        panel = next(panel for panel in page.findChildren(GraphPanel) if panel.view is graph)
        self.assertNotIn("落位", panel.hint.text())
        world = self.window.world()
        fixtures = {f["match_id"]: f for f in world["season"]["fixtures"]}
        for node in graph._nodes:
            self.assertEqual(len(node["legs"]), 2)
            for leg in node["legs"]:
                raw = fixtures[leg["match_id"]]
                self.assertEqual(leg["scores"][raw["home_team_id"]], int(raw.get("home_goals") or 0) + int(raw.get("home_goals_et") or 0))
        node = graph._nodes[0]
        got = []
        graph.match_requested.connect(got.append)
        for index, leg in enumerate(node["legs"]):
            left = node["x"] + node["w"] - 1 - 12 - 154
            point = QPoint(int((left + index * 54 + 23) * graph.scale()), int((node["y"] + 18) * graph.scale()))
            QTest.mouseClick(graph, Qt.LeftButton, pos=point)
            self.assertEqual(got[-1], leg["match_id"])

    def test_late_round_header_round_view_and_f5_keep_target(self):
        page = self.cup("final", "starpath")
        graph = page.findChild(KnockoutPanorama)
        final = graph._headers[-1]
        graph.stage_requested.emit(final["stage"])
        QTest.qWait(40)
        from football_simulator.ui.components import BracketBoard
        board = self.window.stack.currentWidget().findChild(BracketBoard)
        self.assertEqual(board.round_key, final["stage"])
        QTest.keyClick(self.window, Qt.Key_F5)
        QTest.qWait(40)
        board = self.window.stack.currentWidget().findChild(BracketBoard)
        self.assertEqual(board.round_key, final["stage"])

    def test_player_honors_use_readable_tables_without_calculation_or_duplicates(self):
        self.window.open_save("final")
        pid = "real::darwin-nunez"
        self.window.navigate("player_honors", player_id=pid)
        QTest.qWait(60)
        page = self.window.stack.currentWidget()
        self.assertFalse(self.window.error_label.text(), self.window.error_label.text())
        data = records.player_honors(self.window.world(), pid)
        awards = page.findChild(DataTable, "PlayerHonorAwards")
        annual = page.findChild(DataTable, "PlayerHonorAnnual")
        self.assertIsNotNone(awards)
        self.assertEqual(len(awards._rows), min(10, len(data["awards"])))
        self.assertEqual(len(annual._rows), len(records.annual_rank_rows(self.window.world(), pid)))
        self.assertTrue(all("score" not in row and "value" not in row for row in annual._rows))
        self.assertEqual(len({row["season"] for row in annual._rows}), len(annual._rows))
        for text in page.findChildren(QLabel):
            if text.text():
                text.ensurePolished()
                self.assertGreaterEqual(text.fontMetrics().height(), 18, (text.objectName(), text.text(), text.font().pixelSize()))

    def test_narrow_windows_have_all_rounds_without_outer_horizontal_overflow(self):
        for width in (1280, 1440, 1680):
            self.window.setFixedSize(width, 900)
            for cup in ("aureole", "starpath", "ascendant", "zenith"):
                page = self.cup("final", cup)
                page.detail_state(cup)["bracket_view"] = "panorama"
                self.window.refresh_all()
                QTest.qWait(30)
                page = self.window.stack.currentWidget()
                graph = page.findChild(KnockoutPanorama)
                self.assertEqual(len(graph._headers), len(graph.model["columns"]))
                self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
            self.window.navigate("player_honors", player_id="real::darwin-nunez")
            QTest.qWait(40)
            page = self.window.stack.currentWidget()
            self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0, width)
