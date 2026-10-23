"""macOS screenshot regressions: full-card navigation, mirrored matches and empty copy."""
from __future__ import annotations

import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QWidget

from football_simulator.app.service import GameService
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.components import EmptyState, EntityLink
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.pages.competitions import CompetitionsPage
from football_simulator.ui.pages.core import MatchesPage
from football_simulator.ui.pages.entities import TeamsPage
from tests import support


class UiFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="fs_macos_polish_"))
        support.install_test_data_root(str(cls.tmp))
        cls.qapp = support.ensure_qapp()
        cls.service = GameService(rng_factory=lambda: random.Random(20261021))
        cls.service.create_save("polish")
        cls.service.initialize_save("polish")
        cls.window = MainWindow(cls.service)
        cls.window.resize(1440, 900)
        cls.window.show()
        cls.window.open_save("polish")
        QTest.qWait(80)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.qapp.processEvents()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls.tmp))

    def open(self, route):
        self.window.navigate(route)
        QTest.qWait(70)
        self.assertFalse(self.window.error_label.text())
        return self.window.stack.currentWidget()


@unittest.skipUnless(sys.platform == "darwin", "macOS-only presentation")
class MacPresentationTests(UiFixture):
    def test_sidebar_content_fits_viewport_without_clipping(self):
        for size in ((1280, 800), (1440, 900), (1680, 980)):
            self.window.resize(*size)
            self.open("teams")
            scroll = self.window.sidebar_scroll
            self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())
            self.assertTrue(scroll.widget().rect().contains(self.window.nav.geometry()))
            self.assertTrue(scroll.widget().rect().contains(self.window.tools.geometry()))

    def test_team_column_removed_but_name_search_and_link_remain(self):
        page = self.open("teams")
        self.assertNotIn("英文名", [c["label"] for c in page.table._columns])
        original = page.table._rows[0]
        page.search.setText(original["club"])
        QTest.qWait(60)
        self.assertTrue(any(row["team_id"] == original["team_id"] for row in page.table._rows))
        col = next(i for i, c in enumerate(page.table._columns) if c["key"] == "team")
        self.assertFalse(page.table.item(0, col).icon().isNull())
        row = page.table._rows[0]
        QTest.mouseClick(page.table.viewport(), Qt.LeftButton, pos=page.table.visualItemRect(page.table.item(0, col)).center())
        QTest.qWait(40)
        self.assertEqual(self.window._current_route, ("team", {"team_id": row["team_id"]}))
        page.search.clear()

    def _click_card_region(self, region, cup_id="premier"):
        page = self.open("competitions")
        page.scroll.verticalScrollBar().setValue(0)
        card = next(c for c in page.findChildren(macos.NavigationCard) if c.property("competitionId") == cup_id)
        if region == "background":
            point = QPoint(card.width() - 20, card.height() // 2)
        elif region == "title":
            child = next(l for l in card.findChildren(QLabel) if l.objectName() == "CardTitle")
            point = child.mapTo(card, child.rect().center())
        elif region == "trophy":
            child = next(c for c in card.findChildren(QWidget) if c.property("assetReady") is not None)
            point = child.mapTo(card, child.rect().center())
        else:
            child = next(l for l in card.findChildren(QLabel) if l.text() == "查看赛事")
            point = child.mapTo(card, child.rect().center())
        with patch.object(self.window, "navigate", wraps=self.window.navigate) as navigate:
            QTest.mouseClick(self.window.windowHandle(), Qt.LeftButton, Qt.NoModifier, card.mapTo(self.window, point))
            QTest.qWait(40)
            self.assertEqual(navigate.call_count, 1)
        self.assertEqual(self.window._current_route, ("competition", {"competition_id": cup_id}))

    def test_entire_card_and_children_navigate_once(self):
        for region in ("background", "title", "trophy", "footer"):
            with self.subTest(region=region):
                self._click_card_region(region)
        self._click_card_region("background", "aureole")  # unopened cup still explains its rules

    def test_card_enter_and_space_keyboard_activation(self):
        for key in (Qt.Key_Return, Qt.Key_Space):
            page = self.open("competitions")
            card = page.findChildren(macos.NavigationCard)[0]
            self.assertEqual(card.focusPolicy(), Qt.StrongFocus)
            self.assertTrue(card.accessibleName())
            card.setFocus()
            with patch.object(self.window, "navigate", wraps=self.window.navigate) as navigate:
                QTest.keyClick(card, key)
                QTest.qWait(40)
                self.assertEqual(navigate.call_count, 1)
            self.assertEqual(self.window._current_route[0], "competition")

    def test_match_team_columns_are_mirrored_with_crests_and_centered_scores(self):
        for width, height in ((1280, 800), (1440, 900), (1680, 980)):
            self.window.resize(width, height)
            page = self.open("matches")
            table = page.table
            cols = {column["key"]: i for i, column in enumerate(table._columns)}
            home, score, away = (cols[k] for k in ("home_name", "score", "away_name"))
            self.assertLessEqual(abs(table.columnWidth(home) - table.columnWidth(away)), 1)
            self.assertEqual(table.item(0, score).textAlignment(), Qt.AlignCenter)
            self.assertEqual(table.itemDelegateForColumn(home).side, "home")
            self.assertEqual(table.itemDelegateForColumn(away).side, "away")
            for col in (home, away):
                self.assertFalse(table.item(0, col).icon().isNull())
                self.assertTrue(table.item(0, col).toolTip())
            row = table._rows[0]
            QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=table.visualItemRect(table.item(0, score)).center())
            QTest.qWait(40)
            self.assertEqual(self.window._current_route, ("match", {"match_id": row["match_id"]}))
        self.window.resize(1440, 900)

    def test_match_crest_cell_still_opens_correct_team(self):
        page = self.open("matches")
        col = next(i for i, c in enumerate(page.table._columns) if c["key"] == "away_name")
        row = page.table._rows[0]
        QTest.mouseClick(page.table.viewport(), Qt.LeftButton, pos=page.table.visualItemRect(page.table.item(0, col)).center())
        QTest.qWait(40)
        self.assertEqual(self.window._current_route, ("team", {"team_id": row["away_team_id"]}))

    def test_empty_copy_has_full_height_after_refresh_and_resize(self):
        for size in ((1280, 800), (1680, 980), (1440, 900), (1280, 800)):
            self.window.resize(*size)
            for route in ("transfers", "draft"):
                page = self.open(route)
                self.window.refresh_all()
                QTest.qWait(70)
                for empty in page.findChildren(EmptyState):
                    labels = empty.findChildren(QLabel)
                    for label in labels:
                        self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()), (route, size, label.text()))
                        self.assertTrue(empty.rect().contains(label.geometry()), label.text())
                    self.assertGreaterEqual(labels[1].y() - (labels[0].y() + labels[0].height()), 8)
                self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)


class OtherPlatformCompatibilityTests(UiFixture):
    def test_non_macos_keeps_existing_columns_cards_and_empty_labels(self):
        with patch.object(macos, "enabled", return_value=False):
            teams = TeamsPage(self.window)
            self.assertIn("英文名", [c["label"] for c in teams.table._columns])
            matches = MatchesPage(self.window)
            cols = {c["key"]: c for c in matches.table._columns}
            self.assertNotIn("team_side", cols["home_name"])
            self.assertNotIn("alignment", cols["score"])
            cups = CompetitionsPage(self.window)
            cups.refresh(self.service.snapshot("polish"), {})
            self.assertFalse(cups.findChildren(macos.NavigationCard))
            self.assertTrue(cups.findChildren(EntityLink))
            empty = EmptyState("标题", "说明")
            self.assertFalse(empty.findChildren(macos.EmptyStateText))
            for widget in (teams, matches, cups, empty):
                widget.close()
                widget.deleteLater()
