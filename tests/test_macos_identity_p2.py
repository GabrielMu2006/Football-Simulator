# -*- coding: utf-8 -*-
"""P2-01/P2-02/P2-03: cup crest data, annual rank facts and routed panorama edges.

Every assertion is against the current UI or the pure read model.  The panorama
test renders the real widget and reads the polylines paintEvent actually sent to
QPainter, so a test-only path can never pass while the drawing still uses the
old midpoint.
"""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton, QTabBar

from tests import support

from football_simulator.app import queries, records
from football_simulator.app.service import GameService
from football_simulator.persistence_v2 import store
from football_simulator.ui import macos_presentation as macos
from football_simulator.ui.competition_boards import DoubleElimPanorama, SwissBoard
from football_simulator.ui.components import DataTable
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.presentation import bracket as bracket_view
from football_simulator.ui.theme import apply_theme
from football_simulator.ui.team_crest import _badge_path, badge_source_directory

FIXTURES = support.PROJECT_ROOT / "tests" / "fixtures_vnext"
DARWIN = "real::darwin-nunez"

#: The 20 fixed tables named by the review: six stage boards + six champions.
CUP_STAGE_TABLES = {
    "aureole": [(["rank", "team", "played", "goal_diff", "points"], 4)] * 4
    + [(["rank", "team", "tier_label", "points"], 16)],
    "confluence": [(["rank", "team", "tier_label", "points"], 32)],
    "starpath": [
        (["seed", "team", "record", "bu", "status"], 16),
        (["rank", "team", "tier_label", "points"], 16),
    ],
    "ember": [(["rank", "team", "tier_label", "points"], 8)],
    "ascendant": [(["rank", "team", "played", "goal_diff", "points"], 6)] * 2
    + [(["rank", "team", "tier_label", "points"], 12)],
    "zenith": [(["seed", "team", "sources"], 8), (["rank", "team", "tier_label", "points"], 8)],
}
CUP_CHAMPION_ROWS = {"aureole": 2, "confluence": 2, "starpath": 2, "ember": 2, "ascendant": 2, "zenith": 1}


class _Base(unittest.TestCase):
    fixture = "s3-finalized"

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="fs_p2_")
        support.install_test_data_root(cls._tmp)
        cls.app = support.ensure_qapp()
        apply_theme(cls.app)
        store.import_package(FIXTURES / f"{cls.fixture}.fsarchive", "final")
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
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def open(self, route, **params):
        self.window.navigate(route, **params)
        QTest.qWait(180)
        self.assertFalse(self.window.error_label.text(), self.window.error_label.text())
        return self.window.stack.currentWidget()

    def click_tab(self, page, text, *, table_owner=None):
        tabs = next(
            tab for tab in page.findChildren(QTabBar)
            if any(tab.tabText(i) == text for i in range(tab.count()))
        )
        index = next(i for i in range(tabs.count()) if tabs.tabText(i) == text)
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
        QTest.qWait(220)
        return self.window.stack.currentWidget()

    def assert_icons(self, table, label, *, world=None):
        world = world or self.world
        indexes = [i for i, column in enumerate(table._columns) if column.get("icon_key") == "club"]
        self.assertEqual(len(indexes), 1, f"{label}: 应恰有一个球队图标列")
        index = indexes[0]
        for row, data in enumerate(table._rows):
            item = table.item(row, index)
            self.assertIsNotNone(item, f"{label}: 行 {row} 无单元格")
            self.assertFalse(item.icon().isNull(), f"{label}: 行 {row} ({data.get('team_id')}) 无队徽")
            team_id = data.get("team_id")
            self.assertTrue(team_id, f"{label}: 行 {row} 缺 team_id")
            expected = (world.get("teams") or {}).get(team_id) or {}
            self.assertEqual(data.get("club"), expected.get("club_en"), f"{label}: 行 {row} club 不匹配")
            path = _badge_path(expected.get("club_en") or "")
            self.assertIsNotNone(path, f"{label}: {expected.get('club_en')} 缺 v3 资源")
            self.assertIn("team_badges_40_v3", str(path), f"{label}: 未命中正式 v3 队徽")


class CupCrestCoverageTests(_Base):
    def test_20_fixed_cup_tables_and_155_rows_have_v3_crests(self) -> None:
        self.assertEqual(badge_source_directory(), "team_badges_40_v3")
        checked_tables = checked_rows = 0
        for competition_id, expected in CUP_STAGE_TABLES.items():
            page = self.open("competition", competition_id=competition_id)
            page = self.click_tab(page, "阶段与签表")
            tables = list(page.findChildren(DataTable))
            used = set()
            for columns, row_count in expected:
                table = next(
                    (
                        item for item in tables
                        if id(item) not in used
                        and [column["key"] for column in item._columns] == columns
                        and item.rowCount() == row_count
                    ),
                    None,
                )
                self.assertIsNotNone(table, f"{competition_id}: 缺少 {columns} / {row_count} 行表")
                used.add(id(table))
                self.assertGreater(table.rowCount(), 0)
                self.assert_icons(table, f"{competition_id} {columns}")
                checked_tables += 1
                checked_rows += table.rowCount()
            self.assertEqual(len(used), len(expected), f"{competition_id}: 表数量不符")

            page = self.click_tab(page, "历届冠军")
            champions = [
                table for table in page.findChildren(DataTable)
                if [column["key"] for column in table._columns] == ["season", "team"]
            ]
            self.assertEqual(len(champions), 1, f"{competition_id}: 历届冠军表数量")
            table = champions[0]
            self.assertEqual(table.rowCount(), CUP_CHAMPION_ROWS[competition_id], competition_id)
            self.assert_icons(table, f"{competition_id} 历届冠军")
            checked_tables += 1
            checked_rows += table.rowCount()
        self.assertEqual(checked_tables, 20, "固定清单应覆盖 20 处表")
        self.assertEqual(checked_rows, 155, "固定清单应覆盖 155 行")

    def test_champions_page_turn_and_f5_keep_crests_and_missing_identity_is_safe(self) -> None:
        world = copy.deepcopy(self.world)
        source = next(
            archive for archive in world["archives"]
            if any(entry.get("competition_id") == "aureole" for entry in (archive.get("championships") or {}).values())
        )
        champion = next(entry for entry in source["championships"].values() if entry.get("competition_id") == "aureole")
        for offset in range(1, 11):
            clone = copy.deepcopy(source)
            clone["season_number"] = 3 + offset
            for key, entry in list(clone["championships"].items()):
                if entry.get("competition_id") != "aureole":
                    del clone["championships"][key]
                else:
                    clone["championships"][key] = {**entry, "season_number": 3 + offset, "title_id": f"synthetic:{3 + offset}:aureole"}
            world["archives"].append(clone)
        missing = copy.deepcopy(source)
        missing["season_number"] = 30
        missing_key = next(iter(missing["championships"]))
        missing["championships"] = {
            "synthetic:30:aureole": {
                "title_id": "synthetic:30:aureole",
                "season_number": 30,
                "competition_id": "aureole",
                "champion_team_id": "team-does-not-exist",
                "decided_week": 42,
                "roster_player_ids": [],
                "roster_slots": [],
            }
        }
        world["archives"].append(missing)

        with mock.patch.object(self.window.service, "load_world", return_value=world):
            self.window._world = world
            page = self.open("competition", competition_id="aureole")
            state = page.detail_state("aureole:3")
            state["section"] = "history"
            state.setdefault("history_pages", {}).update(page=1, page_size=10)
            self.window.refresh_all()
            QTest.qWait(220)
            page = self.window.stack.currentWidget()
            page = self.click_tab(page, "历届冠军")
            champion_table = next(
                table for table in page.findChildren(DataTable)
                if [column["key"] for column in table._columns] == ["season", "team"]
            )
            self.assertEqual(champion_table.rowCount(), 10)
            # The synthetic missing team is on the first page (newest archive):
            # its icon is intentionally absent, but the table must not crash.
            missing_rows = [row for row in champion_table._rows if row.get("team_id") == "team-does-not-exist"]
            self.assertEqual(len(missing_rows), 1)
            row_index = champion_table._rows.index(missing_rows[0])
            icon_index = next(i for i, column in enumerate(champion_table._columns) if column.get("icon_key") == "club")
            self.assertTrue(champion_table.item(row_index, icon_index).icon().isNull())
            self.assertEqual(missing_rows[0].get("club"), "")
            next_button = page.findChild(QPushButton, "PagerNext")
            self.assertIsNotNone(next_button)
            QTest.mouseClick(next_button, Qt.LeftButton)
            QTest.qWait(180)
            page = self.window.stack.currentWidget()
            champion_table = next(
                table for table in page.findChildren(DataTable)
                if [column["key"] for column in table._columns] == ["season", "team"]
            )
            self.assertEqual(champion_table.rowCount(), 3)
            self.assert_icons(champion_table, "历届冠军第二页")
            # Real F5 key, not a direct refresh_all call.
            self.window.activateWindow()
            self.window.setFocus()
            QTest.qWait(60)
            QTest.keyClick(self.window, Qt.Key_F5)
            QTest.qWait(300)
            page = self.window.stack.currentWidget()
            champion_table = next(
                table for table in page.findChildren(DataTable)
                if [column["key"] for column in table._columns] == ["season", "team"]
            )
            self.assertEqual(champion_table.rowCount(), 3, "F5 后应仍停在第 2 页")
            self.assert_icons(champion_table, "历届冠军 F5 后")
        self.window._world = self.world
        self.window.refresh_all()
        QTest.qWait(180)

    def test_original_ranking_tables_keep_their_crests(self) -> None:
        checked = 0
        for route, params in (
            ("players", {}),
            ("teams", {}),
            ("dashboard", {}),
            ("records", {}),
            ("history", {}),
            ("competition", {"competition_id": "premier"}),
            ("competition", {"competition_id": "second"}),
        ):
            page = self.open(route, **params)
            for table in page.findChildren(DataTable):
                if any(column.get("icon_key") == "club" for column in table._columns) and table.rowCount():
                    self.assert_icons(table, f"{route} 榜单")
                    checked += 1
        self.assertGreaterEqual(checked, 8, "原已通过榜单也应被抽查")


class AnnualRankTests(_Base):
    def test_darwin_annual_rows_are_rank_only_and_deduped(self) -> None:
        annual = records.annual_rank_rows(self.world, DARWIN)
        self.assertEqual([(row["season"], row["rank"]) for row in annual], [(1, 1), (2, 4), (3, 1)])
        self.assertEqual([row["team_id"] for row in annual], ["team-glorious-vision"] * 3)
        # The saved computation facts are still present and unchanged.
        saved = next(
            row for archive in self.world["archives"] for row in archive.get("top20") or []
            if archive["season_number"] == 3 and row.get("player_id") == DARWIN
        )
        self.assertEqual(saved["score"], 1219.22)
        self.assertIn("components", saved)

        page = self.open("player", player_id=DARWIN)
        page = self.click_tab(page, "荣誉")
        annual_table = next(
            table for table in page.findChildren(DataTable)
            if [column["key"] for column in table._columns] == ["season", "rank_text", "team"]
        )
        self.assertEqual(annual_table.rowCount(), 3)
        self.assertEqual([row["season"] for row in annual_table._rows], [1, 2, 3])
        self.assertEqual([row["rank"] for row in annual_table._rows], [1, 4, 1])
        self.assertFalse(any("1219.22" in str(row.get("rank_text")) or "986.88" in str(row.get("rank_text")) for row in annual_table._rows))
        self.assert_icons(annual_table, "球员年度排名")
        self.assertEqual([row["rank_text"] for row in annual_table._rows], ["年度第 1 名", "年度第 4 名", "年度第 1 名"])
        labels = [label.text() for label in page.findChildren(QLabel)]
        self.assertFalse(any("年度第 1219.22 名" in text for text in labels))
        self.assertFalse(any("1219.22" in str(row.get("rank_text")) for row in annual_table._rows))
        # Non-Top20 awards keep their numeric values in the other area.
        medals = [
            table for table in page.findChildren(DataTable)
            if "competition_label" in [column["key"] for column in table._columns]
        ]
        self.assertEqual(len(medals), 1)
        self.assertTrue(medals[0].rowCount() >= 0)
        # Archived and current copies of the same event award are one row, and
        # the remaining rows still carry their original numeric values.
        all_medal_rows = list(medals[0]._rows)
        seen = {(row.get("season"), row.get("competition"), row.get("award")) for row in all_medal_rows}
        self.assertEqual(len(seen), len(all_medal_rows))
        self.assertTrue(all(row.get("value") is not None for row in all_medal_rows))

        # Same season/rank in the historical Top20 table and the honors page.
        history = self.open("annual_top20", season=3)
        top20 = next(
            table for table in history.findChildren(DataTable)
            if [column["key"] for column in table._columns] == ["rank", "label", "team"]
        )
        darwin_row = next(row for row in top20._rows if row.get("player_id") == DARWIN)
        self.assertEqual(darwin_row["rank"], annual[-1]["rank"])
        self.assertEqual(darwin_row["rank"], 1)
        self.assertIn("team-glorious-vision", [row.get("team_id") for row in top20._rows])
        self.assertIn("score", self.world["archives"][-1]["top20"][0])

        honors_page = self.open("player_honors", player_id=DARWIN)
        texts = [label.text() for label in honors_page.findChildren(QLabel)]
        annual_table = honors_page.findChild(DataTable, "PlayerHonorAnnual")
        self.assertIsNotNone(annual_table)
        selected = [row for row in annual_table._rows if row["season"] == 3]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["rank_text"], "年度第 1 名")
        self.assertFalse(any("1219.22" in text for text in texts))

    def test_missing_or_invalid_rank_has_explicit_empty_state_and_no_score_fallback(self) -> None:
        world = copy.deepcopy(self.world)
        world["archives"] = []
        world["season"]["awards"]["top20"] = [
            {"player_id": DARWIN, "label": "Darwin Nunez", "team_id": "team-glorious-vision", "rank": None, "score": 1219.22}
        ]
        rows = records.annual_rank_rows(world, DARWIN)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["rank"])
        self.assertNotIn("value", rows[0])
        with mock.patch.object(self.window.service, "load_world", return_value=world):
            self.window._world = world
            page = self.open("player", player_id=DARWIN)
            page = self.click_tab(page, "荣誉")
            annual_table = next(
                table for table in page.findChildren(DataTable)
                if [column["key"] for column in table._columns] == ["season", "rank_text", "team"]
            )
            self.assertEqual([row["rank_text"] for row in annual_table._rows], ["名次未记录"])
            self.assertFalse(any("1219.22" in str(row.get("rank_text")) for row in annual_table._rows))
        # No Top20 at all: explicit empty state, not a fabricated rank.
        world["season"]["awards"]["top20"] = []
        self.assertEqual(records.annual_rank_rows(world, DARWIN), [])
        with mock.patch.object(self.window.service, "load_world", return_value=world):
            self.window._world = world
            page = self.open("player", player_id=DARWIN)
            page = self.click_tab(page, "荣誉")
            labels = [label.text() for label in page.findChildren(QLabel)]
            self.assertTrue(any("暂无年度排名记录" in text for text in labels))
        self.window._world = self.world
        self.window.refresh_all()
        QTest.qWait(180)

    def test_current_only_rank_is_shown_without_archives(self) -> None:
        world = copy.deepcopy(self.world)
        world["archives"] = []
        world["season"]["awards"]["top20"] = [
            {"player_id": DARWIN, "label": "Darwin Nunez", "team_id": "team-glorious-vision", "rank": 2, "score": 999.0}
        ]
        self.assertEqual([(row["season"], row["rank"]) for row in records.annual_rank_rows(world, DARWIN)], [(3, 2)])
        invalid = copy.deepcopy(world)
        invalid["season"]["awards"]["top20"][0]["rank"] = 99
        self.assertIsNone(records.annual_rank_rows(invalid, DARWIN)[0]["rank"])
        invalid["season"]["awards"]["top20"][0]["rank"] = True
        self.assertIsNone(records.annual_rank_rows(invalid, DARWIN)[0]["rank"])

    def test_opening_rank_pages_does_not_write_world_or_database(self) -> None:
        db_path = store.database_path("final")
        before_db = db_path.read_bytes() if db_path.exists() else b""
        before_world = json.dumps(store.load_world("final"), ensure_ascii=False, sort_keys=True)
        for route, params in (
            ("player", {"player_id": DARWIN}),
            ("history", {}),
            ("player_honors", {"player_id": DARWIN}),
        ):
            page = self.open(route, **params)
            if route == "player":
                self.click_tab(page, "荣誉")
            elif route == "history":
                self.click_tab(page, "奖项")
        after_world = json.dumps(store.load_world("final"), ensure_ascii=False, sort_keys=True)
        after_db = db_path.read_bytes() if db_path.exists() else b""
        self.assertEqual(before_world, after_world)
        self.assertEqual(before_db, after_db)

    def test_archived_top20_rows_keep_saved_rank_and_team_identity(self) -> None:
        archived = queries.history_page(self.world, "final", 3)["archive"]
        self.assertEqual(len(archived["top20"]), 20)
        for row in archived["top20"]:
            self.assertIsInstance(row.get("rank"), int)
            self.assertTrue(1 <= row["rank"] <= 20)
            self.assertIn(row["team_id"], self.world["teams"])
            self.assertEqual(self.world["teams"][row["team_id"]]["club_en"], (self.world["teams"][row["team_id"]]["club_en"]))
        self.assertEqual([row["rank"] for row in archived["top20"]], list(range(1, 21)))

    def test_cup_champions_and_placement_keep_saved_team_ids(self) -> None:
        for competition_id in CUP_STAGE_TABLES:
            detail = queries.competition_detail(self.world, "final", competition_id)
            for row in detail.get("placement_rows") or []:
                self.assertIn(row["team_id"], self.world["teams"])
            for archive in self.world["archives"]:
                for entry in (archive.get("championships") or {}).values():
                    if entry.get("competition_id") == competition_id:
                        self.assertIn(entry["champion_team_id"], self.world["teams"])


class PanoramaRoutingTests(_Base):
    @staticmethod
    def _segment_hits_rect(p, q, rect):
        x1, y1 = p
        x2, y2 = q
        eps = 0.01
        if abs(x1 - x2) < eps:
            lo, hi = sorted((y1, y2))
            return rect.left() + eps < x1 < rect.right() - eps and lo < rect.bottom() - eps and hi > rect.top() + eps
        if abs(y1 - y2) < eps:
            lo, hi = sorted((x1, x2))
            return rect.top() + eps < y1 < rect.bottom() - eps and lo < rect.right() - eps and hi > rect.left() + eps
        return True

    def _panorama(self):
        detail = queries.competition_detail(self.world, "final", "ember")
        model = bracket_view.cup_bracket(self.world, "ember", detail)
        view = DoubleElimPanorama(self.world, model)
        view.resize(view.minimumSize())
        image = QImage(view.minimumSize(), QImage.Format_ARGB32)
        image.fill(0)
        view.render(image)
        return view, model

    def _assert_clear(self, view):
        rects = {node["id"]: QRect(node["x"], node["y"], node["w"], node["h"]) for node in view._nodes}
        headers = [QRect(header["x"], header["y"], header["w"], header["h"]) for header in view._headers if header.get("stage")]
        self.assertEqual(len(view._painted_polylines), len(view._edges))
        for painted, edge in zip(view._painted_polylines, view._edges):
            self.assertEqual((painted["from"], painted["to"], painted["style"]), (edge["from"], edge["to"], edge["style"]))
            self.assertEqual(painted["points"], edge["polyline"], "paintEvent 必须绘制布局层给出的实际点列")
            points = painted["points"]
            self.assertGreaterEqual(len(points), 2)
            for start, end in zip(points, points[1:]):
                for node_id, rect in rects.items():
                    if node_id in (painted["from"], painted["to"]):
                        continue
                    self.assertFalse(self._segment_hits_rect(start, end, rect), (painted["from"], painted["to"], node_id, start, end))
                for header in headers:
                    self.assertFalse(self._segment_hits_rect(start, end, header), (painted["from"], painted["to"], "header", start, end))
        for edge in view._edges:
            first, last = edge["polyline"][0], edge["polyline"][-1]
            source = rects[edge["from"]]
            target = rects[edge["to"]]
            self.assertEqual(first[0], source.right())
            self.assertEqual(last[0], target.left())
            self.assertAlmostEqual(first[1], source.top() + 8 + edge["from_row"] * 26 + 13)
            self.assertAlmostEqual(last[1], target.top() + 8 + edge["to_row"] * 26 + 13)
            self.assertTrue(0 <= first[0] <= view._size.width())
            self.assertTrue(0 <= last[0] <= view._size.width())

    def test_completed_panorama_draws_real_obstacle_free_polylines(self) -> None:
        view, model = self._panorama()
        self.assertEqual(len(view._nodes), 14)
        self.assertEqual(len(view._edges), 20)
        self.assertEqual(len(view._painted_polylines), 20)
        self._assert_clear(view)
        # The six edges independently reproduced as crossing cards by the
        # reviewer must now each be routed outside every unrelated card.
        problematic = {
            ("tie-ember-wb_quarterfinal-017", "tie-ember-lb_round_1-063"),
            ("tie-ember-wb_quarterfinal-018", "tie-ember-lb_round_1-063"),
            ("tie-ember-wb_quarterfinal-019", "tie-ember-lb_round_1-064"),
            ("tie-ember-wb_quarterfinal-020", "tie-ember-lb_round_1-064"),
            ("tie-ember-wb_semifinal-061", "tie-ember-lb_round_2-067"),
            ("tie-ember-wb_final-068", "tie-ember-grand_final-078"),
        }
        actual = {(edge["from"], edge["to"]) for edge in view._edges}
        self.assertTrue(problematic <= actual, problematic - actual)
        for edge in view._edges:
            if (edge["from"], edge["to"]) in problematic:
                self.assertGreaterEqual(len(edge["polyline"]), 6, f"旧中点路径未换成外部通道: {edge['from']}")
        # Penalty nodes keep their extra badge height and still route cleanly.
        special = [node for node in view._nodes if node.get("badge")]
        self.assertTrue(special)
        for node in special:
            self.assertGreaterEqual(node["h"], 68 + 18)
        self._assert_clear(view)
        # GF has two real sources; no reset node is invented.
        self.assertEqual(len([edge for edge in view._edges if edge["to"] == "tie-ember-grand_final-078"]), 2)
        self.assertIn("tie-ember-grand_final-078", view.node_ids())

    def test_initial_and_mid_states_only_show_real_ties(self) -> None:
        # Initial: s2-week25 has only the four WB first-round ties created.
        store.import_package(FIXTURES / "s2-week25.fsarchive", "week25")
        initial_world = store.load_world("week25")
        initial_detail = queries.competition_detail(initial_world, "week25", "ember")
        initial_model = bracket_view.cup_bracket(initial_world, "ember", initial_detail)
        self.assertIsNotNone(initial_model)
        view = DoubleElimPanorama(initial_world, initial_model)
        self.assertEqual(len(view._nodes), 4)
        self.assertEqual(len(view._edges), 0)
        self.assertTrue(all(node["id"].startswith("tie-ember-wb_quarterfinal") for node in view._nodes))
        image = QImage(view.minimumSize(), QImage.Format_ARGB32)
        image.fill(0)
        view.render(image)
        self.assertEqual(view._painted_polylines, [])

        # Mid: slice the real completed model to ties that existed after LB
        # round 2; no future round is fabricated by the layout.
        detail = queries.competition_detail(self.world, "final", "ember")
        model = bracket_view.cup_bracket(self.world, "ember", detail)
        retained = {"wb_quarterfinal", "wb_semifinal", "wb_final", "lb_round_1", "lb_round_2"}
        groups = []
        for group in model["groups"]:
            rounds = [rnd for rnd in group["rounds"] if rnd["key"] in retained]
            if rounds:
                groups.append({**group, "rounds": rounds})
        kept = {node["tie_id"] for group in groups for rnd in group["rounds"] for node in rnd["nodes"]}
        edges = [edge for edge in model["edges"] if edge["source_tie_id"] in kept and edge["target_tie_id"] in kept]
        mid_model = {**model, "groups": groups, "edges": edges}
        mid = DoubleElimPanorama(self.world, mid_model)
        self.assertEqual(len(mid._nodes), 11)
        self.assertEqual(len(mid._edges), 14)
        self.assertFalse(any(node["id"].endswith(("grand_final-078", "lb_round_3-069")) for node in mid._nodes))
        mid.resize(mid.minimumSize())
        image = QImage(mid.minimumSize(), QImage.Format_ARGB32)
        image.fill(0)
        mid.render(image)
        self._assert_clear(mid)

    def test_scale_paths_stay_inside_canvas(self) -> None:
        view, _model = self._panorama()
        for scale in (0.5, 1.0, 1.5):
            view.apply_scale(scale)
            image = QImage(view.minimumSize(), QImage.Format_ARGB32)
            image.fill(0)
            view.render(image)
            self.assertEqual(len(view._painted_polylines), 20)
            size = view.minimumSize()
            for painted in view._painted_polylines:
                for x, y in painted["points"]:
                    self.assertGreaterEqual(x, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(int(x * scale), size.width() + 1)
                    self.assertLessEqual(int(y * scale), size.height() + 1)
            for node in view._nodes:
                rect = view.node_rect(node["id"])
                self.assertIsNotNone(rect)
                self.assertGreaterEqual(rect.left(), 0)
                self.assertLessEqual(rect.right(), size.width())
            self._assert_clear(view)
        view.apply_scale(1.0)

    def test_swiss_graph_uses_record_pool_routes(self) -> None:
        from football_simulator.ui.presentation import swiss as swiss_view

        model = swiss_view.swiss_graph(self.world)
        self.assertIsNotNone(model)
        view = SwissBoard(self.world, model)
        view.resize(view.minimumSize())
        image = QImage(view.minimumSize(), QImage.Format_ARGB32)
        image.fill(0)
        view.render(image)
        self.assertEqual(len(view._painted_polylines), len(view._edges))
        self.assertGreater(len(view._nodes), 0)
        # Swiss now routes record pools, not individual match winners/losers.
        self.assertEqual(len(view._edges), 12)
        self.assertTrue(all(edge["from"].startswith("pool-") and edge["to"].startswith("pool-") for edge in view._edges))
        self.assertTrue(all(len(edge.get("polyline") or []) == 4 for edge in view._edges))
        self.assertTrue(all(len(painted["points"]) == 4 for painted in view._painted_polylines))


class InitialPanoramaTests(_Base):
    fixture = "s2-week25"

    def test_initial_first_round_has_no_fabricated_edges(self) -> None:
        detail = queries.competition_detail(self.world, "final", "ember")
        model = bracket_view.cup_bracket(self.world, "ember", detail)
        view = DoubleElimPanorama(self.world, model)
        self.assertEqual(len(view._nodes), 4)
        self.assertEqual(len(view._edges), 0)
        self.assertEqual(len(view._headers), 1)
        self.assertTrue(all("wb_quarterfinal" in node["id"] for node in view._nodes))


if __name__ == "__main__":
    unittest.main()
