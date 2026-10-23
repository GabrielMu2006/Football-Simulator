"""Lifetime honor totals use frozen seasons once, with a navigable macOS board."""
import copy
import shutil
import tempfile
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton, QTabBar

from tests import support
from football_simulator.app import records
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.pages.honors import TeamHonorLeaderboard
from football_simulator.ui.theme import apply_theme


class HonorTotalDataTests(unittest.TestCase):
    def world(self):
        return {"teams": {"a": {"club_en": "A"}, "b": {"club_en": "B"}, "c": {"club_en": "C"}},
                "season_number": 2, "season": {"finalized": True, "honor_totals": {"a": 999}},
                "archives": [{"season_number": 1, "honor_totals": {"a": 60, "b": 30}},
                             {"season_number": 2, "honor_totals": {"a": 40, "b": 70}},
                             {"season_number": 2, "honor_totals": {"a": 40, "b": 70}}]}

    def test_archived_current_and_repeated_archives_are_not_counted_twice(self):
        world = self.world()
        before = copy.deepcopy(world)
        result = records.cumulative_team_honors(world)
        self.assertEqual(result["seasons"], [1, 2])
        self.assertEqual([(row["team_id"], row["points"], row["rank"]) for row in result["rows"]], [("a", 100, 1), ("b", 100, 1), ("c", 0, 3)])
        self.assertEqual(world, before)

    def test_newly_finalized_season_enters_once_and_unsettled_current_is_excluded(self):
        world = self.world()
        world["season_number"] = 3
        world["season"].update(finalized=False, honor_totals={"a": 20, "b": 30})
        self.assertEqual(records.cumulative_team_honors(world)["seasons"], [1, 2])
        world["season"]["finalized"] = True
        expected = records.cumulative_team_honors(world)
        self.assertEqual([(row["team_id"], row["points"]) for row in expected["rows"]], [("b", 130), ("a", 120), ("c", 0)])
        world["archives"].append({"season_number": 3, "honor_totals": dict(world["season"]["honor_totals"])})
        self.assertEqual(records.cumulative_team_honors(world), expected)
        world["season_number"] = 4
        world["season"] = {"finalized": False, "honor_totals": {}}
        self.assertEqual(records.cumulative_team_honors(world)["rows"], expected["rows"])

    def test_no_settled_seasons_has_zero_totals_and_no_invented_rank(self):
        world = self.world()
        world.update(archives=[], season_number=1, season={"finalized": False})
        result = records.cumulative_team_honors(world)
        self.assertEqual(result["seasons"], [])
        self.assertTrue(all(row["points"] == 0 and row["rank"] is None for row in result["rows"]))


class HonorTotalUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fs_honor_total_tests_")
        support.install_test_data_root(cls.tmp)
        cls.app = support.ensure_qapp()
        apply_theme(cls.app)
        store.import_package(support.PROJECT_ROOT / "tests/fixtures_vnext/s3-finalized.fsarchive", "final")
        cls.window = MainWindow(GameService())
        cls.window.setFixedSize(1440, 900)
        cls.window.show()
        cls.window.open_save("final")

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.window.deleteLater()
        support.cleanup_qt_widgets()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls.tmp))

    def test_real_three_season_totals_all_40_teams_and_crests(self):
        before = store.load_world("final", readonly=True)
        self.window.navigate("honors")
        QTest.qWait(80)
        page = self.window.stack.currentWidget()
        self.assertFalse(self.window.error_label.text(), self.window.error_label.text())
        board = page.findChild(TeamHonorLeaderboard)
        by_season = {a["season_number"]: a["honor_totals"] for a in before["archives"]}
        if before["season"]["finalized"] and before["season_number"] not in by_season:
            by_season[before["season_number"]] = before["season"]["honor_totals"]
        self.assertEqual(len(board.data["rows"]), 40)
        for row in board.data["rows"]:
            self.assertEqual(row["points"], sum(totals.get(row["team_id"], 0) for totals in by_season.values()))
        for row in range(board.table.rowCount()):
            self.assertFalse(board.table.item(row, 1).icon().isNull())
        self.assertEqual(store.load_world("final", readonly=True), before)

    def test_page_two_team_navigation_and_f5_preserve_page_and_total(self):
        self.window.navigate("honors")
        QTest.qWait(50)
        page = self.window.stack.currentWidget()
        board = page.findChild(TeamHonorLeaderboard)
        next_button = board.findChild(QPushButton, "PagerNext")
        page.scroll.ensureWidgetVisible(next_button)
        QTest.mouseClick(next_button, Qt.LeftButton)
        QTest.qWait(30)
        ids = [row["team_id"] for row in board.table._rows]
        first = board.table._rows[0]
        page.scroll.ensureWidgetVisible(board.table)
        QTest.mouseClick(board.table.viewport(), Qt.LeftButton, pos=board.table.visualItemRect(board.table.item(0, 1)).center())
        QTest.qWait(50)
        self.assertEqual(self.window._current_route, ("team_honors", {"team_id": first["team_id"]}))
        texts = [label.text() for label in self.window.stack.currentWidget().findChildren(QLabel)]
        self.assertIn(str(first["points"]), texts)
        self.window.go_back()
        QTest.keyClick(self.window, Qt.Key_F5)
        QTest.qWait(50)
        board = self.window.stack.currentWidget().findChild(TeamHonorLeaderboard)
        self.assertEqual([row["team_id"] for row in board.table._rows], ids)

    def test_history_has_total_board_and_keeps_single_season_board(self):
        self.window.navigate("history")
        QTest.qWait(50)
        page = self.window.stack.currentWidget()
        tabs = next(tabs for tabs in page.findChildren(QTabBar) if any(tabs.tabText(i) == "总荣誉榜" for i in range(tabs.count())))
        index = next(i for i in range(tabs.count()) if tabs.tabText(i) == "总荣誉榜")
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
        QTest.qWait(50)
        self.assertIsNotNone(self.window.stack.currentWidget().findChild(TeamHonorLeaderboard))
        page = self.window.stack.currentWidget()
        tabs = next(tabs for tabs in page.findChildren(QTabBar) if any(tabs.tabText(i) == "荣誉分" for i in range(tabs.count())))
        index = next(i for i in range(tabs.count()) if tabs.tabText(i) == "荣誉分")
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
        QTest.qWait(50)
        self.assertIsNone(self.window.stack.currentWidget().findChild(TeamHonorLeaderboard))
        self.assertFalse(self.window.error_label.text())
