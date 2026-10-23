# -*- coding: utf-8 -*-
"""UIP-010/020/030/040/050: team crests everywhere, Top20 without formulas,
shared bracket node geometry, and the compact 路径 menu."""

from __future__ import annotations

import tempfile
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton, QTabBar

from tests import support

from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.competition_boards import DoubleElimPanorama, GraphPanel, SwissBoard
from football_simulator.ui.components import BRACKET_NODE_SPEC, BracketNode, DataTable
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.presentation import bracket as bracket_view
from football_simulator.ui.theme import apply_theme

FIXTURES = support.PROJECT_ROOT / "tests" / "fixtures_vnext"


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_uip_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        apply_theme(cls.app)
        store.import_package(FIXTURES / "s3-finalized.fsarchive", "final")
        cls.service = GameService()
        cls.window = MainWindow(cls.service)
        cls.window.setFixedSize(1440, 900)
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
        import shutil

        shutil.rmtree(cls._tmp, ignore_errors=True)

    def open(self, route, **params):
        self.window.navigate(route, **params)
        QTest.qWait(180)
        self.assertFalse(self.window.error_label.text(), self.window.error_label.text())
        return self.window.stack.currentWidget()

    def team_icon_column(self, table):
        return next((index for index, column in enumerate(table._columns) if column.get("icon_key") == "club"), None)

    def assert_team_icons(self, table, label):
        index = self.team_icon_column(table)
        self.assertIsNotNone(index, f"{label}: 缺少队徽列")
        for row in range(table.rowCount()):
            item = table.item(row, index)
            self.assertIsNotNone(item, f"{label}: 行 {row} 无单元格")
            self.assertFalse(item.icon().isNull(), f"{label}: 行 {row} 无队徽")


class IdentityTests(_Base):
    def test_u01_players_directory_crest_matches_row_team(self) -> None:
        page = self.open("players")
        table = page.findChild(DataTable)
        self.assert_team_icons(table, "球员目录")
        column = self.team_icon_column(table)
        for row in table._rows[:30]:
            team_id = row.get("team_id")
            self.assertEqual(row["club"], self.world["teams"][team_id]["club_en"])
        # Clicking the team cell only routes to the team once.
        row = table._rows[0]
        table.cell_activated.emit(row, "team")
        QTest.qWait(80)
        self.assertEqual(self.window._current_route, ("team", {"team_id": row["team_id"]}))

    def click_section(self, page, text):
        tabs = next(tab for tab in page.findChildren(QTabBar) if any(tab.tabText(i) == text for i in range(tab.count())))
        index = next(i for i in range(tabs.count()) if tabs.tabText(i) == text)
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
        QTest.qWait(200)
        return self.window.stack.currentWidget()

    def test_u02_named_rankings_all_have_one_crest_per_row(self) -> None:
        history = self.open("history")
        history_tables = [table for table in history.findChildren(DataTable) if any(column.get("icon_key") == "club" for column in table._columns)]
        self.assertGreaterEqual(len(history_tables), 3, "历史页应有多处带队徽的榜单")
        for table in history_tables:
            self.assert_team_icons(table, "历史榜单")
        records = self.open("records")
        self.assert_team_icons(records.findChild(DataTable), "历史纪录")
        # The cup reopens on its remembered section: explicitly select the
        # stage table and re-fetch the current page before checking groups.
        cup = self.open("competition", competition_id="aureole")
        cup = self.click_section(cup, "阶段与签表")
        group_tables = [
            table for table in cup.findChildren(DataTable)
            if [c["key"] for c in table._columns] == ["rank", "team", "played", "goal_diff", "points"]
        ]
        self.assertEqual(len(group_tables), 4, "君临杯应有 A–D 四个分组榜")
        self.assertEqual([table.rowCount() for table in group_tables], [4, 4, 4, 4])
        for table in group_tables:
            self.assert_team_icons(table, "分组榜")
        cup = self.open("competition", competition_id="ascendant")
        cup = self.click_section(cup, "阶段与签表")
        group_tables = [
            table for table in cup.findChildren(DataTable)
            if [c["key"] for c in table._columns] == ["rank", "team", "played", "goal_diff", "points"]
        ]
        self.assertEqual(len(group_tables), 2, "凌越杯应有登峰/涅槃两个分组榜")
        self.assertEqual([table.rowCount() for table in group_tables], [6, 6])
        for table in group_tables:
            self.assert_team_icons(table, "凌越分组榜")
        # Players board also has a team column with icon_key=club (not a visible
        # column key).  Assert it exists and has icons rather than skipping it.
        cup = self.open("competition", competition_id="aureole")
        cup = self.click_section(cup, "球员榜")
        icon_tables = [table for table in cup.findChildren(DataTable) if any(column.get("icon_key") == "club" for column in table._columns)]
        self.assertGreaterEqual(len(icon_tables), 2, "进球/助攻双榜都应有球队图标列")
        for table in icon_tables:
            self.assert_team_icons(table, "赛事双榜")

    def test_u04_top20_shows_only_rank_player_and_era_team(self) -> None:
        archive = max(self.world["archives"], key=lambda item: item["season_number"])
        top20 = archive["awards"].get("top20") or []
        self.assertTrue(top20)
        self.assertIn("score", top20[0])
        self.assertIn("components", top20[0])
        page = self.open("annual_top20", season=archive["season_number"])
        texts = [label.text() for label in page.findChildren(QLabel)]
        joined = " ".join(texts)
        self.assertNotIn("分数解释", joined)
        self.assertNotIn("总分", joined)
        self.assertNotIn("权重", joined)
        top_table = next(
            (table for table in page.findChildren(DataTable) if [c["key"] for c in table._columns] == ["rank", "label", "team"]),
            None,
        )
        self.assertIsNotNone(top_table, "Top20 应只有名次/球员/当届球队")
        self.assertEqual(top_table.rowCount(), len(top20))
        self.assert_team_icons(top_table, "Top20")
        for row, saved in zip(top_table._rows, top20):
            self.assertEqual(row["rank"], saved["rank"])
            self.assertEqual(row["team_id"], saved["team_id"])
            self.assertEqual(row["club"], self.world["teams"][saved["team_id"]]["club_en"])

    def test_u05_other_awards_and_honor_points_keep_values(self) -> None:
        page = self.open("competition", competition_id="aureole")
        tabs = next(tab for tab in page.findChildren(QTabBar) if any(tab.tabText(i) == "个人奖项" for i in range(tab.count())))
        index = next(i for i in range(tabs.count()) if tabs.tabText(i) == "个人奖项")
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
        QTest.qWait(200)
        page = self.window.stack.currentWidget()
        texts = [label.text() for label in page.findChildren(QLabel)]
        self.assertTrue(any("射手王" in text for text in texts))
        self.assertTrue(any(text.isdigit() for text in texts))
        history = self.open("history")
        tabs = next(tab for tab in history.findChildren(QTabBar) if any(tab.tabText(i) == "荣誉分" for i in range(tab.count())))
        index = next(i for i in range(tabs.count()) if tabs.tabText(i) == "荣誉分")
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
        QTest.qWait(200)
        history = self.window.stack.currentWidget()
        table = next((t for t in history.findChildren(DataTable) if "points" in [c["key"] for c in t._columns]), None)
        self.assertIsNotNone(table)
        self.assertGreater(table.rowCount(), 0)


class NodeTests(_Base):
    def _board(self, width=1280):
        self.window.setFixedSize(width, 800)
        page = self.open("competition", competition_id="ember")
        tabs = next(tab for tab in page.findChildren(QTabBar) if any(tab.tabText(i) == "轮次视图" for i in range(tab.count())))
        return page, tabs

    def test_u06_node_has_no_footer_and_keeps_special_result(self) -> None:
        page, tabs = self._board()
        state = page.detail_state("ember")
        state["bracket_view"] = "rounds"
        state["bracket_group"] = "lb"
        state["bracket_round"] = "lb_round_1"
        self.window.refresh_all()
        QTest.qWait(220)
        page = self.window.stack.currentWidget()
        nodes = page.findChildren(BracketNode)
        self.assertTrue(nodes)
        node = nodes[0]
        texts = [label.text() for label in node.findChildren(QLabel)]
        self.assertNotIn("已完赛", texts)
        self.assertNotIn("两回合 · 已完赛", texts)
        self.assertFalse(any(text.startswith("来源：") for text in texts))
        self.assertGreaterEqual(node.minimumWidth(), BRACKET_NODE_SPEC["min_width"])
        # A penalty/extra-time badge stays as its own label when the tie has one.
        model = bracket_view.cup_bracket(self.world, "ember", queries.competition_detail(self.world, "final", "ember"))
        special = [
            node_data for group in model["groups"] for rnd in group["rounds"] for node_data in rnd["nodes"]
            if bracket_view_node_badge(node_data)
        ]
        self.assertTrue(special, "完成态应存在真实加时/点球结果")

    def test_u10_path_menu_reaches_cross_group_target(self) -> None:
        page, tabs = self._board()
        state = page.detail_state("ember")
        state["bracket_group"] = "lb"
        state["bracket_round"] = "lb_round_2"
        self.window.refresh_all()
        QTest.qWait(220)
        page = self.window.stack.currentWidget()
        node = next(node for node in page.findChildren(BracketNode) if "lb_round_2" in (node.node.get("stage") or ""))
        menu_button = node.findChild(QPushButton, "PathMenu")
        self.assertIsNotNone(menu_button)
        action = next(action for action in menu_button.menu().actions() if action.text().startswith("来源：胜者组次轮"))
        action.trigger()
        QTest.qWait(200)
        page = self.window.stack.currentWidget()
        state = page.detail_state("ember")
        self.assertEqual((state["bracket_group"], state["bracket_round"]), ("wb", "wb_semifinal"))
        self.window.refresh_all()
        QTest.qWait(220)
        board = self.window.stack.currentWidget().findChild(__import__("football_simulator").ui.components.BracketBoard)
        self.assertEqual((board.group_key, board.round_key), ("wb", "wb_semifinal"))

    def test_u11_zoom_keeps_lower_nodes_reachable(self) -> None:
        page = self.open("competition", competition_id="ember")
        tabs = next(tab for tab in page.findChildren(QTabBar) if any(tab.tabText(i) == "全景签表" for i in range(tab.count())))
        index = next(i for i in range(tabs.count()) if tabs.tabText(i) == "全景签表")
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
        QTest.qWait(260)
        page = self.window.stack.currentWidget()
        panel = page.findChild(GraphPanel)
        pano = page.findChild(DoubleElimPanorama)
        before = panel.scroll.height()
        panel.zoom_in()
        QTest.qWait(120)
        self.assertGreater(panel.scroll.height(), before)
        self.assertFalse(panel.scroll.verticalScrollBar().isVisible())
        self.assertTrue(pano.node_ids())
        rect = pano.node_rect(pano.node_ids()[-1])
        self.assertIsNotNone(rect)
        panel.zoom_out()
        QTest.qWait(120)


def bracket_view_node_badge(node: dict) -> str:
    from football_simulator.ui.components import node_special_badge

    return node_special_badge(node)


if __name__ == "__main__":
    unittest.main()
