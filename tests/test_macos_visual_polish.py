"""macOS canvas inspection and responsive honors keep navigation/data intact."""

import unittest

from PySide6.QtCore import QPoint, QPointF, QEvent, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QSignalSpy, QTest

from tests.test_macos_identity_p2 import _Base
from football_simulator.app import queries
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.competition_boards import DoubleElimPanorama, GraphPanel
from football_simulator.ui.components import DataTable
from football_simulator.ui.presentation import bracket


@unittest.skipUnless(macos.enabled(), "macOS presentation only")
class VisualPolishTests(_Base):
    def panorama(self):
        detail = queries.competition_detail(self.world, "final", "ember")
        view = DoubleElimPanorama(self.world, bracket.cup_bracket(self.world, "ember", detail))
        view.show()
        self.addCleanup(view.close)
        self.addCleanup(view.deleteLater)
        QTest.qWait(40)
        return view

    def test_hover_traces_team_without_hiding_edges_or_navigating(self):
        view = self.panorama()
        match_spy, team_spy = QSignalSpy(view.match_requested), QSignalSpy(view.team_requested)
        first = view._nodes[0]
        # Offscreen platforms have no desktop pointer. Deliver the real Qt
        # MouseMove event to the painted canvas without relying on global focus.
        point = QPointF(first["x"] + 50, first["y"] + 20)
        QApplication.sendEvent(view, QMouseEvent(QEvent.MouseMove, point, point, Qt.NoButton, Qt.NoButton, Qt.NoModifier))
        QTest.qWait(60)
        self.assertEqual(view._active_team, first["team_rows"][0]["team_id"])
        view.grab()
        self.assertEqual(len(view._painted_polylines), 20)
        self.assertTrue(any(edge.get("team_id") == view._active_team for edge in view._edges))
        self.assertEqual((match_spy.count(), team_spy.count()), (0, 0))
        point = QPointF(5, 5)
        QApplication.sendEvent(view, QMouseEvent(QEvent.MouseMove, point, point, Qt.NoButton, Qt.NoButton, Qt.NoModifier))
        QTest.qWait(40)
        self.assertEqual(view._active_team, "")

    def test_keyboard_inspection_and_original_click_targets(self):
        view = self.panorama()
        match_spy, team_spy = QSignalSpy(view.match_requested), QSignalSpy(view.team_requested)
        QTest.keyClick(view, Qt.Key_Right)
        QTest.keyClick(view, Qt.Key_Down)
        node = view._nodes[1]
        self.assertEqual(view._active_node, node["id"])
        self.assertEqual(view._active_team, node["team_rows"][1]["team_id"])
        QTest.keyClick(view, Qt.Key_Return)
        self.assertEqual(match_spy.at(0)[0], node["match_id"])
        QTest.keyClick(view, Qt.Key_Escape)
        self.assertEqual(view._active_team, "")
        QTest.mouseClick(view, Qt.LeftButton, pos=QPoint(node["x"] + 50, node["y"] + 20))
        self.assertEqual(team_spy.at(0)[0], node["team_rows"][0]["team_id"])
        QTest.mouseClick(view, Qt.LeftButton, pos=QPoint(node["x"] + node["w"] - 18, node["y"] + 20))
        self.assertEqual(match_spy.at(1)[0], node["match_id"])

    def test_fit_and_zoom_keep_lower_bracket_in_page_scroll(self):
        view = self.panorama()
        panel = GraphPanel(view)
        panel.resize(1050, 800)
        panel.show()
        self.addCleanup(panel.close)
        self.addCleanup(panel.deleteLater)
        QTest.qWait(80)
        panel.fit()
        QTest.qWait(40)
        self.assertGreaterEqual(panel.scroll.height(), view.minimumHeight())
        self.assertEqual(panel.scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(panel.zoom_label.text(), f"{view.scale():.0%}")
        panel.zoom_in()
        self.assertGreaterEqual(panel.scroll.height(), view.minimumHeight())
        self.assertTrue(all(view.node_rect(key).bottom() < panel.scroll.height() for key in view.node_ids()))

    def test_honors_reflow_preserves_rows_without_horizontal_page_overflow(self):
        page = self.open("player", player_id="real::darwin-nunez")
        page = self.click_tab(page, "荣誉")
        panels = page.findChild(macos.AdaptivePanels)
        self.assertIsNotNone(panels)
        tables = panels.findChildren(DataTable)
        before = [list(table._rows) for table in tables]
        for width, wide in ((1680, True), (1280, False), (1440, True)):
            self.window.setFixedSize(width, 900)
            QTest.qWait(100)
            self.assertEqual(panels._wide, wide, (width, panels.width(), panels.minimumSizeHint()))
            self.assertEqual([list(table._rows) for table in tables], before)
            self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
        self.window.setFixedSize(1440, 900)


if __name__ == "__main__":
    unittest.main()
