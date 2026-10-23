"""macOS player scopes, league zones and team-perspective match outcomes."""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QTabBar

from football_simulator.app import queries, records
from football_simulator.app.service import GameService
from football_simulator.ui import macos_presentation as macos, tokens as t
from football_simulator.ui.components import DataTable, MatchRowCard
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.pages.entities import PlayersPage
from football_simulator.ui.pages.honors import RecordsPage
from tests import support


@unittest.skipUnless(sys.platform == "darwin", "macOS presentation")
class ObserverViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="fs_macos_observer_"))
        support.install_test_data_root(str(cls.tmp))
        cls.qapp = support.ensure_qapp()
        cls.service = GameService()
        cls.service.import_save(Path(__file__).parent / "fixtures_vnext/s3-finalized.fsarchive", "views")
        cls.window = MainWindow(cls.service)
        cls.window.resize(1440, 900)
        cls.window.show()
        cls.window.open_save("views")
        QTest.qWait(60)
        cls.world = cls.window.world()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.qapp.processEvents()
        support.reset_test_data_root()
        shutil.rmtree(support.assert_cleanup_path_is_temporary(cls.tmp))

    def open(self, route, **params):
        self.window.navigate(route, **params)
        QTest.qWait(40)
        self.assertFalse(self.window.error_label.text())
        return self.window.stack.currentWidget()

    def real_rows(self, tables):
        rows = [row for table in tables for row in table._rows if "player_id" in row]
        self.assertTrue(rows)
        self.assertTrue(all(self.world["players"][row["player_id"]]["is_real"] for row in rows))

    def test_player_directory_default_and_explicit_all_survive_refresh(self):
        page = self.open("players")
        page.kind.setCurrentIndex(page.kind.findData("real"))
        self.real_rows([page.table])
        real_count = page.table.rowCount()
        page.kind.setCurrentIndex(page.kind.findData("all"))
        self.assertGreater(page.table.rowCount(), real_count)
        self.window.refresh_all()
        self.assertEqual(page.kind.currentData(), "all")
        page.kind.setCurrentIndex(page.kind.findData("real"))

    def test_new_pages_default_real_only_and_windows_keep_all(self):
        for enabled, scope in ((True, "real"), (False, "all")):
            with patch.object(macos, "enabled", return_value=enabled):
                players = PlayersPage(self.window)
                record_page = RecordsPage(self.window)
                self.assertEqual(players.kind.currentData(), scope)
                self.assertEqual(record_page.player_scope, scope)
                table = DataTable()
                table.set_league_style("premier")
                self.assertEqual(table._league_style, "premier" if enabled else "")
                players.deleteLater()
                record_page.deleteLater()
                table.deleteLater()

    def test_team_roster_default_all_toggle_and_back(self):
        team = next(iter(self.world["teams"]))
        page = self.open("team", team_id=team)
        page.set_detail_section(team, "squad")
        page.set_player_scope("real")
        self.real_rows(page.findChildren(DataTable))
        count = len(page.findChildren(DataTable)[0]._rows)
        box = page.findChild(QComboBox, "PlayerScope")
        box.setCurrentIndex(box.findData("all"))
        self.assertGreater(page.findChildren(DataTable)[0].rowCount(), count)
        self.open("teams")
        self.window.go_back()
        QTest.qWait(40)
        self.assertEqual(page.player_scope, "all")
        page.set_player_scope("real")

    def test_match_players_filter_without_hiding_match_events(self):
        fixture = next(m for m in self.world["season"]["fixtures"] if m["status"] == "completed" and m.get("player_facts"))
        page = self.open("match", match_id=fixture["match_id"])
        # 比赛详情改为“事件 / 球员表现 / 对阵信息”分区，球员表在“球员表现”分区。
        page._set_section("players")
        QTest.qWait(30)
        page.set_player_scope("real")
        self.real_rows(page.findChildren(DataTable))
        actual = queries.match_detail(self.world, "views", fixture["match_id"])
        self.assertEqual(len(actual["events"]), len(fixture["events"]))
        page.set_player_scope("all")
        rows = [row for table in page.findChildren(DataTable) for row in table._rows]
        self.assertTrue(any(not self.world["players"][row["player_id"]]["is_real"] for row in rows))
        self.window.refresh_all()
        self.assertEqual(page.player_scope, "all")
        page.set_player_scope("real")

    def test_records_cumulative_edition_and_historical_default_real(self):
        page = self.open("records")
        page.player_scope = "real"
        page._reload()
        self.real_rows(page.findChildren(DataTable))
        expected = records.leaderboards("views", player_scope="real", metric="goals", page_size=30, include_in_progress=False)
        self.assertEqual([r["player_id"] for r in page.findChildren(DataTable)[0]._rows], [r["player_id"] for r in expected["rows"]])
        page.real_box.setCurrentIndex(page.real_box.findData("all"))
        self.window.refresh_all()
        self.assertEqual(page.player_scope, "all")
        page.real_box.setCurrentIndex(page.real_box.findData("real"))
        page.scope_box.setCurrentIndex(page.scope_box.findData("edition"))
        self.real_rows(page.findChildren(DataTable))
        archived = self.open("history_competition", competition_id="premier", season=2)
        tabs = archived.findChild(QTabBar, "DetailSections")
        index = next(i for i in range(tabs.count()) if tabs.tabText(i) == "球员榜")
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
        QTest.qWait(40)
        self.real_rows(archived.findChildren(DataTable))

    def test_current_compact_and_archived_tables_have_correct_zones(self):
        for league in ("premier", "second"):
            page = self.open("competition", competition_id=league)
            table = next(table for table in page.findChildren(DataTable) if table._league_style)
            self.assertEqual(table._league_style, league)
            self.assertEqual(table.rowCount(), 20)
            for i, row in enumerate(table._rows):
                color, _ = macos.standings_band(league, row["rank"])
                actual = table.item(i, 0).background().color().name() if table.item(i, 0).background().style() != Qt.NoBrush else None
                self.assertEqual(actual, color.lower() if color else None)
                if color:
                    shot = table.viewport().grab().toImage()
                    rect = table.visualItemRect(table.item(i, 1))
                    scale = shot.devicePixelRatio()
                    pixel = shot.pixelColor(int((rect.right() - 12) * scale), int(rect.center().y() * scale)).name()
                    self.assertEqual(pixel, color.lower(), "zone must be painted, not merely stored in BackgroundRole")
        page = self.open("dashboard")
        self.assertTrue(any(table._league_style == "premier" for table in page.findChildren(DataTable)))
        page = self.open("history")
        self.assertEqual({table._league_style for table in page.findChildren(DataTable) if table._league_style}, {"premier", "second"})
        page = self.open("competition", competition_id="aureole")
        self.assertFalse(any(table._league_style for table in page.findChildren(DataTable)))

    def test_zone_exact_boundaries(self):
        self.assertEqual([i for i in range(1, 21) if macos.standings_band("premier", i)[0]], [18, 19, 20])
        self.assertEqual([i for i in range(1, 21) if macos.standings_band("second", i)[0] == t.SUCCESS_BG], [1, 2])
        self.assertEqual([i for i in range(1, 21) if macos.standings_band("second", i)[0] == t.WARNING_BG], [3, 4, 5, 6])

    def test_home_away_draw_penalty_and_aggregate_outcomes(self):
        dto = {"status": "completed", "home_team_id": "h", "away_team_id": "a", "total_home": 2, "total_away": 1, "home_pk": None, "away_pk": None, "winner_team_id": "a"}
        self.assertEqual(macos.team_result(dto, "h")[0], "win")
        self.assertEqual(macos.team_result(dto, "a")[0], "loss")
        dto.update(total_away=2)
        self.assertEqual(macos.team_result(dto, "a")[0], "draw")
        dto.update(home_pk=4, away_pk=5)
        self.assertEqual(macos.team_result(dto, "a")[:2], ("win", "点球胜"))
        dto.update(total_home=3)
        self.assertEqual(macos.team_result(dto, "h")[0], "win")
        dto.update(status="scheduled")
        self.assertIsNone(macos.team_result(dto, "h"))

    def test_recent_results_colored_but_upcoming_neutral(self):
        team = next(iter(self.world["teams"]))
        page = self.open("team", team_id=team)
        page.set_detail_section(team, "fixtures")
        cards = page.findChildren(MatchRowCard)
        fixtures = sorted((m for m in self.world["season"]["fixtures"] if team in (m["home_team_id"], m["away_team_id"]) and m["status"] == "completed"), key=lambda m: (int(m["week"]), m["match_id"]), reverse=True)[:5]
        recent = [queries.fixture_dto(self.world, m) for m in fixtures]
        self.assertEqual([c.match_id for c in cards if c.property("teamResult")], [m["match_id"] for m in fixtures])
        self.assertEqual(len([c for c in cards if c.property("teamResult")]), len(recent))
        for dto in recent:
            card = next(c for c in cards if c.match_id == dto["match_id"])
            self.assertEqual(card.property("teamResult"), macos.team_result(dto, team)[0])
        mid = cards[0].match_id
        QTest.mouseClick(cards[0], Qt.LeftButton)
        QTest.qWait(40)
        self.assertEqual(self.window._current_route, ("match", {"match_id": mid}))

    def test_global_search_ignores_default_players(self):
        default_id, default = next((pid, p) for pid, p in self.world["players"].items() if not p["is_real"])
        world = {**self.world, "players": {**self.world["players"], default_id: {**default, "name": "VirtualSearchTest"}}}
        self.window.search.setText("VirtualSearchTest")
        with patch.object(self.window, "world", return_value=world):
            self.window._search_activated()
        self.assertIn("没有找到", self.window.error_label.text())
        real_id, real = next((pid, p) for pid, p in self.world["players"].items() if p["is_real"] and p.get("name"))
        self.window.search.setText(real["name"])
        self.window._search_activated()
        self.assertEqual(self.window._current_route, ("player", {"player_id": real_id}))
        self.window.search.clear()


def tearDownModule():
    support.cleanup_qt_widgets()
