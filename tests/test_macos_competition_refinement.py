# -*- coding: utf-8 -*-
"""CUI-010..090 regressions: weekly competitions, crests, zones, dual boards,
league history, Swiss graph and double-elimination panorama.

Data assertions compare against independent oracles (raw fixtures / archive
rows), not against the functions under test. UI assertions use the real page.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QTabBar

from tests import support

from football_simulator.app import queries
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.game import rules
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.competition_boards import DoubleElimPanorama, DualLeaderboard, GraphPanel, SwissBoard
from football_simulator.ui.components import DataTable
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.presentation import bracket as bracket_view
from football_simulator.ui.presentation import swiss as swiss_view

FIXTURES = support.PROJECT_ROOT / "tests" / "fixtures_vnext"


class RefinementBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_cui_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        cls.service = GameService()
        store.import_package(FIXTURES / "s3-finalized.fsarchive", "final")
        store.import_package(FIXTURES / "s2-week25.fsarchive", "winter")
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
        support.assert_cleanup_path_is_temporary(cls._tmp)
        import shutil

        shutil.rmtree(cls._tmp, ignore_errors=True)

    def open(self, route, **params):
        self.window.navigate(route, **params)
        QTest.qWait(160)
        self.assertFalse(self.window.error_label.text(), self.window.error_label.text())
        return self.window.stack.currentWidget()

    def section_tabs(self, page):
        return [tab for tab in page.findChildren(QTabBar) if tab.objectName() == "DetailSections"]

    def click_tab(self, tab, text):
        index = next(i for i in range(tab.count()) if tab.tabText(i) == text)
        QTest.mouseClick(tab, Qt.LeftButton, pos=tab.tabRect(index).center())
        QTest.qWait(180)


class WeeklyCompetitionsTests(RefinementBase):
    def test_r01_mixed_week_matches_raw_fixture_oracle(self) -> None:
        # Find a week that really contains more than one competition.
        fixtures = self.world["season"]["fixtures"]
        weeks = {}
        for fixture in fixtures:
            weeks.setdefault(int(fixture["week"]), set()).add(fixture["competition"])
        mixed = next((week for week, comps in sorted(weeks.items()) if len(comps) > 1), None)
        self.assertIsNotNone(mixed, "测试夹具应包含混合周")
        expected = {}
        for fixture in fixtures:
            if int(fixture["week"]) != mixed:
                continue
            entry = expected.setdefault(fixture["competition"], {"total": 0, "completed": 0})
            entry["total"] += 1
            entry["completed"] += fixture["status"] == "completed"
        rows = queries.week_competitions(self.world, mixed)
        self.assertEqual({row["competition_id"]: row["total"] for row in rows}, {cid: item["total"] for cid, item in expected.items()})
        for row in rows:
            self.assertEqual(row["scheduled"], row["total"] - expected[row["competition_id"]]["completed"])
        # A cup present in the raw fixtures must not be missing from the page.
        cups = [row["competition_id"] for row in rows if row["competition_id"] in rules.CUP_IDS]
        if cups:
            self.assertTrue(all(row["stages"] for row in rows if row["competition_id"] in cups))

    def test_r02_empty_week_and_matches_filter_application(self) -> None:
        # s2 is at the winter window: no official fixtures in week 25.
        self.window.open_save("winter")
        QTest.qWait(160)
        winter = store.load_world("winter")
        self.assertEqual(queries.week_competitions(winter, int(winter["season"]["week"])), [])
        page = self.open("dashboard")
        self.assertTrue(any("本周赛事" in label.text() for label in page.findChildren(QLabel)))
        self.window.open_save("final")
        QTest.qWait(150)
        page = self.open("matches", competition="aureole", week=9)
        self.assertEqual(page.filters["competition"], "aureole")
        self.assertEqual(page.filters["week"], "9")
        rows = queries.matches_page(self.world, "final", competition="aureole", week=9)
        self.assertEqual(page.table.rowCount(), len(rows["rows"]))
        for row in page.table._rows:
            self.assertEqual(row["competition"], "aureole")
            self.assertEqual(int(row["week"]), 9)


class LeagueTableTests(RefinementBase):
    def test_r03_current_league_table_has_crest_and_band(self) -> None:
        page = self.open("competition", competition_id=rules.PREMIER)
        table = page.findChild(DataTable)
        self.assertEqual(len(table._rows), 20)
        for row in table._rows:
            self.assertTrue(row["club"], row["team_id"])
        team_column = next(i for i, column in enumerate(table._columns) if column["key"] == "team")
        for index in range(table.rowCount()):
            item = table.item(index, team_column)
            self.assertIsNotNone(item)
            self.assertFalse(item.icon().isNull(), f"row {index} 缺少队徽")
        self.assertEqual(table._league_style, "premier")

    def test_r03_history_table_reads_archive_not_current(self) -> None:
        archives = sorted(self.world["archives"], key=lambda item: item["season_number"])
        second = archives[0]
        history = queries.archived_league_table(self.world, rules.PREMIER, second["season_number"])
        self.assertFalse(history["empty"])
        self.assertEqual(len(history["rows"]), 20)
        oracle = second["premier_table"]
        for row, expected in zip(history["rows"], oracle):
            for key in ("played", "wins", "draws", "losses", "goals_for", "goals_against", "goal_diff", "points"):
                self.assertEqual(int(row[key]), int(expected.get(key, 0)), (row["team_id"], key))
            self.assertEqual(row["team_id"], expected["team_id"])
            self.assertTrue(row["club"])

    def test_r08_league_sections_and_history_season_keep_state(self) -> None:
        page = self.open("competition", competition_id=rules.SECOND)
        tabs = self.section_tabs(page)
        self.assertEqual([tabs[0].tabText(i) for i in range(tabs[0].count())], ["积分榜", "赛程", "球员榜", "个人奖项", "历届冠军", "历史积分榜"])
        self.click_tab(tabs[0], "历史积分榜")
        page = self.window.stack.currentWidget()
        table = page.findChild(DataTable)
        self.assertEqual(len(table._rows), 20)
        seasons = [combo for combo in page.findChildren(QComboBox) if combo.accessibleName() == "选择历史赛季"]
        self.assertTrue(seasons)
        season_combo = seasons[0]
        first_season = season_combo.currentData()
        if season_combo.count() > 1:
            season_combo.setCurrentIndex(1)
            QTest.qWait(220)
            page = self.window.stack.currentWidget()
            table = page.findChild(DataTable)
            combo = next(combo for combo in page.findChildren(QComboBox) if combo.accessibleName() == "选择历史赛季")
            archive = next(item for item in self.world["archives"] if int(item["season_number"]) == int(combo.currentData()))
        else:
            archive = next(item for item in self.world["archives"] if int(item["season_number"]) == int(first_season))
        oracle = archive["second_table"]
        for row, expected in zip(table._rows, oracle):
            self.assertEqual(row["team_id"], expected["team_id"])
            self.assertEqual(int(row["points"]), int(expected.get("points", 0)))
        state = page.detail_state(f"{rules.SECOND}:{self.world['season_number']}")
        self.assertEqual(state["section"], "history")
        self.window.refresh_all()
        QTest.qWait(200)
        page = self.window.stack.currentWidget()
        self.assertEqual(page.detail_state(f"{rules.SECOND}:{self.world['season_number']}")["section"], "history")
        self.assertEqual(page.findChild(DataTable).rowCount(), 20)


class GroupZoneTests(RefinementBase):
    def _zone_rows(self, page):
        tables = page.findChildren(DataTable)
        return tables

    def test_r04_aureole_groups_have_two_green_and_no_path_column(self) -> None:
        page = self.open("competition", competition_id="aureole")
        tables = [table for table in page.findChildren(DataTable) if len(table._rows) == 4]
        self.assertEqual(len(tables), 4)
        for table in tables:
            keys = [column["key"] for column in table._columns]
            self.assertNotIn("advance", keys)
            self.assertEqual(len([zone for zone in table._zone_rows if zone]), 2)
            labels = {zone["label"] for zone in table._zone_rows if zone}
            self.assertEqual(labels, {"晋级八强"})
            self.assertEqual([row["rank"] for row in table._rows], [1, 2, 3, 4])

    def test_r05_ascendant_three_zones(self) -> None:
        page = self.open("competition", competition_id="ascendant")
        tables = [table for table in page.findChildren(DataTable) if len(table._rows) == 6]
        self.assertEqual(len(tables), 2)
        peak, nirvana = tables
        self.assertEqual([zone["label"] if zone else None for zone in peak._zone_rows], ["直通八强"] * 4 + ["突围赛"] * 2)
        self.assertEqual([zone["label"] if zone else None for zone in nirvana._zone_rows], ["直通八强"] + ["突围赛"] * 4 + ["淘汰"])
        self.assertNotIn("淘汰", {zone["label"] for zone in peak._zone_rows if zone})


class DualBoardTests(RefinementBase):
    def test_r06_r07_dual_boards_filter_sort_and_truncate(self) -> None:
        page = self.open("competition", competition_id="aureole")
        tabs = self.section_tabs(page)
        self.click_tab(tabs[0], "球员榜")
        page = self.window.stack.currentWidget()
        board = page.findChild(DualLeaderboard)
        self.assertIsNotNone(board)
        self.assertEqual(set(board.tables), {"goals", "assists"})
        leaders = queries.competition_leaders(self.world, "aureole")
        for key, table in board.tables.items():
            rows = table._rows
            self.assertLessEqual(len(rows), 10)
            values = [int(row[key]) for row in rows]
            self.assertEqual(values, sorted(values, reverse=True))
            self.assertTrue(all(value > 0 for value in values))
            self.assertIn(key, [column["key"] for column in table._columns])
            other = "assists" if key == "goals" else "goals"
            self.assertNotIn(other, [column["key"] for column in table._columns])
            self.assertNotIn("appearances", [column["key"] for column in table._columns])
            for row in rows:
                self.assertTrue(row["player_id"])
                self.assertTrue(row["team_label"])
        # Filter-before-truncate: the 11th player must not displace a positive one.
        all_goals = [row for row in queries.competition_players(self.world, "aureole") if row["goals"] > 0]
        self.assertEqual(len(leaders["goals"]), min(10, len(all_goals)))


class SwissGraphTests(RefinementBase):
    def test_r09_pre_round_records_match_independent_oracle(self) -> None:
        model = swiss_view.swiss_graph(self.world)
        self.assertIsNotNone(model)
        # Independent oracle: walk saved Swiss rounds in order, counting before each.
        fixtures = [f for f in self.world["season"]["fixtures"] if f["competition"] == "starpath" and str(f.get("stage", "")).startswith("swiss_")]
        by_round = {}
        for fixture in fixtures:
            by_round.setdefault(int(str(fixture["stage"]).split("_")[1]), []).append(fixture)
        records = {}
        for round_index in sorted(by_round):
            expected = {}
            for fixture in by_round[round_index]:
                for team_id in (fixture["home_team_id"], fixture["away_team_id"]):
                    expected[team_id] = records.get(team_id, (0, 0))
            round_data = next(item for item in model["rounds"] if item["round"] == round_index)
            for bucket in round_data["buckets"]:
                for node in bucket["nodes"]:
                    self.assertEqual(bucket["record"], expected[node["home_id"]], (round_index, node["match_id"]))
                    self.assertEqual(bucket["record"], expected[node["away_id"]], (round_index, node["match_id"]))
                    self.assertEqual((node["home"], node["away"]), (self.world["teams"][node["home_id"]]["club_en"], self.world["teams"][node["away_id"]]["club_en"]))
                    self.assertIn(node["status"], ("completed", "scheduled"))
                    if node["status"] == "completed":
                        self.assertIsNotNone(node["winner_id"])
                    else:
                        self.assertIsNone(node["winner_id"])
                        self.assertEqual(node["score_text"], "vs")
            for fixture in by_round[round_index]:
                if fixture.get("winner_team_id"):
                    winner = fixture["winner_team_id"]
                    loser = next(team for team in (fixture["home_team_id"], fixture["away_team_id"]) if team != winner)
                    wins, losses = records.get(winner, (0, 0))
                    records[winner] = (wins + 1, losses)
                    wins, losses = records.get(loser, (0, 0))
                    records[loser] = (wins, losses + 1)
        self.assertEqual(len(model["qualified"]), 8)
        self.assertEqual(len(model["eliminated"]), 8)
        self.assertEqual(len(model["rounds"]), 5)

    def test_r10_toggle_and_graph_nodes(self) -> None:
        page = self.open("competition", competition_id="starpath")
        tabs = self.section_tabs(page)
        swiss_tabs = [tab for tab in tabs if any(tab.tabText(i) == "对阵图" for i in range(tab.count()))]
        self.assertTrue(swiss_tabs)
        self.click_tab(swiss_tabs[0], "对阵图")
        page = self.window.stack.currentWidget()
        board = page.findChild(SwissBoard)
        self.assertIsNotNone(board)
        model = swiss_view.swiss_graph(self.world)
        expected_nodes = sum(len(bucket["nodes"]) for round_data in model["rounds"] for bucket in round_data["buckets"])
        self.assertEqual(len([node for node in board.node_ids() if not node.startswith(("qualified-", "eliminated-"))]), expected_nodes)
        self.assertEqual(len([node for node in board.node_ids() if node.startswith("qualified-")]), 8)
        self.assertEqual(len([node for node in board.node_ids() if node.startswith("eliminated-")]), 8)
        # One transition per record pool replaces the old per-club spaghetti.
        self.assertEqual(len(board._pool_blocks), 9)
        self.assertEqual(len(board._edges), 12)
        self.assertTrue(all(edge["from"].startswith("pool-") and edge["to"].startswith("pool-") for edge in board._edges))
        # Table view remains reachable and unchanged (re-fetch: the page rebuilt).
        page = self.window.stack.currentWidget()
        fresh_tabs = [tab for tab in self.section_tabs(page) if any(tab.tabText(i) == "积分表" for i in range(tab.count()))]
        self.assertTrue(fresh_tabs)
        self.click_tab(fresh_tabs[0], "积分表")
        page = self.window.stack.currentWidget()
        self.assertTrue(any(len(table._rows) == 16 for table in page.findChildren(DataTable)))


class PanoramaTests(RefinementBase):
    def _model(self):
        detail = queries.competition_detail(self.world, "final", "ember")
        return bracket_view.cup_bracket(self.world, "ember", detail)

    def test_r11_panorama_nodes_and_edges_match_model(self) -> None:
        page = self.open("competition", competition_id="ember")
        tabs = self.section_tabs(page)
        view_tabs = [tab for tab in tabs if any(tab.tabText(i) == "全景签表" for i in range(tab.count()))]
        self.assertTrue(view_tabs)
        self.click_tab(view_tabs[0], "全景签表")
        page = self.window.stack.currentWidget()
        pano = page.findChild(DoubleElimPanorama)
        self.assertIsNotNone(pano)
        model = self._model()
        ties = [node["tie_id"] for group in model["groups"] for round_data in group["rounds"] for node in round_data["nodes"]]
        self.assertEqual(len(ties), 14)
        self.assertEqual(sorted(pano.node_ids()), sorted(ties))
        self.assertEqual(len(model["edges"]), 20)
        self.assertEqual(len(pano._edges), 20)
        # Each drawn edge matches a model edge with the same endpoints.
        drawn = {(edge["from"], edge["to"]) for edge in pano._edges}
        expected = {(edge["source_tie_id"], edge["target_tie_id"]) for edge in model["edges"]}
        self.assertEqual(drawn, expected)
        gf = self.world["season"]["cups"]["ember"]["grand_final"]["tie_id"]
        self.assertIn(gf, pano.node_ids())

    def test_r14_panorama_stage_header_returns_to_round_view(self) -> None:
        page = self.open("competition", competition_id="ember")
        tabs = self.section_tabs(page)
        view_tabs = [tab for tab in tabs if any(tab.tabText(i) == "全景签表" for i in range(tab.count()))][0]
        self.click_tab(view_tabs, "全景签表")
        page = self.window.stack.currentWidget()
        pano = page.findChild(DoubleElimPanorama)
        header = next(item for item in pano._headers if item.get("stage") == "wb_semifinal")
        QTest.mouseClick(pano, Qt.LeftButton, pos=__import__("PySide6").QtCore.QPoint(int((header["x"] + 10) * pano.scale()), int((header["y"] + 10) * pano.scale())))
        QTest.qWait(220)
        page = self.window.stack.currentWidget()
        state = page.detail_state("ember")
        self.assertEqual(state["bracket_view"], "rounds")
        self.assertEqual((state["bracket_group"], state["bracket_round"]), ("wb", "wb_semifinal"))
        from football_simulator.ui.components import BracketBoard

        board = page.findChild(BracketBoard)
        self.assertIsNotNone(board)
        self.assertEqual((board.group_key, board.round_key), ("wb", "wb_semifinal"))
        self.window.refresh_all()
        QTest.qWait(220)
        page = self.window.stack.currentWidget()
        from football_simulator.ui.components import BracketBoard

        board = page.findChild(BracketBoard)
        self.assertEqual((board.group_key, board.round_key), ("wb", "wb_semifinal"))


if __name__ == "__main__":
    unittest.main()
